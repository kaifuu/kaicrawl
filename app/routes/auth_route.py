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
