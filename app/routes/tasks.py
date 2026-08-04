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


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    query = Task.query
    if q:
        query = query.filter(Task.name.like(f"%{q}%"))
    pagination = query.order_by(Task.id.desc()).paginate(
        page=page, per_page=15, error_out=False)
    for t in pagination.items:
        t._next_run = scheduler_jobs.next_run_time(t.id)
    sources = Source.query.filter_by(enabled=True).order_by(Source.category, Source.name).all()
    return render_template("tasks.html", tasks=pagination.items, sources=sources,
                           q=q, pagination=pagination)


@bp.route("/new", methods=["POST"])
def new():
    source_id = request.form.get("source_id", type=int)
    run_time = request.form.get("run_time", "").strip()
    name = request.form.get("name", "").strip()

    source = db.session.get(Source, source_id) if source_id else None
    if not source:
        flash("请选择有效的数据源", "danger")
        return redirect(url_for("tasks.index"))
    if not TIME_RE.match(run_time):
        flash("时间格式应为 HH:MM（如 08:00）", "danger")
        return redirect(url_for("tasks.index"))

    if not name:
        name = f"{source.category}-{source.name} {run_time}"

    t = Task(name=name, source_id=source.id, run_time=run_time, enabled=True)
    db.session.add(t)
    db.session.commit()

    app = current_app._get_current_object()
    scheduler_jobs.sync_task(app, t)
    flash(f"已创建任务：{name}（每日 {run_time}）", "success")
    return redirect(url_for("tasks.index"))


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
    scheduler_jobs.run_now(current_app._get_current_object(), t.source_id, task_id=t.id)
    flash(f"已触发任务：{t.name}（后台执行中）", "info")
    return redirect(url_for("tasks.index"))
