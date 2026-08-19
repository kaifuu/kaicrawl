# -*- coding: utf-8 -*-
"""认证路由：登录（含验证码）/ 登出 / 验证码图片。"""
from datetime import datetime
from urllib.parse import urljoin

from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, send_file)

from ..extensions import db
from ..models import User, LoginLog
from .. import captcha, auth as auth_mod

bp = Blueprint("auth", __name__)


def _is_safe_url(target):
    """防止开放重定向：只允许同站跳转。"""
    if not target:
        return False
    ref = urljoin(request.host_url, target)
    return ref.startswith(request.host_url)


@bp.route("/login", methods=["GET", "POST"])
def login():
    # 已登录直接进首页
    if auth_mod._load_user():
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        code = request.form.get("captcha") or ""
        ip = request.remote_addr or ""

        def _fail(msg):
            db.session.add(LoginLog(username=username, ip=ip,
                                    status="failed", message=msg))
            db.session.commit()
            flash(msg, "danger")

        # 1) 验证码
        if not captcha.verify_captcha(code):
            _fail("验证码错误")
            return render_template("auth/login.html", username=username)

        # 2) 用户名密码
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            _fail("用户名或密码错误")
            return render_template("auth/login.html", username=username)

        # 3) 启用状态
        if not user.enabled:
            _fail("账号已被禁用")
            return render_template("auth/login.html", username=username)

        # 4) 登录成功
        session.clear()
        session["user_id"] = user.id
        user.last_login_at = datetime.now()
        user.last_login_ip = ip
        db.session.add(LoginLog(username=username, ip=ip,
                                status="success", message="登录成功"))
        db.session.commit()

        nxt = request.args.get("next") or ""
        if nxt and _is_safe_url(nxt):
            return redirect(nxt)
        return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html", username="")


def _back_url():
    """POST 后回到来源页（表单 next 优先），只允许同站跳转。"""
    nxt = request.form.get("next") or request.referrer or ""
    if nxt and _is_safe_url(nxt):
        return nxt
    return url_for("dashboard.index")


def _login_user_or_redirect():
    """/auth 前缀在全局拦截白名单内（g.user 恒为 None），须自行加载用户。"""
    user = auth_mod._load_user()
    if not user:
        flash("请先登录", "warning")
        return None, redirect(url_for("auth.login"))
    return user, None


@bp.route("/profile", methods=["POST"])
def profile():
    """右上角「基本信息」：修改显示名。"""
    user, resp = _login_user_or_redirect()
    if resp:
        return resp
    user.nickname = (request.form.get("nickname") or "").strip()[:64]
    db.session.commit()
    flash("基本信息已更新", "success")
    return redirect(_back_url())


@bp.route("/password", methods=["POST"])
def password():
    """右上角「基本信息」：修改密码（校验当前密码，新密码两次一致）。"""
    user, resp = _login_user_or_redirect()
    if resp:
        return resp
    old = request.form.get("old_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm") or ""
    if not user.check_password(old):
        flash("当前密码不正确", "danger")
    elif len(new) < 6:
        flash("新密码至少 6 位", "danger")
    elif new != confirm:
        flash("两次输入的新密码不一致", "danger")
    else:
        user.set_password(new)
        db.session.commit()
        flash("密码已修改", "success")
    return redirect(_back_url())


@bp.route("/logout")
def logout():
    session.clear()
    flash("已退出登录", "info")
    return redirect(url_for("auth.login"))


@bp.route("/captcha")
def captcha_image():
    buf, _ = captcha.generate_captcha()
    resp = send_file(buf, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp
