# -*- coding: utf-8 -*-
"""Flask 扩展单例。分开声明以便在 create_app 中统一初始化。"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
