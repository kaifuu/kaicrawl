# -*- coding: utf-8 -*-
"""Flask 应用工厂：配置、数据库、路由、目录、种子导入、调度器、认证。"""
import os

from flask import Flask

from .extensions import db
from .routes import register_routes
from . import excel_sync, scheduler_jobs, auth, security_seed
from config import DATA_DIR, OUTPUT_DIR, EXCEL_PATH, DB_URI, BASE_DIR


def create_app(start_sched=True):
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = "dch-crawler-secret-key"

    db.init_app(app)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    register_routes(app)
    auth.init_auth(app)            # 全局 before_request 登录保护（须在路由注册之后）

    with app.app_context():
        db.create_all()
        seeded = excel_sync.seed_if_empty(EXCEL_PATH)
        if seeded:
            app.logger.info("已从 Excel 种子导入 %d 个数据源", seeded)
        security_seed.seed_security()   # 菜单/角色/管理员（幂等）
        if start_sched:
            scheduler_jobs.reconcile(app)

    return app
