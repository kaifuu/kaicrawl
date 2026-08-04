# -*- coding: utf-8 -*-
"""数据源管理：列表 / 新增 / 编辑 / 启停 / 删除 / 从Excel重导入 / 立即抓取。"""
import re

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app)

from sqlalchemy import or_

from ..extensions import db
from ..models import Source
from ..sources import PARSER_REGISTRY
from .. import scheduler_jobs, excel_sync
from config import EXCEL_PATH

bp = Blueprint("sources", __name__)


def _parser_options():
    """[(key, 标签)]，供表单下拉。"""
    return [(key, getattr(cls, "site_name", "") or key)
            for key, cls in PARSER_REGISTRY.items()]


def _since_from_form():
    """读取表单 since（起始日期），返回 (since_date_or_None, 错误消息_or_None)。

    合法：空（=不限日期）或 YYYY-MM-DD；非法给出错误消息。
    """
    since = (request.form.get("since") or "").strip()
    if since and not re.match(r"^\d{4}-\d{2}-\d{2}$", since):
        return None, "起始日期格式应为 YYYY-MM-DD"
    return since or None, None


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("category") or "").strip()
    query = Source.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Source.name.like(like), Source.url.like(like)))
    if cat:
        query = query.filter_by(category=cat)
    sources = query.order_by(Source.category, Source.id).all()
    grouped = {}
    for s in sources:
        grouped.setdefault(s.category, []).append(s)
    categories = sorted({c for (c,) in
                         Source.query.with_entities(Source.category).distinct() if c})
    return render_template("sources.html", grouped=grouped, sources=sources,
                           q=q, category=cat, categories=categories)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        s = Source(
            category=request.form.get("category", "").strip(),
            name=request.form.get("name", "").strip(),
            url=request.form.get("url", "").strip(),
            source_type=request.form.get("source_type", "website").strip(),
            parser_key=request.form.get("parser_key", "bjdch").strip(),
            author_policy=request.form.get("author_policy", "").strip(),
            remark=request.form.get("remark", "").strip(),
            enabled=request.form.get("enabled") == "on",
        )
        if not s.category or not s.name:
            flash("分类与名称必填", "danger")
            return render_template("source_form.html", source=s,
                                   parser_options=_parser_options())
        db.session.add(s)
        db.session.commit()
        flash("数据源已添加", "success")
        return redirect(url_for("sources.index"))
    return render_template("source_form.html", source=None,
                           parser_options=_parser_options())


@bp.route("/<int:sid>/edit", methods=["GET", "POST"])
def edit(sid):
    s = db.session.get(Source, sid)
    if not s:
        flash("数据源不存在", "danger")
        return redirect(url_for("sources.index"))
    if request.method == "POST":
        s.category = request.form.get("category", "").strip()
        s.name = request.form.get("name", "").strip()
        s.url = request.form.get("url", "").strip()
        s.source_type = request.form.get("source_type", "website").strip()
        s.parser_key = request.form.get("parser_key", "bjdch").strip()
        s.author_policy = request.form.get("author_policy", "").strip()
        s.remark = request.form.get("remark", "").strip()
        s.enabled = request.form.get("enabled") == "on"
        db.session.commit()
        flash("已保存", "success")
        return redirect(url_for("sources.index"))
    return render_template("source_form.html", source=s,
                           parser_options=_parser_options())


@bp.route("/<int:sid>/toggle", methods=["POST"])
def toggle(sid):
    s = db.session.get(Source, sid)
    if s:
        s.enabled = not s.enabled
        db.session.commit()
        flash(f"已{'启用' if s.enabled else '禁用'}：{s.name}", "success")
    return redirect(url_for("sources.index"))


@bp.route("/<int:sid>/delete", methods=["POST"])
def delete(sid):
    s = db.session.get(Source, sid)
    if s:
        # 同时移除该来源下的任务
        for t in list(s.tasks):
            scheduler_jobs.remove_task(t.id)
        db.session.delete(s)
        db.session.commit()
        flash("已删除数据源及其任务", "success")
    return redirect(url_for("sources.index"))


@bp.route("/<int:sid>/run", methods=["POST"])
def run(sid):
    """立即抓取某来源（后台线程）。可选起始日期 since（YYYY-MM-DD）回溯抓取。"""
    s = db.session.get(Source, sid)
    if not s:
        flash("数据源不存在", "danger")
        return redirect(url_for("sources.index"))
    since, err = _since_from_form()
    if err:
        flash(err, "danger")
        return redirect(url_for("sources.index"))
    scheduler_jobs.run_now(current_app._get_current_object(), s.id, since_date=since)
    flash(f"已触发抓取：{s.name}（后台执行中，请稍后查看日志）", "info")
    return redirect(url_for("sources.index"))


@bp.route("/<int:sid>/run-overwrite", methods=["POST"])
def run_overwrite(sid):
    """立即覆盖抓取某来源：对已存在的文章删旧记录+旧 WORD 后重新抓取生成。

    可选起始日期 since（YYYY-MM-DD）回溯抓取。
    """
    s = db.session.get(Source, sid)
    if not s:
        flash("数据源不存在", "danger")
        return redirect(url_for("sources.index"))
    since, err = _since_from_form()
    if err:
        flash(err, "danger")
        return redirect(url_for("sources.index"))
    scheduler_jobs.run_now(current_app._get_current_object(), s.id,
                           overwrite=True, since_date=since)
    flash(f"已触发覆盖抓取：{s.name}（后台执行中，请稍后查看日志）", "info")
    return redirect(url_for("sources.index"))


@bp.route("/reimport", methods=["POST"])
def reimport():
    n = excel_sync.import_from_excel(EXCEL_PATH, replace=False)
    flash(f"已从 Excel 同步数据源（共 {n} 条）", "success")
    return redirect(url_for("sources.index"))
