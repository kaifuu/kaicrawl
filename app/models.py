# -*- coding: utf-8 -*-
"""数据模型：Source / Task / Article / CrawlLog。"""
from datetime import datetime

from .extensions import db


class Source(db.Model):
    """数据源：对应《爬虫数据来源.xlsx》中的一行（分类 + 来源 + URL）。"""
    __tablename__ = "sources"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(64), nullable=False, index=True)   # 新闻分类
    name = db.Column(db.String(128), nullable=False)                  # 来源名称，如「人民网」
    url = db.Column(db.String(512), nullable=False)                   # 列表/入口 URL
    source_type = db.Column(db.String(32), default="website")         # website / wechat / xuexi
    parser_key = db.Column(db.String(64), nullable=False)             # 映射到解析器插件
    author_policy = db.Column(db.String(64), default="")              # 单位动态 -> 「各单位」
    content_xpath = db.Column(db.String(255), default="")             # 详情页正文区域 XPath，留空=用解析器默认选择器
    enabled = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    articles = db.relationship("Article", backref="source", lazy="dynamic")
    tasks = db.relationship("Task", backref="source", lazy="dynamic")

    def __repr__(self):
        return f"<Source {self.category}/{self.name}>"


class Task(db.Model):
    """调度任务：一个来源 + 一个每日执行时间点（HH:MM）。"""
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"), nullable=False)
    run_time = db.Column(db.String(8), nullable=False)   # "HH:MM"
    enabled = db.Column(db.Boolean, default=True)
    last_run_at = db.Column(db.DateTime)
    last_status = db.Column(db.String(16), default="")   # success / error / running
    last_message = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    @property
    def hour_minute(self):
        try:
            h, m = self.run_time.split(":")
            return int(h), int(m)
        except Exception:
            return 8, 0

    def __repr__(self):
        return f"<Task {self.name} @ {self.run_time}>"


class Article(db.Model):
    """已抓取文章的元数据。(source_id, url) 联合作为去重依据。"""
    __tablename__ = "articles"
    __table_args__ = (db.UniqueConstraint("source_id", "url", name="uq_source_url"),)

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"), nullable=False, index=True)
    title = db.Column(db.String(512), default="")
    author = db.Column(db.String(128), default="")
    publish_date = db.Column(db.String(32), default="")          # 原文发布日期
    url = db.Column(db.String(1024), nullable=False, index=True)  # 同一来源内去重
    docx_path = db.Column(db.String(512), default="")            # 相对 OUTPUT_DIR 的路径
    images_dir = db.Column(db.String(512), default="")
    status = db.Column(db.String(16), default="ok")              # ok / error
    error_msg = db.Column(db.String(500), default="")
    crawled_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def __repr__(self):
        return f"<Article {self.title[:20]}>"


class CrawlLog(db.Model):
    """单次抓取（计划或手动）的执行记录。"""
    __tablename__ = "crawl_logs"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, nullable=True, index=True)   # 手动运行时为空
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"), nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(16), default="running")         # running / success / error
    new_count = db.Column(db.Integer, default=0)
    total_count = db.Column(db.Integer, default=0)
    message = db.Column(db.Text, default="")

    def __repr__(self):
        return f"<CrawlLog #{self.id} {self.status}>"


# ===== 认证与权限（RBAC） =====
from werkzeug.security import generate_password_hash, check_password_hash


# 角色-菜单 多对多关联表
role_menus = db.Table(
    "role_menus",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("menu_id", db.Integer, db.ForeignKey("menus.id"), primary_key=True),
)


class Role(db.Model):
    """角色：聚合一批菜单/按钮权限，用户通过 role_id 关联（单角色）。"""
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)   # 显示名，如「管理员」
    code = db.Column(db.String(64), unique=True, nullable=False)   # 标识，如 admin
    remark = db.Column(db.String(255), default="")
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    menus = db.relationship("Menu", secondary=role_menus, backref="roles")
    users = db.relationship("User", backref="role")

    def permission_codes(self):
        """聚合该角色所有菜单的 permission 字段，供权限校验。"""
        codes = set()
        for m in self.menus:
            if m.permission:
                codes.add(m.permission)
        return codes

    def __repr__(self):
        return f"<Role {self.code}>"


class User(db.Model):
    """登录用户。密码仅存 hash，set_password/check_password 封装 werkzeug。"""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(64), default="")                # 显示名
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=True)
    enabled = db.Column(db.Boolean, default=True)
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        if not self.password_hash or not raw:
            return False
        return check_password_hash(self.password_hash, raw)

    @property
    def permissions(self):
        return self.role.permission_codes() if self.role else set()

    def has_perm(self, code):
        return code in self.permissions

    def __repr__(self):
        return f"<User {self.username}>"


class Menu(db.Model):
    """自关联树。type: directory(目录)/menu(菜单)/button(按钮)。
    path 仅 menu 存 url_for 端点名（如 'sources.index'）；permission 仅受控项存权限码。"""
    __tablename__ = "menus"

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("menus.id"), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)
    code = db.Column(db.String(64), unique=True, nullable=False)
    type = db.Column(db.String(16), default="menu")   # directory / menu / button
    path = db.Column(db.String(128), default="")       # url_for 端点名或 URL
    icon = db.Column(db.String(32), default="")        # emoji
    permission = db.Column(db.String(64), default="")  # 权限码，如 source:add
    sort_order = db.Column(db.Integer, default=0)
    visible = db.Column(db.Boolean, default=True)      # 是否在侧边栏显示
    created_at = db.Column(db.DateTime, default=datetime.now)

    children = db.relationship(
        "Menu",
        backref=db.backref("parent", remote_side="Menu.id"),
        order_by="Menu.sort_order",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Menu {self.code}>"


class OperationLog(db.Model):
    """操作日志：after_request 自动记录的写操作（非 GET）。"""
    __tablename__ = "operation_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    username = db.Column(db.String(64), default="")
    method = db.Column(db.String(8))                   # POST / DELETE ...
    path = db.Column(db.String(255), index=True)
    status_code = db.Column(db.Integer)
    duration_ms = db.Column(db.Integer, default=0)
    ip = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def __repr__(self):
        return f"<OperationLog {self.method} {self.path} {self.status_code}>"


class LoginLog(db.Model):
    """登录日志：记录每次登录尝试（成功/失败）。"""
    __tablename__ = "login_logs"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True)
    ip = db.Column(db.String(64), default="")
    status = db.Column(db.String(16))                  # success / failed
    message = db.Column(db.String(255), default="")    # 如 密码错误 / 验证码错误
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def __repr__(self):
        return f"<LoginLog {self.username} {self.status}>"
