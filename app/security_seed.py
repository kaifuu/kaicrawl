# -*- coding: utf-8 -*-
"""安全种子：幂等建菜单树 + admin 角色（挂全部菜单）+ admin/admin123 用户。

在 create_app 的 app_context 中、db.create_all() 之后调用。
每条菜单按 code 判存（缺则补），二次运行可补全被删的菜单。
"""
from .extensions import db
from .models import Menu, Role, User


# (name, code, type, path(端点), icon, permission, parent_code, sort)
MENU_DEFS = [
    ("仪表盘", "dashboard", "menu", "dashboard.index", "📊", "", None, 1),
    ("数据源管理", "source", "menu", "sources.index", "🌐", "", None, 2),
    ("数据源-新增", "source:add", "button", "sources.new", "", "source:add", "source", 1),
    ("数据源-编辑", "source:edit", "button", "sources.edit", "", "source:edit", "source", 2),
    ("数据源-删除", "source:delete", "button", "sources.delete", "", "source:delete", "source", 3),
    ("数据源-抓取", "source:run", "button", "sources.run", "", "source:run", "source", 4),
    ("数据源-重导入", "source:reimport", "button", "sources.reimport", "", "source:reimport", "source", 5),
    ("任务管理", "task", "menu", "tasks.index", "⏰", "", None, 3),
    ("任务-新增", "task:add", "button", "tasks.new", "", "task:add", "task", 1),
    ("任务-启停", "task:toggle", "button", "tasks.toggle", "", "task:toggle", "task", 2),
    ("任务-运行", "task:run", "button", "tasks.run", "", "task:run", "task", 3),
    ("任务-删除", "task:delete", "button", "tasks.delete", "", "task:delete", "task", 4),
    ("文章文件", "article", "menu", "articles.index", "📄", "", None, 4),
    ("手动导入", "article_import", "menu", "articles.manual_import", "📥", "", None, 5),
    ("URL 批量导入", "article_url_import", "menu", "articles.import_urls", "🔗", "", None, 6),
    ("运行日志", "log", "menu", "logs.index", "📜", "", None, 7),
    ("发布管理", "release", "menu", "releases.index", "📦", "", None, 8),
    ("发布-构建", "release:build", "button", "", "", "release:build", "release", 1),
    ("发布-删除", "release:delete", "button", "", "", "release:delete", "release", 2),

    ("系统管理", "system", "directory", "", "⚙️", "", None, 99),
    ("人员管理", "user", "menu", "admin.users", "👥", "", "system", 1),
    ("人员-新增", "user:add", "button", "", "", "user:add", "user", 1),
    ("人员-编辑", "user:edit", "button", "", "", "user:edit", "user", 2),
    ("人员-启停", "user:toggle", "button", "", "", "user:toggle", "user", 3),
    ("人员-删除", "user:delete", "button", "", "", "user:delete", "user", 4),
    ("角色管理", "role", "menu", "admin.roles", "🛡️", "", "system", 2),
    ("角色-新增", "role:add", "button", "", "", "role:add", "role", 1),
    ("角色-编辑", "role:edit", "button", "", "", "role:edit", "role", 2),
    ("角色-分配菜单", "role:assign", "button", "", "", "role:assign", "role", 3),
    ("角色-删除", "role:delete", "button", "", "", "role:delete", "role", 4),
    ("菜单管理", "menu_mgmt", "menu", "admin.menus", "📐", "", "system", 3),
    ("操作日志", "op_log", "menu", "admin.operation_logs", "🧾", "", "system", 4),
    ("登录日志", "login_log", "menu", "admin.login_logs", "🔑", "", "system", 5),
]


def seed_security():
    """幂等建菜单/角色/管理员。已存在则跳过对应部分。"""
    # 1. 菜单：按 code 判存，缺则补
    existing = {m.code: m for m in Menu.query.all()}
    for name, code, mtype, path, icon, perm, parent_code, sort in MENU_DEFS:
        if code in existing:
            continue
        parent_id = None
        if parent_code:
            pm = existing.get(parent_code)
            parent_id = pm.id if pm else None
        m = Menu(
            name=name, code=code, type=mtype, path=path, icon=icon,
            permission=perm, parent_id=parent_id, sort_order=sort,
            visible=(mtype != "button"),
        )
        db.session.add(m)
        db.session.flush()
        existing[code] = m
    db.session.commit()

    # 2. admin 角色，挂全部菜单
    role = Role.query.filter_by(code="admin").first()
    if not role:
        role = Role(name="管理员", code="admin",
                    remark="内置超级管理员", enabled=True)
        db.session.add(role)
        db.session.flush()
    role.menus = Menu.query.all()
    db.session.commit()

    # 3. admin/admin123 用户
    if not User.query.filter_by(username="admin").first():
        u = User(username="admin", nickname="超级管理员",
                 role_id=role.id, enabled=True)
        u.set_password("admin123")
        db.session.add(u)
        db.session.commit()
