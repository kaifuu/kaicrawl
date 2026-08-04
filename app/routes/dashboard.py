# -*- coding: utf-8 -*-
"""仪表盘：概览统计与最近日志。"""
from datetime import date

from flask import Blueprint, render_template

from ..models import Source, Task, Article, CrawlLog

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    today = date.today().isoformat()
    today_display = date.today().strftime("%Y年%m月%d日")
    stats = {
        "sources": Source.query.count(),
        "sources_enabled": Source.query.filter_by(enabled=True).count(),
        "tasks": Task.query.count(),
        "tasks_enabled": Task.query.filter_by(enabled=True).count(),
        "articles": Article.query.count(),
        "today_articles": Article.query.filter(Article.crawled_at >= today).count(),
        "today_ok": Article.query.filter(
            Article.crawled_at >= today, Article.status == "ok"
        ).count(),
    }
    recent_logs = CrawlLog.query.order_by(CrawlLog.id.desc()).limit(8).all()
    recent_articles = Article.query.order_by(Article.id.desc()).limit(8).all()
    return render_template(
        "dashboard.html", stats=stats, today=today_display,
        recent_logs=recent_logs, recent_articles=recent_articles,
    )
