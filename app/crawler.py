# -*- coding: utf-8 -*-
"""抓取编排：列表 -> 去重 -> 详情 -> 生成 WORD -> 落库。

run_source 假定调用方已处于 Flask application context 中（路由线程 / 调度器回调均会压入）。
同一来源并发抓取用按来源的锁互斥，避免重复。
"""
import os
import time
import types
import logging
import threading
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from .extensions import db
from .models import Source, Article, CrawlLog, CrawlLogLine, Task
from .sources import get_parser, ParserError
from . import docx_writer
from config import OUTPUT_DIR, REQUEST_DELAY, DETAIL_PREFETCH

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


class _RunLogWriter:
    """逐条运行日志写入器：详情页终端实时滚动用。

    - seq 在单次运行内单调递增，前端按 after=<seq> 增量拉取；
    - 父日志被「清空日志」删掉后置 dead，后续行全部丢弃（SQLite 不强制外键，防孤儿行）；
    - 只在会话干净的时机调用 write()：其内部 commit 会顺带 flush 会话中挂起的其它变更。
    """

    def __init__(self, log_id):
        self.log_id = log_id
        self.seq = 0
        self.dead = False

    def write(self, text, level="info"):
        if self.dead:
            return
        try:
            if db.session.get(CrawlLog, self.log_id) is None:
                self.dead = True  # 日志已被清空，停止写入
                return
            self.seq += 1
            db.session.add(CrawlLogLine(log_id=self.log_id, seq=self.seq,
                                        level=level, text=str(text)[:2000]))
            db.session.commit()
        except Exception:
            db.session.rollback()
            _log.warning("运行日志行写入失败", exc_info=True)


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


def _source_snapshot(source):
    """把 Source ORM 行拷成纯值对象（types.SimpleNamespace）。

    详情预取线程会调用 parser.fetch_detail，其中读取 self.source 的属性；
    而 ORM 实例每次 commit 后过期（默认 expire_on_commit=True），过期属性的
    懒刷新会跨线程触碰主线程的 Session（greenlet 错误/竞态）——快照彻底断开
    这条跨线程通道，预取线程只读纯值。
    """
    snap = types.SimpleNamespace()
    for col in Source.__table__.columns:
        setattr(snap, col.name, getattr(source, col.name))
    return snap


def run_source(source_id, task_id=None, overwrite=False, since_date=None, limit=None,
               days_back=0):
    """抓取单个数据源，返回本次的 CrawlLog。已在运行则直接返回 None。

    overwrite=True 时，对当前列表里已存在的文章先删旧记录与旧 WORD，再重新抓取生成
    （刷新内容、避免文件名 _2/_3 堆积）；默认 False 仍按 URL 跳过已有。
    since_date(YYYY-MM-DD) 非空时回溯抓取：翻页直到早于该日期（仅支持翻页的解析器）。
    days_back（未显式指定 since_date 时生效）为任务抓取范围：1=仅当天；N>1=最近 N 天
    （含当天，起始日 = 运行日 - (N-1)）；0=不限（只抓列表首页）。列表无日期的条目仍放行。
    limit 为本次最多抓取篇数（界面输入），None 时用解析器默认上限。
    """
    source = db.session.get(Source, source_id)
    if not source:
        return None

    lock = _get_lock(source_id)
    if not lock.acquire(blocking=False):
        return None  # 该来源正在运行

    _clear_stop(source.id)  # 复位可能残留的停止信号
    started = datetime.now()
    today = started.strftime("%Y-%m-%d")   # 运行日，供 docx_writer 归档目录用
    # 范围 -> 起始日期：显式回溯优先；否则按天数（1=当天，N=运行日往前推 N-1 天）
    if since_date:
        since, range_label = since_date, since_date
    elif days_back > 0:
        since = (started - timedelta(days=days_back - 1)).strftime("%Y-%m-%d")
        range_label = "仅当天" if days_back == 1 else f"最近{days_back}天（含当天）"
    else:
        since, range_label = None, "全部"
    log = CrawlLog(task_id=task_id, source_id=source.id, status="running",
                   started_at=started)
    db.session.add(log)
    db.session.commit()
    logw = _RunLogWriter(log.id)
    logw.write(f"▶ 开始抓取 · [{source.category}] {source.name} · 触发={'定时任务' if task_id else '手动'}"
               f" · 覆盖={'是' if overwrite else '否'} · 范围={range_label}"
               f" · 上限={limit or '默认'} · 运行 #{log.id}")

    new_count, total, failed, overwritten, repaired, skipped = 0, 0, 0, 0, 0, 0
    stopped = False
    try:
        # 预取线程用的解析器以离线快照构造（断开 ORM 懒刷新的跨线程通道）
        parser = get_parser(_source_snapshot(source))
        items = parser.fetch_list(since_date=since, limit=limit)
        total = len(items)
        if total:
            logw.write(f"✓ 列表获取成功 · 共 {total} 篇待检查")
        else:
            logw.write("列表为空，无可处理文章", "warn")

        # 预扫描：一次批量查已有记录并预分类（fetch_list 各实现均按 URL 去重）。
        # 跳过条件与逐篇查询完全一致：记录存在且文件完好且非覆盖。跳过项不预取。
        existing_by_url = {}
        if items:
            urls = [it["url"] for it in items]
            existing_by_url = {r.url: r for r in Article.query.filter(
                Article.source_id == source.id, Article.url.in_(urls)).all()}
        skip_urls, work_urls = set(), []
        for it in items:
            ex = existing_by_url.get(it["url"])
            if ex and not overwrite and _article_intact(ex):
                skip_urls.add(it["url"])
            else:
                work_urls.append(it["url"])

        # 详情滑动窗口预取：预取线程只跑无状态 fetch_detail（不碰 DB/日志），
        # 主线程按列表顺序消费 future——落库、日志、删除旧记录全部留在主线程。
        W = max(1, DETAIL_PREFETCH)
        pool = ThreadPoolExecutor(max_workers=W, thread_name_prefix=f"detail-{source.id}")
        futures = deque()   # 队头恒为当前待处理项的 future（按 work_urls 顺序提交）
        wi = 0

        def _topup():
            nonlocal wi
            while len(futures) < W and wi < len(work_urls):
                futures.append(pool.submit(parser.fetch_detail, work_urls[wi]))
                wi += 1

        try:
            for idx, it in enumerate(items, 1):
                if _stop_requested(source.id):
                    logw.write(f"■ 收到停止信号，处理完当前环节后结束（已检查 {idx - 1} 篇）", "warn")
                    stopped = True
                    break
                url = it["url"]
                # 跳过条件：记录存在且文件完好。文件缺失（人工删除等）→ 重新生成。
                if url in skip_urls:
                    skipped += 1
                    logw.write(f"· [{idx}/{total}] 跳过（已归档）· {(it.get('title') or '')[:40]}", "dim")
                    continue
                existing = existing_by_url.get(url)
                need_regen = existing is not None          # 覆盖 或 文件缺失
                is_repair = need_regen and not overwrite   # 仅因文件缺失而重生成（非用户主动覆盖）
                _topup()
                fut = futures.popleft()
                try:
                    if need_regen:
                        # 覆盖：文件可能在（删之）；修复：文件已丢（no-op）。都先删旧记录释放唯一约束。
                        _remove_docx(existing.docx_path)
                        db.session.delete(existing)
                        db.session.commit()
                    detail = fut.result()
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
                    title_short = (detail.get("title") or "无标题")[:40]
                    fname = os.path.basename(docx_path)
                    if is_repair:
                        logw.write(f"修补 [{idx}/{total}] 文件缺失重抓 · {title_short} · {fname}", "warn")
                    elif need_regen:
                        logw.write(f"↻ [{idx}/{total}] 覆盖 · {title_short} · {fname}")
                    else:
                        logw.write(f"✓ [{idx}/{total}] 新增 · {title_short} · {fname}", "success")
                except Exception as e:
                    # 单篇失败不中断整体；不落 Article 以便下次重试
                    failed += 1
                    logw.write(f"✗ [{idx}/{total}] 失败 · {(it.get('title') or '')[:40]} · {e}", "error")
                    if not isinstance(e, ParserError):
                        logw.write("    ↳ " + traceback.format_exc().strip()[-400:], "error")
                    log.message = (log.message or "") + f"\n失败：{it.get('title','')[:30]} -> {e}"
                time.sleep(REQUEST_DELAY)

            db.session.commit()
        finally:
            # 停止/异常时弃置在途预取（浪费 ≤ 窗口数）；已提交的渲染照常完成，不毒化渲染线程
            pool.shutdown(wait=False, cancel_futures=True)
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
        logw.write(f"✗ 列表解析失败 · {e}", "error")
    except Exception as e:
        db.session.rollback()
        log.status = "error"
        log.message = f"未知错误：{e}"
        logw.write(f"✗ 未知错误 · {e}", "error")
        logw.write("    ↳ " + traceback.format_exc().strip()[-400:], "error")
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
        result_label = {"success": "成功", "error": "失败", "stopped": "已停止"}.get(log.status, log.status)
        cost = int((log.finished_at - started).total_seconds())
        final_level = "success" if log.status == "success" else ("error" if log.status == "error" else "warn")
        logw.write(f"■ 运行结束 · 结果={result_label} · 耗时 {cost}s · 列表 {total} · 新增 {new_count}"
                   f" · 覆盖 {overwritten} · 修复 {repaired} · 失败 {failed} · 跳过 {skipped}", final_level)
        _clear_stop(source.id)
        lock.release()
    return log


def run_task(task_id):
    """调度器回调入口：解析任务 -> 按任务配置抓取其来源。"""
    t = db.session.get(Task, task_id)
    if not t or not t.enabled:
        return
    run_source(t.source_id, task_id=t.id, days_back=t.days_back or 0)
