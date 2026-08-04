# -*- coding: utf-8 -*-
"""运行日志：展示抓取执行记录（含失败原因），支持来源/状态筛选、分页、清空。"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from ..extensions import db
from ..models import CrawlLog, Source

bp = Blueprint("logs", __name__)

_STATUS_LABELS = {"success": "成功", "error": "失败", "running": "运行中", "stopped": "已停止"}
PER_PAGE_OPTIONS = [20, 50, 100]


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
    """清空运行日志：status 为空清全部，否则按状态清（success/error/running）。"""
    status = (request.form.get("status") or "").strip()
    if status in _STATUS_LABELS:
        n = CrawlLog.query.filter_by(status=status).delete(synchronize_session=False)
        flash(f"已清空 {n} 条「{_STATUS_LABELS[status]}」日志", "success")
    else:
        n = CrawlLog.query.delete(synchronize_session=False)
        flash(f"已清空全部 {n} 条运行日志", "success")
    db.session.commit()
    return redirect(url_for("logs.index"))
