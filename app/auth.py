# -*- coding: utf-8 -*-
"""认证与权限：app 级 before_request 全局拦截 + after_request 操作日志 + 装饰器。

设计要点：
- before_request 注册在 app 上（非蓝图），覆盖所有蓝图，
  现有业务蓝图（dashboard/sources/tasks/articles/logs）零改动即受登录保护。
- 白名单放行 /auth、/static；scheduler 后台线程不经 Flask 请求分发，天然不受影响。
- context_processor 注入 current_user 与 sidebar_roots（当前用户可见菜单树）。
"""
import time
from functools import wraps

from flask import (session, redirect, url_for, request, g, flash,
                   current_app)

from .extensions import db
from .models import User, OperationLog, Menu


# 完全放行的请求路径前缀
PUBLIC_PATH_PREFIXES = ("/auth", "/static")


def _load_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


def _build_visible_tree(user):
    """构建当前用户可见的菜单树（仅 directory/menu、visible、按 sort_order）。

    返回 list[dict]，每个 dict = {"menu": Menu, "children": [...]}。
    """
    if not user or not user.role:
        return []
    owned_ids = {m.id for m in user.role.menus}
    if not owned_ids:
        return []
    menus = (Menu.query.filter(Menu.type != "button", Menu.visible.is_(True))
             .order_by(Menu.sort_order, Menu.id).all())
    menus = [m for m in menus if m.id in owned_ids]
    by_id = {m.id: {"menu": m, "children": []} for m in menus}
    roots = []
    for node in by_id.values():
        m = node["menu"]
        if m.parent_id and m.parent_id in by_id:
            by_id[m.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _menu_url(menu):
    """安全反解菜单 path 为 URL，失败回退 #（防 BuildError 中断整页渲染）。"""
    if not menu or not menu.path:
        return "#"
    try:
        return url_for(menu.path)
    except Exception:
        return "#"


def init_auth(app):
    """在 create_app 中、register_routes 之后调用。"""

    @app.before_request
    def _authenticate():
        g._req_start = time.time()
        # 白名单：静态资源、auth 前缀（登录/验证码/登出）放行
        if request.path.startswith(PUBLIC_PATH_PREFIXES):
            g.user = None
            return None

        user = _load_user()
        g.user = user

        if not user:
            flash("请先登录", "warning")
            return redirect(url_for("auth.login", next=request.full_path))

        if not user.enabled:
            session.clear()
            flash("账号已被禁用，请联系管理员", "danger")
            return redirect(url_for("auth.login"))
        return None

    @app.after_request
    def _log_operation(response):
        # 只记录写操作：非 GET、非 static、非 auth（登录日志走专门表）
        try:
            ep = request.endpoint or ""
            if (request.method != "GET"
                    and not request.path.startswith("/static")
                    and not ep.startswith("auth.")
                    and getattr(g, "user", None) is not None):
                start = getattr(g, "_req_start", None)
                dur = int((time.time() - start) * 1000) if start else 0
                fwd = request.headers.get("X-Forwarded-For", "")
                ip = (fwd.split(",")[0].strip() if fwd else "") \
                    or request.remote_addr or ""
                db.session.add(OperationLog(
                    user_id=g.user.id,
                    username=g.user.username,
                    method=request.method,
                    path=request.path,
                    status_code=response.status_code,
                    duration_ms=dur,
                    ip=ip,
                ))
                db.session.commit()
        except Exception as e:
            current_app.logger.warning("写操作日志失败: %s", e)
        return response

    @app.context_processor
    def _inject_globals():
        user = getattr(g, "user", None)
        return {
            "current_user": user,
            "sidebar_roots": _build_visible_tree(user) if user else [],
            "menu_url": _menu_url,
        }


def login_required(view):
    """显式登录保护（before_request 已全局拦截，此装饰器供特殊路由标注）。"""
    @wraps(view)
    def _w(*a, **kw):
        if getattr(g, "user", None) is None:
            flash("请先登录", "warning")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*a, **kw)
    return _w


def permission_required(code):
    """按钮/操作级权限校验装饰器。用法：@permission_required('source:delete')"""
    def deco(view):
        @wraps(view)
        def _w(*a, **kw):
            user = getattr(g, "user", None)
            if user is None:
                flash("请先登录", "warning")
                return redirect(url_for("auth.login", next=request.full_path))
            if not user.has_perm(code):
                flash(f"无权限：{code}", "danger")
                return redirect(request.referrer or url_for("dashboard.index"))
            return view(*a, **kw)
        return _w
    return deco
