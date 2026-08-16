# -*- coding: utf-8 -*-
"""文章/文件：列表筛选、下载 docx、打开所在目录、手动导入（供公众号/学习强国）。"""
import os
import re
import sys
import json
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


def _parse_content_blocks(content_json, plain_fallback):
    """解析编辑器提交的 blocks JSON（text / image 块），失败则回退纯文本按行分段。

    仅接受 text 块与 http(s) 远程 / data:image/ 内嵌图片；限制单图与总大小，
    防止误传超大截图撑爆请求与磁盘。返回 (blocks, err)，err 非空表示拒绝提交。
    """
    blocks = []
    if content_json:
        try:
            raw = json.loads(content_json)
        except ValueError:
            raw = None
        if isinstance(raw, list):
            total = 0
            for b in raw[:500]:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    data = str(b.get("data") or "").strip()
                    if data:
                        blocks.append({"type": "text", "data": data[:20000]})
                elif b.get("type") == "image":
                    src = str(b.get("src") or "").strip()
                    if src.startswith(("http://", "https://")):
                        blocks.append({"type": "image", "src": src[:2000]})
                    elif src.startswith("data:image/") and len(src) <= 9_000_000:
                        blocks.append({"type": "image", "src": src})
                        total += len(src)
            if total > 26_000_000:
                return [], "粘贴的图片总量过大（约超 18MB），请压缩或减少图片后重试"
    if not blocks and plain_fallback:
        blocks = [{"type": "text", "data": p.strip()}
                  for p in plain_fallback.splitlines() if p.strip()]
    return blocks, ""


def _wipe_output_dir():
    """彻底清空 OUTPUT_DIR：删除其下所有文件与子目录（保留 OUTPUT_DIR 本身）。

    自底向上走目录树，文件删除失败（如被 WORD 占用）不阻断其余清理，
    返回成功删除的文件数。
    """
    n = 0
    for root, dirs, files in os.walk(OUTPUT_DIR, topdown=False):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
                n += 1
            except OSError:
                pass
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    return n


def remove_article_files(article):
    """删除单篇文章生成的 WORD 与配套图片（images/ 目录按分类/日期共享，
    只删以本文 docx 文件名为前缀的图片）。

    返回 (删除文件数, 涉及的 images 目录绝对路径集合)；文件缺失/删除失败不抛错。
    供清空文章与删除数据源共用。
    """
    n, dirs = 0, set()
    base = os.path.basename((article.docx_path or "").replace("\\", "/"))
    stem = os.path.splitext(base)[0]
    if article.docx_path:
        docx_abs = os.path.join(OUTPUT_DIR, article.docx_path)
        if os.path.isfile(docx_abs):
            try:
                os.remove(docx_abs)
                n += 1
            except OSError:
                pass
    if article.images_dir and stem:
        img_dir = os.path.join(OUTPUT_DIR, article.images_dir)
        dirs.add(img_dir)
        if os.path.isdir(img_dir):
            for f in os.listdir(img_dir):
                if f.startswith(stem + "_"):
                    try:
                        os.remove(os.path.join(img_dir, f))
                        n += 1
                    except OSError:
                        pass
    return n, dirs


def prune_empty_dirs(dirs):
    """自底向上清理 OUTPUT_DIR 下的空目录（不动 OUTPUT_DIR 本身与目录里的未入库文件）。"""
    for d in dirs:
        cur = d
        while cur and cur != OUTPUT_DIR and os.path.isdir(cur) and not os.listdir(cur):
            os.rmdir(cur)
            cur = os.path.dirname(cur)


@bp.route("/clear-all", methods=["POST"])
def clear_all():
    """一键清空文章记录，按选择决定是否连带删除文件（均不可恢复）。

    mode=records  仅清空记录，保留 data/output 下全部文件
    mode=tracked  连带删除各文章生成的 WORD 及配套图片（默认）
    mode=all      彻底清空 OUTPUT_DIR，含未入库文件

    tracked 的 images/ 目录按 分类/日期 共享，只删以本文 docx 文件名为前缀的图片；
    最后自底向上清理空目录（不动 OUTPUT_DIR 本身与目录里的未入库文件）。
    """
    mode = request.form.get("mode") or "tracked"
    if mode not in ("records", "tracked", "all"):
        mode = "tracked"

    articles = Article.query.all()
    n_files = 0
    if mode == "tracked":
        images_dirs = set()
        for a in articles:
            n, dirs = remove_article_files(a)
            n_files += n
            images_dirs |= dirs
        Article.query.delete(synchronize_session=False)
        db.session.commit()

        prune_empty_dirs(images_dirs)
    elif mode == "all":
        Article.query.delete(synchronize_session=False)
        db.session.commit()
        n_files = _wipe_output_dir()
    else:
        Article.query.delete(synchronize_session=False)
        db.session.commit()

    if mode == "records":
        flash(f"已清空 {len(articles)} 篇文章记录（文件已保留）", "success")
    elif mode == "all":
        flash(f"已清空 {len(articles)} 篇文章，output 目录共删除 {n_files} 个文件", "success")
    else:
        flash(f"已清空 {len(articles)} 篇文章、{n_files} 个文件", "success")
    return redirect(url_for("articles.index"))


@bp.route("/import", methods=["GET", "POST"])
def manual_import():
    sources = Source.query.order_by(Source.category, Source.name).all()
    if request.method == "POST":
        form = request.form
        source_id = form.get("source_id", type=int)
        title = (form.get("title") or "").strip()
        author = (form.get("author") or "").strip()
        publish_date = (form.get("publish_date") or "").strip()
        blocks, err = _parse_content_blocks(form.get("content_json"),
                                            form.get("content"))

        source = db.session.get(Source, source_id) if source_id else None
        if err:
            flash(err, "danger")
            return render_template("import.html", sources=sources, form=form)
        if not source or not title or not blocks:
            flash("来源、标题、正文必填", "danger")
            return render_template("import.html", sources=sources, form=form)

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
