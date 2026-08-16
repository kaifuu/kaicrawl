# -*- coding: utf-8 -*-
"""运行日志：展示抓取执行记录（含失败原因），支持来源/状态筛选、分页、清空；
日志详情页含终端式实时滚动窗口（轮询逐条日志行）；列表可浏览/下载本次抓取生成的文件。"""
import io
import os
import sys
import subprocess
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, send_file)

from ..extensions import db
from ..models import Article, CrawlLog, CrawlLogLine, Source
from config import OUTPUT_DIR

bp = Blueprint("logs", __name__)

_STATUS_LABELS = {"success": "成功", "error": "失败", "running": "运行中", "stopped": "已停止"}
PER_PAGE_OPTIONS = [10, 20, 50, 100]
FILES_PER_PAGE_OPTIONS = [10, 20, 50]


@bp.route("/")
def index():
    source_id = request.args.get("source_id", type=int)
    status = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in PER_PAGE_OPTIONS:
        per_page = 20

    query = CrawlLog.query
    if source_id:
        query = query.filter_by(source_id=source_id)
    if status in _STATUS_LABELS:
        query = query.filter_by(status=status)
    if q:
        like = f"%{q}%"
        query = (query.outerjoin(Source, CrawlLog.source_id == Source.id)
                 .filter(db.or_(CrawlLog.message.ilike(like),
                                Source.name.ilike(like),
                                Source.category.ilike(like))))
    pagination = query.order_by(CrawlLog.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False)

    sources = Source.query.order_by(Source.category, Source.name).all()
    src_map = {s.id: s for s in sources}
    for log in pagination.items:
        log._source = src_map.get(log.source_id)
    return render_template("logs.html", logs=pagination.items, sources=sources,
                           source_id=source_id or 0, status=status, q=q, per_page=per_page,
                           per_page_options=PER_PAGE_OPTIONS, pagination=pagination)


@bp.route("/<int:log_id>")
def detail(log_id):
    """日志详情：头部运行统计 + 终端式实时日志。"""
    log = db.session.get(CrawlLog, log_id)
    if not log:
        abort(404)
    source = db.session.get(Source, log.source_id) if log.source_id else None
    return render_template("log_detail.html", log=log, source=source)


@bp.route("/<int:log_id>/lines")
def lines(log_id):
    """轮询接口：返回 after 之后的新日志行 + 头部统计字段（供详情页增量刷新）。"""
    log = db.session.get(CrawlLog, log_id)
    if not log:
        return jsonify({"error": "not found"}), 404
    after = request.args.get("after", 0, type=int) or 0
    rows = (CrawlLogLine.query.filter_by(log_id=log_id)
            .filter(CrawlLogLine.seq > after)
            .order_by(CrawlLogLine.seq).all())
    end = log.finished_at or datetime.now()
    started = log.started_at or end
    return jsonify({
        "log_id": log.id,
        "status": log.status,
        "finished": log.status != "running",
        "finished_at": log.finished_at.strftime("%Y-%m-%d %H:%M:%S") if log.finished_at else None,
        "new_count": log.new_count,
        "total_count": log.total_count,
        "elapsed_seconds": int((end - started).total_seconds()),
        "message": log.message or "",
        "last_seq": rows[-1].seq if rows else after,
        "lines": [{"seq": r.seq,
                   "ts": r.ts.strftime("%H:%M:%S") if r.ts else "",
                   "level": r.level,
                   "text": r.text} for r in rows],
    })


@bp.route("/<int:log_id>/download")
def download(log_id):
    """整段运行日志导出为 txt：头部信息块 + 全量逐条日志行。"""
    log = db.session.get(CrawlLog, log_id)
    if not log:
        abort(404)
    source = db.session.get(Source, log.source_id) if log.source_id else None
    rows = (CrawlLogLine.query.filter_by(log_id=log_id)
            .order_by(CrawlLogLine.seq).all())
    end = log.finished_at or datetime.now()
    started = log.started_at or end
    head = [
        f"运行日志 #{log.id}",
        f"来源：[{source.category}] {source.name}" if source else "来源：—",
        f"触发：{'定时任务' if log.task_id else '手动'}",
        f"状态：{_STATUS_LABELS.get(log.status, log.status)}",
        f"开始：{log.started_at.strftime('%Y-%m-%d %H:%M:%S') if log.started_at else '—'}",
        f"结束：{log.finished_at.strftime('%Y-%m-%d %H:%M:%S') if log.finished_at else '—'}",
        f"耗时：{int((end - started).total_seconds())}s",
        f"新增/总数：{log.new_count}/{log.total_count}",
        "汇总：" + (log.message or "无"),
        "=" * 60,
    ]
    body = [f"[{r.ts.strftime('%H:%M:%S') if r.ts else ''}] [{r.level}] {r.text}" for r in rows]
    text = "\n".join(head + body) + "\n"
    return send_file(io.BytesIO(text.encode("utf-8")), as_attachment=True,
                     download_name=f"run-{log.id}.log", mimetype="text/plain")


@bp.route("/<int:log_id>/files")
def files(log_id):
    """本次抓取生成的文件：按抓取时间窗（started_at ~ finished_at/当前）查该来源
    落库的文章，分页返回。覆盖/修复的记录会重写 crawled_at，故时间窗即「本次产物」。"""
    log = db.session.get(CrawlLog, log_id)
    if not log:
        return jsonify({"error": "not found"}), 404
    page = request.args.get("page", 1, type=int) or 1
    per_page = request.args.get("per_page", 10, type=int)
    if per_page not in FILES_PER_PAGE_OPTIONS:
        per_page = 10
    empty = {"log_id": log.id, "count": 0, "total_size": None,
             "page": 1, "pages": 0, "per_page": per_page, "files": []}
    end = log.finished_at or datetime.now()
    window = [Article.source_id == log.source_id,
              Article.crawled_at >= log.started_at,
              Article.crawled_at <= end]
    if not log.started_at:
        return jsonify(empty)

    pagination = (Article.query.filter(*window)
                  .order_by(Article.crawled_at)
                  .paginate(page=page, per_page=per_page, error_out=False))
    total_size = 0
    paths = (db.session.query(Article.docx_path)
             .filter(*window, Article.docx_path != "").all())
    for (path,) in paths:
        try:
            total_size += os.path.getsize(os.path.join(OUTPUT_DIR, path))
        except OSError:
            pass
    out = []
    for a in pagination.items:
        size = None
        if a.docx_path:
            try:
                size = os.path.getsize(os.path.join(OUTPUT_DIR, a.docx_path))
            except OSError:
                size = None
        out.append({
            "aid": a.id,
            "title": a.title or "无标题",
            "publish_date": a.publish_date or "",
            "file": os.path.basename(a.docx_path.replace("\\", "/")) if a.docx_path else "",
            "size": size,
        })
    return jsonify({"log_id": log.id, "count": pagination.total,
                    "total_size": total_size if pagination.total else None,
                    "page": pagination.page, "pages": pagination.pages,
                    "per_page": per_page, "files": out})


@bp.route("/<int:log_id>/open")
def open_folder(log_id):
    """在系统文件管理器中打开本次抓取文件所在目录；无产物时回退输出根目录。"""
    log = db.session.get(CrawlLog, log_id)
    if not log:
        return jsonify({"error": "not found"}), 404
    target = None
    if log.started_at:
        end = log.finished_at or datetime.now()
        a = (Article.query
             .filter(Article.source_id == log.source_id,
                     Article.crawled_at >= log.started_at,
                     Article.crawled_at <= end,
                     Article.docx_path != "")
             .order_by(Article.crawled_at).first())
        if a:
            rel_dir = os.path.dirname(a.docx_path.replace("\\", "/"))
            target = os.path.join(OUTPUT_DIR, rel_dir) if rel_dir else OUTPUT_DIR
    if not target or not os.path.isdir(target):
        target = OUTPUT_DIR
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/stop", methods=["POST"])
def stop():
    """停止某个正在运行的抓取任务（在当前文章处理完后生效）。"""
    log_id = request.form.get("log_id", type=int)
    log = db.session.get(CrawlLog, log_id) if log_id else None
    if log and log.status == "running":
        from .. import crawler
        crawler.request_stop(log.source_id)
        flash("已发送停止信号，任务将在当前文章处理完成后停止", "info")
    else:
        flash("该任务未在运行（可能刚结束）", "warning")
    return redirect(request.referrer or url_for("logs.index"))


@bp.route("/clear", methods=["POST"])
def clear():
    """清空运行日志：status 为空清全部，否则按状态清（success/error/running）。

    先删逐条日志行再删日志本体（同一事务；SQLite 不强制外键，须手动防孤儿行）。
    """
    status = (request.form.get("status") or "").strip()
    if status in _STATUS_LABELS:
        ids = [r[0] for r in db.session.query(CrawlLog.id).filter_by(status=status).all()]
    else:
        ids = [r[0] for r in db.session.query(CrawlLog.id).all()]
    if ids:
        CrawlLogLine.query.filter(CrawlLogLine.log_id.in_(ids)).delete(synchronize_session=False)
        CrawlLog.query.filter(CrawlLog.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
    if status in _STATUS_LABELS:
        flash(f"已清空 {len(ids)} 条「{_STATUS_LABELS[status]}」日志", "success")
    else:
        flash(f"已清空全部 {len(ids)} 条运行日志", "success")
    return redirect(url_for("logs.index"))
