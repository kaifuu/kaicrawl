# -*- coding: utf-8 -*-
"""任务管理：新建/启停/删除/立即运行。每个任务 = 一个来源 + 每日时间点。"""
import re

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app)

from ..extensions import db
from ..models import Task, Source
from .. import scheduler_jobs

bp = Blueprint("tasks", __name__)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_DAYS_BACK = 30
PER_PAGE_OPTIONS = (10, 15, 30, 50)


def _parse_days_back(raw):
    """表单的抓取范围天数：1=仅当天（默认）；N>1=最近 N 天（含当天）；0=不限。"""
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(0, min(MAX_DAYS_BACK, days))


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 15, type=int)
    if per_page not in PER_PAGE_OPTIONS:
        per_page = 15
    query = Task.query
    if q:
        query = query.filter(Task.name.like(f"%{q}%"))
    pagination = query.order_by(Task.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    for t in pagination.items:
        t._next_run = scheduler_jobs.next_run_time(t.id)
    sources = Source.query.filter_by(enabled=True).order_by(Source.category, Source.name).all()
    return render_template("tasks.html", tasks=pagination.items, sources=sources,
                           q=q, pagination=pagination,
                           per_page=per_page, per_page_options=PER_PAGE_OPTIONS)


@bp.route("/new", methods=["POST"])
def new():
    source_id = request.form.get("source_id", type=int)
    run_time = request.form.get("run_time", "").strip()
    name = request.form.get("name", "").strip()
    days_back = _parse_days_back(request.form.get("days_back"))

    source = db.session.get(Source, source_id) if source_id else None
    if not source:
        flash("请选择有效的数据源", "danger")
        return redirect(url_for("tasks.index"))
    if not TIME_RE.match(run_time):
        flash("时间格式应为 HH:MM（如 08:00）", "danger")
        return redirect(url_for("tasks.index"))

    if not name:
        name = f"{source.category}-{source.name} {run_time}"

    t = Task(name=name, source_id=source.id, run_time=run_time,
             days_back=days_back, enabled=True)
    db.session.add(t)
    db.session.commit()

    app = current_app._get_current_object()
    scheduler_jobs.sync_task(app, t)
    flash(f"已创建任务：{name}（每日 {run_time}，{t.range_label}）", "success")
    return redirect(url_for("tasks.index"))


@bp.route("/<int:tid>/edit", methods=["GET", "POST"])
def edit(tid):
    """编辑任务：名称 / 来源 / 每日时间 / 抓取范围 / 启停。保存后重排调度作业。"""
    t = db.session.get(Task, tid)
    if not t:
        flash("任务不存在", "danger")
        return redirect(url_for("tasks.index"))

    if request.method == "POST":
        source_id = request.form.get("source_id", type=int)
        run_time = request.form.get("run_time", "").strip()
        source = db.session.get(Source, source_id) if source_id else None
        if not source:
            flash("请选择有效的数据源", "danger")
            return redirect(url_for("tasks.edit", tid=t.id))
        if not TIME_RE.match(run_time):
            flash("时间格式应为 HH:MM（如 08:00）", "danger")
            return redirect(url_for("tasks.edit", tid=t.id))

        name = request.form.get("name", "").strip()
        t.name = name or f"{source.category}-{source.name} {run_time}"
        t.source_id = source.id
        t.run_time = run_time
        t.days_back = _parse_days_back(request.form.get("days_back"))
        t.enabled = request.form.get("enabled") is not None
        db.session.commit()

        scheduler_jobs.sync_task(current_app._get_current_object(), t)
        flash(f"已保存任务：{t.name}（每日 {run_time}）", "success")
        return redirect(url_for("tasks.index"))

    # 数据源下拉：默认只列启用的；任务当前指向的来源若已禁用也保留，避免保存时被悄悄换掉
    sources = Source.query.filter_by(enabled=True).order_by(Source.category, Source.name).all()
    if t.source.enabled is False and t.source not in sources:
        sources.append(t.source)
    t._next_run = scheduler_jobs.next_run_time(t.id)
    return render_template("task_form.html", task=t, sources=sources)


@bp.route("/<int:tid>/toggle", methods=["POST"])
def toggle(tid):
    t = db.session.get(Task, tid)
    if t:
        t.enabled = not t.enabled
        db.session.commit()
        scheduler_jobs.sync_task(current_app._get_current_object(), t)
        flash(f"任务已{'启用' if t.enabled else '禁用'}", "success")
    return redirect(url_for("tasks.index"))


@bp.route("/<int:tid>/delete", methods=["POST"])
def delete(tid):
    t = db.session.get(Task, tid)
    if t:
        scheduler_jobs.remove_task(t.id)
        db.session.delete(t)
        db.session.commit()
        flash("任务已删除", "success")
    return redirect(url_for("tasks.index"))


@bp.route("/<int:tid>/run", methods=["POST"])
def run(tid):
    t = db.session.get(Task, tid)
    if not t:
        flash("任务不存在", "danger")
        return redirect(url_for("tasks.index"))
    scheduler_jobs.run_now(current_app._get_current_object(), t.source_id, task_id=t.id,
                           days_back=t.days_back or 0)
    flash(f"已触发任务：{t.name}（后台执行中，{t.range_label}）", "info")
    return redirect(url_for("tasks.index"))
