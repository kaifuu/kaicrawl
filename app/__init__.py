# -*- coding: utf-8 -*-
"""Flask 应用工厂：配置、数据库、路由、目录、种子导入、调度器、认证。"""
import os

from flask import Flask

from .extensions import db
from .routes import register_routes
from . import excel_sync, scheduler_jobs, auth, security_seed
from config import DATA_DIR, OUTPUT_DIR, EXCEL_PATH, DB_URI, RESOURCE_DIR


def _ensure_columns():
    """SQLite 轻量迁移：db.create_all 只建缺失的表，不会给已有表加列。

    手动检查 sources 表，缺列则 ALTER TABLE 补上。幂等，可随启动重复执行。
    不引入 Alembic，与项目「删 db 重建」的轻量风格一致，且不丢现有数据。
    """
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    if "sources" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sources")}
    # sources 表新增列统一在此登记：(列名, DDL 类型与默认值)，启动时自动补齐
    needed = [
        ("content_xpath", "VARCHAR(255) DEFAULT ''"),
        ("list_xpath", "VARCHAR(255) DEFAULT ''"),
        ("date_xpath", "VARCHAR(255) DEFAULT ''"),
        ("meta_xpath", "VARCHAR(255) DEFAULT ''"),
        ("page_url_pattern", "VARCHAR(255) DEFAULT ''"),
        ("render_mode", "VARCHAR(16) DEFAULT 'static'"),
    ]
    with db.engine.begin() as conn:
        for name, ddl in needed:
            if name not in cols:
                conn.execute(text(f"ALTER TABLE sources ADD COLUMN {name} {ddl}"))


def create_app(start_sched=True):
    app = Flask(
        __name__,
        template_folder=os.path.join(RESOURCE_DIR, "templates"),
        static_folder=os.path.join(RESOURCE_DIR, "static"),
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = "dch-crawler-secret-key"
    # 抓取线程逐条写运行日志 + web 线程并发写时的 SQLite busy 容忍（默认 5s 偏短）
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 20}}

    db.init_app(app)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    register_routes(app)
    auth.init_auth(app)            # 全局 before_request 登录保护（须在路由注册之后）

    with app.app_context():
        db.create_all()
        _ensure_columns()
        seeded = excel_sync.seed_if_empty(EXCEL_PATH)
        if seeded:
            app.logger.info("已从 Excel 种子导入 %d 个数据源", seeded)
        security_seed.seed_security()   # 菜单/角色/管理员（幂等）
        if start_sched:
            scheduler_jobs.reconcile(app)

    return app
