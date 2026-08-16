# -*- coding: utf-8 -*-
"""注册全部蓝图。"""
from .dashboard import bp as dashboard_bp
from .sources import bp as sources_bp
from .tasks import bp as tasks_bp
from .articles import bp as articles_bp
from .logs import bp as logs_bp
from .releases import bp as releases_bp
from .auth_route import bp as auth_bp
from .admin import bp as admin_bp


def register_routes(app):
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(sources_bp, url_prefix="/sources")
    app.register_blueprint(tasks_bp, url_prefix="/tasks")
    app.register_blueprint(articles_bp, url_prefix="/articles")
    app.register_blueprint(logs_bp, url_prefix="/logs")
    app.register_blueprint(releases_bp, url_prefix="/releases")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
