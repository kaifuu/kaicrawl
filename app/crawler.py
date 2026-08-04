# -*- coding: utf-8 -*-
"""抓取编排：列表 -> 去重 -> 详情 -> 生成 WORD -> 落库。

run_source 假定调用方已处于 Flask application context 中（路由线程 / 调度器回调均会压入）。
同一来源并发抓取用按来源的锁互斥，避免重复。
"""
import os
import time
import logging
import threading
from datetime import datetime

from .extensions import db
from .models import Source, Article, CrawlLog, Task
from .sources import get_parser, ParserError
from . import docx_writer
from config import OUTPUT_DIR, REQUEST_DELAY

_locks = {}
_locks_guard = threading.Lock()


def _get_lock(source_id):
    with _locks_guard:
        lk = _locks.get(source_id)
        if lk is None:
            lk = threading.Lock()
            _locks[source_id] = lk
        return lk


# 手动停止信号：source_id -> True。run_source 在每篇文章处理前检查。
# 同一来源同时只有一个抓取在跑（按来源锁互斥），故用 source_id 即可定位。
_stop_flags = {}
_stop_guard = threading.Lock()


def request_stop(source_id):
    """请求停止某来源正在运行的抓取（日志页「停止」按钮触发）。"""
    with _stop_guard:
        _stop_flags[source_id] = True


def _stop_requested(source_id):
    with _stop_guard:
        return _stop_flags.get(source_id, False)


def _clear_stop(source_id):
    with _stop_guard:
        _stop_flags.pop(source_id, None)


_log = logging.getLogger(__name__)


def _remove_docx(rel_path):
    """删除 OUTPUT_DIR 下相对路径的旧 WORD 文件。文件缺失或删除失败均不抛错。"""
    if not rel_path:
        return
    abs_path = os.path.join(OUTPUT_DIR, rel_path)
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
    except OSError as e:
        _log.warning("删除旧 WORD 失败：%s -> %s", abs_path, e)


def _article_intact(article):
    """文章是否已完好归档：有 docx_path 且 WORD 文件确实存在于磁盘。

    去重的真实判据——记录在库但文件被人工删除/丢失，视为未完成归档，需重新抓取。
    """
    if not article.docx_path:
        return False
    return os.path.isfile(os.path.join(OUTPUT_DIR, article.docx_path))


def run_source(source_id, task_id=None, overwrite=False, since_date=None):
    """抓取单个数据源，返回本次的 CrawlLog。已在运行则直接返回 None。

    overwrite=True 时，对当前列表里已存在的文章先删旧记录与旧 WORD，再重新抓取生成
    （刷新内容、避免文件名 _2/_3 堆积）；默认 False 仍按 URL 跳过已有。
    since_date(YYYY-MM-DD) 非空时回溯抓取：翻页直到早于该日期（仅支持翻页的解析器）。
    """
    source = db.session.get(Source, source_id)
    if not source:
        return None

    lock = _get_lock(source_id)
    if not lock.acquire(blocking=False):
        return None  # 该来源正在运行

    _clear_stop(source.id)  # 复位可能残留的停止信号
    started = datetime.now()
    today = started.strftime("%Y-%m-%d")
    log = CrawlLog(task_id=task_id, source_id=source.id, status="running",
                   started_at=started)
    db.session.add(log)
    db.session.commit()

    new_count, total, failed, overwritten, repaired = 0, 0, 0, 0, 0
    stopped = False
    try:
        parser = get_parser(source)
        items = parser.fetch_list(since_date=since_date)
        total = len(items)

        for it in items:
            if _stop_requested(source.id):
                stopped = True
                break
            url = it["url"]
            existing = Article.query.filter_by(source_id=source.id, url=url).first()
            # 跳过条件：记录存在且文件完好。文件缺失（人工删除等）→ 重新生成。
            if existing and not overwrite and _article_intact(existing):
                continue
            need_regen = existing is not None          # 覆盖 或 文件缺失
            is_repair = need_regen and not overwrite   # 仅因文件缺失而重生成（非用户主动覆盖）
            try:
                if need_regen:
                    # 覆盖：文件可能在（删之）；修复：文件已丢（no-op）。都先删旧记录释放唯一约束。
                    _remove_docx(existing.docx_path)
                    db.session.delete(existing)
                    db.session.commit()
                detail = parser.fetch_detail(url)
                if not (detail.get("title") or "").strip():
                    detail["title"] = it.get("title") or "无标题"
                docx_path, images_dir = docx_writer.generate(source, detail, today)
                db.session.add(Article(
                    source_id=source.id,
                    title=(detail.get("title") or "无标题")[:500],
                    author=(detail.get("author") or "")[:128],
                    publish_date=(detail.get("publish_date") or "")[:32],
                    url=url,
                    docx_path=os.path.relpath(docx_path, OUTPUT_DIR).replace("\\", "/"),
                    images_dir=os.path.relpath(images_dir, OUTPUT_DIR).replace("\\", "/"),
                    status="ok",
                ))
                if is_repair:
                    repaired += 1
                elif need_regen:
                    overwritten += 1
                else:
                    new_count += 1
                db.session.commit()
            except Exception as e:
                # 单篇失败不中断整体；不落 Article 以便下次重试
                failed += 1
                log.message = (log.message or "") + f"\n失败：{it.get('title','')[:30]} -> {e}"
            time.sleep(REQUEST_DELAY)

        db.session.commit()
        processed = new_count + overwritten + repaired + failed
        if stopped:
            log.status = "stopped"
            log.message = (f"已手动停止：列表 {total} 篇，已处理 {processed} 篇"
                           f"（新增 {new_count}，覆盖 {overwritten}，修复 {repaired}，失败 {failed}）。"
                           + (log.message or ""))
        else:
            log.status = "success"
            log.message = (f"列表 {total} 篇，新增 {new_count} 篇，覆盖 {overwritten} 篇，"
                           f"修复 {repaired} 篇，失败 {failed} 篇。" + (log.message or ""))
    except ParserError as e:
        db.session.rollback()
        log.status = "error"
        log.message = str(e)
    except Exception as e:
        db.session.rollback()
        log.status = "error"
        log.message = f"未知错误：{e}"
    finally:
        log.finished_at = datetime.now()
        log.new_count = new_count
        log.total_count = total
        if task_id:
            t = db.session.get(Task, task_id)
            if t:
                t.last_run_at = started
                t.last_status = log.status
                t.last_message = (log.message or "")[:250]
        db.session.commit()
        _clear_stop(source.id)
        lock.release()
    return log


def run_task(task_id):
    """调度器回调入口：解析任务 -> 抓取其来源。"""
    t = db.session.get(Task, task_id)
    if not t or not t.enabled:
        return
    run_source(t.source_id, task_id=t.id)
