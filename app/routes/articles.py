# -*- coding: utf-8 -*-
"""文章/文件：列表筛选、下载 docx、打开所在目录、手动导入（供公众号/学习强国）。"""
import os
import re
import sys
import time
import subprocess
from datetime import date, datetime

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, send_from_directory, abort)
from sqlalchemy import func

from ..extensions import db
from ..models import Article, Source
from .. import docx_writer
from config import OUTPUT_DIR

bp = Blueprint("articles", __name__)


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    source_id = request.args.get("source_id", type=int)
    day = (request.args.get("date") or "").strip()

    query = Article.query
    if q:
        query = query.filter(Article.title.like(f"%{q}%"))
    if source_id:
        query = query.filter_by(source_id=source_id)
    if day:
        query = query.filter(func.date(Article.crawled_at) == day)
    if category:
        query = query.join(Source).filter(Source.category == category)

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Article.id.desc()).paginate(
        page=page, per_page=15, error_out=False)
    articles = pagination.items
    sources = Source.query.order_by(Source.category, Source.name).all()
    categories = sorted({s.category for s in sources})
    return render_template("articles.html", articles=articles, sources=sources,
                           categories=categories, pagination=pagination,
                           filters={"q": q, "category": category,
                                    "source_id": source_id, "date": day})


@bp.route("/<int:aid>/download")
def download(aid):
    a = db.session.get(Article, aid)
    if not a or not a.docx_path:
        abort(404)
    # docx_path 相对 OUTPUT_DIR，统一成正斜杠后取目录与文件名
    rel = a.docx_path.replace("\\", "/")
    directory = os.path.join(OUTPUT_DIR, os.path.dirname(rel))
    filename = os.path.basename(rel)
    if not os.path.isfile(os.path.join(directory, filename)):
        abort(404)
    return send_from_directory(directory, filename, as_attachment=True)


@bp.route("/<int:aid>/open")
def open_folder(aid):
    a = db.session.get(Article, aid)
    if not a:
        abort(404)
    rel = (a.docx_path or "").replace("\\", "/")
    target = os.path.join(OUTPUT_DIR, os.path.dirname(rel)) if rel else OUTPUT_DIR
    os.makedirs(target, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        flash("已打开所在目录", "info")
    except Exception as e:
        flash(f"无法打开目录：{e}", "danger")
    return redirect(url_for("articles.index"))


@bp.route("/import", methods=["GET", "POST"])
def manual_import():
    sources = Source.query.order_by(Source.category, Source.name).all()
    if request.method == "POST":
        source_id = request.form.get("source_id", type=int)
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        publish_date = (request.form.get("publish_date") or "").strip()
        content = request.form.get("content") or ""

        source = db.session.get(Source, source_id) if source_id else None
        if not source or not title or not content.strip():
            flash("来源、标题、正文必填", "danger")
            return render_template("import.html", sources=sources)

        # 正文按行分段（空行忽略），构造有序文本块
        paragraphs = [p.strip() for p in content.splitlines() if p.strip()]
        blocks = [{"type": "text", "data": p} for p in paragraphs]

        detail = {
            "title": title,
            "author": author,
            "publish_date": publish_date or date.today().isoformat(),
            "blocks": blocks,
        }
        docx_path, images_dir = docx_writer.generate(source, detail, date.today().isoformat())

        url = f"manual://{source.id}/{int(time.time())}"
        db.session.add(Article(
            source_id=source.id, title=title[:500], author=author[:128],
            publish_date=detail["publish_date"][:32], url=url,
            docx_path=os.path.relpath(docx_path, OUTPUT_DIR).replace("\\", "/"),
            images_dir=os.path.relpath(images_dir, OUTPUT_DIR).replace("\\", "/"),
            status="ok", crawled_at=datetime.now(),
        ))
        db.session.commit()
        flash("已手动导入并生成 WORD", "success")
        return redirect(url_for("articles.index"))
    return render_template("import.html", sources=sources)


@bp.route("/import-urls", methods=["GET", "POST"])
def import_urls():
    """URL 批量导入：粘贴多个文章链接（如公众号），自动抓正文生成 WORD。"""
    sources = Source.query.order_by(Source.category, Source.name).all()
    if request.method == "POST":
        source_id = request.form.get("source_id", type=int)
        raw = request.form.get("urls") or ""
        urls = [u.strip() for u in re.split(r"[\r\n]+", raw) if u.strip()]
        source = db.session.get(Source, source_id) if source_id else None
        if not source:
            flash("请选择归入的来源", "danger")
            return render_template("import_urls.html", sources=sources,
                                   source_id=source_id, urls=raw)
        if not urls:
            flash("请粘贴至少一个文章链接", "danger")
            return render_template("import_urls.html", sources=sources,
                                   source_id=source_id, urls=raw)

        from ..sources import get_parser
        parser = get_parser(source)
        results, ok_count = [], 0
        for u in urls:
            try:
                # 同来源同 URL 去重
                if Article.query.filter_by(source_id=source.id, url=u).first():
                    results.append((u, False, "已存在，跳过"))
                    continue
                detail = parser.fetch_detail(u)
                docx_path, images_dir = docx_writer.generate(
                    source, detail, date.today().isoformat())
                db.session.add(Article(
                    source_id=source.id,
                    title=(detail.get("title") or "未命名")[:500],
                    author=(detail.get("author") or "")[:128],
                    publish_date=(detail.get("publish_date") or "")[:32],
                    url=u,
                    docx_path=os.path.relpath(docx_path, OUTPUT_DIR).replace("\\", "/"),
                    images_dir=os.path.relpath(images_dir, OUTPUT_DIR).replace("\\", "/"),
                    status="ok", crawled_at=datetime.now(),
                ))
                db.session.commit()
                results.append((u, True, (detail.get("title") or "")[:60]))
                ok_count += 1
            except Exception as e:
                db.session.rollback()
                results.append((u, False, str(e)[:200]))
        flash(f"完成：成功 {ok_count} 篇，失败 {len(urls) - ok_count} 篇",
              "success" if ok_count else "danger")
        return render_template("import_urls.html", sources=sources,
                               source_id=source_id, results=results)
    return render_template("import_urls.html", sources=sources)
