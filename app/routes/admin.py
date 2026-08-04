# -*- coding: utf-8 -*-
"""系统管理：人员 / 角色 / 菜单 / 日志（操作 + 登录）。

写操作通过 @permission_required 做按钮级权限校验；
内置 admin 账号/角色受保护，不可禁用/删除（防系统锁死）。
"""
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash)
from sqlalchemy import or_

from ..extensions import db
from ..models import User, Role, Menu, OperationLog, LoginLog
from ..auth import permission_required

bp = Blueprint("admin", __name__)


def _role_options():
    return [(r.id, r.name)
            for r in Role.query.filter_by(enabled=True).order_by(Role.id).all()]


def _menu_tree():
    """全部菜单的树（含 button），供菜单管理与角色分配。"""
    menus = Menu.query.order_by(Menu.sort_order, Menu.id).all()
    by_id = {m.id: {"menu": m, "children": []} for m in menus}
    roots = []
    for node in by_id.values():
        m = node["menu"]
        if m.parent_id and m.parent_id in by_id:
            by_id[m.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


# ===== 人员管理 =====

@bp.route("/")
def index():
    return redirect(url_for("admin.users"))


@bp.route("/users")
def users():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.username.like(like),
                                 User.nickname.like(like)))
    pagination = query.order_by(User.id).paginate(page=page, per_page=15, error_out=False)
    return render_template("admin/users.html", users=pagination.items, q=q,
                           role_options=_role_options(),
                           all_roles=Role.query.order_by(Role.id).all(),
                           pagination=pagination)


@bp.route("/users/new", methods=["POST"])
@permission_required("user:add")
def user_new():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    nickname = (request.form.get("nickname") or "").strip()
    role_id = request.form.get("role_id", type=int)
    enabled = request.form.get("enabled") == "on"
    if not username or not password:
        flash("用户名与初始密码必填", "danger")
        return redirect(url_for("admin.users"))
    if User.query.filter_by(username=username).first():
        flash("用户名已存在", "danger")
        return redirect(url_for("admin.users"))
    u = User(username=username, nickname=nickname,
             role_id=role_id, enabled=enabled)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash(f"已新增用户：{username}", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@permission_required("user:edit")
def user_edit(uid):
    u = db.session.get(User, uid)
    if not u:
        flash("用户不存在", "danger")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        u.nickname = (request.form.get("nickname") or "").strip()
        u.role_id = request.form.get("role_id", type=int)
        password = request.form.get("password") or ""
        if password:
            u.set_password(password)   # 留空则不改；填了即重置
        u.enabled = request.form.get("enabled") == "on"
        db.session.commit()
        flash("已保存", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=u,
                           role_options=_role_options())


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@permission_required("user:toggle")
def user_toggle(uid):
    u = db.session.get(User, uid)
    if not u:
        flash("用户不存在", "danger")
        return redirect(url_for("admin.users"))
    if u.username == "admin" and u.enabled:
        flash("内置 admin 账号不可禁用", "danger")
        return redirect(url_for("admin.users"))
    u.enabled = not u.enabled
    db.session.commit()
    flash(f"已{'启用' if u.enabled else '禁用'}：{u.username}", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:uid>/delete", methods=["POST"])
@permission_required("user:delete")
def user_delete(uid):
    u = db.session.get(User, uid)
    if not u:
        flash("用户不存在", "danger")
        return redirect(url_for("admin.users"))
    if u.username == "admin":
        flash("内置 admin 账号不可删除", "danger")
        return redirect(url_for("admin.users"))
    db.session.delete(u)
    db.session.commit()
    flash("已删除用户", "success")
    return redirect(url_for("admin.users"))


# ===== 角色管理 =====

@bp.route("/roles")
def roles():
    page = request.args.get("page", 1, type=int)
    pagination = Role.query.order_by(Role.id).paginate(page=page, per_page=15, error_out=False)
    return render_template("admin/roles.html", roles=pagination.items, pagination=pagination)


@bp.route("/roles/new", methods=["POST"])
@permission_required("role:add")
def role_new():
    name = (request.form.get("name") or "").strip()
    code = (request.form.get("code") or "").strip()
    remark = (request.form.get("remark") or "").strip()
    if not name or not code:
        flash("角色名与标识必填", "danger")
        return redirect(url_for("admin.roles"))
    if Role.query.filter_by(code=code).first():
        flash("角色标识已存在", "danger")
        return redirect(url_for("admin.roles"))
    db.session.add(Role(name=name, code=code, remark=remark, enabled=True))
    db.session.commit()
    flash(f"已新增角色：{name}", "success")
    return redirect(url_for("admin.roles"))


@bp.route("/roles/<int:rid>/edit", methods=["GET", "POST"])
@permission_required("role:edit")
def role_edit(rid):
    r = db.session.get(Role, rid)
    if not r:
        flash("角色不存在", "danger")
        return redirect(url_for("admin.roles"))
    if request.method == "POST":
        r.name = (request.form.get("name") or "").strip()
        r.remark = (request.form.get("remark") or "").strip()
        r.enabled = request.form.get("enabled") == "on"
        db.session.commit()
        flash("已保存", "success")
        return redirect(url_for("admin.roles"))
    return render_template("admin/role_form.html", role=r)


@bp.route("/roles/<int:rid>/menus", methods=["GET", "POST"])
@permission_required("role:assign")
def role_menus(rid):
    r = db.session.get(Role, rid)
    if not r:
        flash("角色不存在", "danger")
        return redirect(url_for("admin.roles"))
    if request.method == "POST":
        ids = request.form.getlist("menu_ids", type=int)
        r.menus = Menu.query.filter(Menu.id.in_(ids)).all() if ids else []
        db.session.commit()
        flash("已更新角色权限", "success")
        return redirect(url_for("admin.roles"))
    return render_template("admin/role_menus.html", role=r,
                           tree=_menu_tree(), checked={m.id for m in r.menus})


@bp.route("/roles/<int:rid>/delete", methods=["POST"])
@permission_required("role:delete")
def role_delete(rid):
    r = db.session.get(Role, rid)
    if not r:
        flash("角色不存在", "danger")
        return redirect(url_for("admin.roles"))
    if r.code == "admin":
        flash("内置 admin 角色不可删除", "danger")
        return redirect(url_for("admin.roles"))
    if User.query.filter_by(role_id=rid).first():
        flash("该角色下仍有用户，请先转移后再删除", "danger")
        return redirect(url_for("admin.roles"))
    db.session.delete(r)
    db.session.commit()
    flash("已删除角色", "success")
    return redirect(url_for("admin.roles"))


# ===== 菜单管理 =====

@bp.route("/menus")
def menus():
    return render_template("admin/menus.html", tree=_menu_tree())


def _type_options():
    return [("directory", "目录"), ("menu", "菜单"), ("button", "按钮")]


def _parent_options():
    return [(m.id, f"{m.name}（{m.code}）")
            for m in Menu.query.filter(Menu.type != "button").order_by(Menu.id).all()]


def _menu_from_form(m):
    """从表单填充菜单（新增传 None 则新建）。校验失败返回 None。"""
    name = (request.form.get("name") or "").strip()
    code = (request.form.get("code") or "").strip()
    if not name or not code:
        flash("名称与标识必填", "danger")
        return None
    dup = Menu.query.filter_by(code=code).first()
    if dup and (m is None or dup.id != m.id):
        flash("菜单标识已存在", "danger")
        return None
    if m is None:
        m = Menu()
    m.name = name
    m.code = code
    m.type = request.form.get("type", "menu").strip()
    m.path = (request.form.get("path") or "").strip()
    m.icon = (request.form.get("icon") or "").strip()
    m.permission = (request.form.get("permission") or "").strip()
    m.sort_order = request.form.get("sort_order", type=int) or 0
    m.visible = request.form.get("visible") == "on"
    pid = request.form.get("parent_id", type=int)
    m.parent_id = pid if pid else None
    return m


@bp.route("/menus/new", methods=["GET", "POST"])
def menu_new():
    if request.method == "POST":
        m = _menu_from_form(None)
        if m is None:
            return redirect(url_for("admin.menu_new"))
        db.session.add(m)
        db.session.commit()
        flash("已新增菜单", "success")
        return redirect(url_for("admin.menus"))
    return render_template("admin/menu_form.html", menu=None,
                           parent_options=_parent_options(),
                           type_options=_type_options())


@bp.route("/menus/<int:mid>/edit", methods=["GET", "POST"])
def menu_edit(mid):
    m = db.session.get(Menu, mid)
    if not m:
        flash("菜单不存在", "danger")
        return redirect(url_for("admin.menus"))
    if request.method == "POST":
        if _menu_from_form(m) is None:
            return redirect(url_for("admin.menu_edit", mid=mid))
        db.session.commit()
        flash("已保存", "success")
        return redirect(url_for("admin.menus"))
    return render_template("admin/menu_form.html", menu=m,
                           parent_options=_parent_options(),
                           type_options=_type_options())


@bp.route("/menus/<int:mid>/delete", methods=["POST"])
def menu_delete(mid):
    m = db.session.get(Menu, mid)
    if not m:
        flash("菜单不存在", "danger")
        return redirect(url_for("admin.menus"))
    if m.children:
        flash("该菜单下有子菜单，请先删除子菜单", "danger")
        return redirect(url_for("admin.menus"))
    db.session.delete(m)
    db.session.commit()
    flash("已删除菜单", "success")
    return redirect(url_for("admin.menus"))


# ===== 日志 =====

@bp.route("/logs/operation")
def operation_logs():
    q = (request.args.get("q") or "").strip()
    query = OperationLog.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(OperationLog.path.like(like),
                                 OperationLog.username.like(like)))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(OperationLog.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/operation_logs.html", logs=pagination.items, q=q, pagination=pagination)


@bp.route("/logs/login")
def login_logs():
    q = (request.args.get("q") or "").strip()
    query = LoginLog.query
    if q:
        query = query.filter(LoginLog.username.like(f"%{q}%"))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(LoginLog.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/login_logs.html", logs=pagination.items, q=q, pagination=pagination)
