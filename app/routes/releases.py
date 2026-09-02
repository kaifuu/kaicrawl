# -*- coding: utf-8 -*-
"""发布管理：网页端一键把工程打包成 EXE（后台线程执行 build_exe.py），
含版本管理（版本号 + 更新说明的历史记录）、构建实时日志、产物 zip 下载。

- 渠道：win7 = 正式 Win7 包（需本机装 Python 3.8/3.9）；smoke = 冒烟包（当前 Python，验证链路）
- 打包版（frozen）内不可再构建，页面只读
"""
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, send_file, current_app)

from ..extensions import db
from ..models import Release, ReleaseLine
from ..auth import permission_required
from config import BASE_DIR, APP_VERSION, PORT

bp = Blueprint("releases", __name__)

CHANNEL_LABELS = {"win7": "Win7 正式", "win10": "Win10 正式", "smoke": "冒烟验证"}
PER_PAGE = 10


def _norm_version(raw):
    """归一化版本号：去空白与 v 前缀，校验 X.Y[.Z[.N]] 数字格式；非法返回 None。"""
    v = (raw or "").strip().lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){0,3}", v):
        return None
    return v


def _bump_patch(v):
    parts = v.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def suggest_version():
    """下一个建议版本号：从未发布过用 APP_VERSION，否则最新版本号补丁位 +1。"""
    rel = Release.query.order_by(Release.id.desc()).first()
    if not rel:
        return _norm_version(APP_VERSION) or "1.0.0"
    return _bump_patch(_norm_version(rel.version) or "1.0.0")


def _norm_port(raw):
    """端口归一化：空/缺省返回 None（EXE 用默认 5000）；非法返回 -1 供报错。"""
    v = (raw or "").strip()
    if not v:
        return None
    if not v.isdigit() or not 1 <= int(v) <= 65535:
        return -1
    return int(v)


def _find_toolchain():
    """是否已装 Win7 工具链（Python 3.8/3.9）；复用 build_exe 的探测逻辑。"""
    try:
        from build_exe import find_win7_python
        return find_win7_python(None)
    except Exception:
        return None


# ===== 构建（后台线程） =====

def _log(rid, text, level="info"):
    """追加一行构建输出（独立事务提交，供前端轮询）。"""
    seq = (db.session.query(db.func.max(ReleaseLine.seq))
           .filter_by(release_id=rid).scalar() or 0) + 1
    db.session.add(ReleaseLine(release_id=rid, seq=seq,
                               level=level, text=str(text)[:2000]))
    db.session.commit()


def _line_level(t):
    """按输出内容粗分级，前端着色用。"""
    if "ERROR" in t or "Traceback" in t or "失败" in t:
        return "error"
    if "WARNING" in t or "warning" in t or "警告" in t:
        return "warn"
    if "完成" in t or "Build complete" in t or "成功" in t:
        return "success"
    return "info"


def _run_build(app, rid, channel):
    with app.app_context():
        try:
            _build(rid, channel)
        except Exception as e:
            rel = db.session.get(Release, rid)
            if rel:
                rel.status = "failed"
                rel.finished_at = datetime.now()
                rel.message = f"构建异常：{e}"
                db.session.commit()
            try:
                _log(rid, f"构建异常：{e}", "error")
            except Exception:
                pass


def _build(rid, channel):
    rel = db.session.get(Release, rid)
    port_label = f"，启动端口 {rel.port}" if rel.port else ""
    _log(rid, f"开始构建 v{rel.version}（{CHANNEL_LABELS.get(channel, channel)}{port_label}）", "info")

    script = os.path.join(BASE_DIR, "build_exe.py")
    if not os.path.isfile(script):
        raise RuntimeError(f"未找到打包脚本 {script}（打包版内不可构建）")

    if channel == "smoke":
        # 冒烟渠道用当前解释器：先确保 PyInstaller 可用，缺则自动装
        # （win10 渠道的依赖自检在 build_exe.py 内做，含 playwright + 浏览器）
        chk = subprocess.run([sys.executable, "-c", "import PyInstaller"],
                             capture_output=True)
        if chk.returncode != 0:
            _log(rid, "未安装 PyInstaller，自动安装中…", "warn")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pyinstaller"],
                           check=True)
            _log(rid, "PyInstaller 安装完成", "success")

    cmd = [sys.executable, "-u", script, "--version", rel.version, "--channel", channel]
    if rel.port:
        cmd += ["--port", str(rel.port)]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"       # 中文输出按 UTF-8 读，避免 GBK 乱码
    env["PYTHONUNBUFFERED"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0  # type: ignore[attr-defined]

    _log(rid, "> " + " ".join(cmd), "dim")
    proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    for line in proc.stdout:
        t = line.rstrip()
        if t:
            _log(rid, t, _line_level(t))
    rc = proc.wait()

    rel = db.session.get(Release, rid)
    if rc != 0:
        rel.status = "failed"
        rel.finished_at = datetime.now()
        rel.message = f"构建命令退出码 {rc}，详见日志"
        db.session.commit()
        _log(rid, f"构建失败（退出码 {rc}）", "error")
        return

    # ---- 成功：登记产物信息 + 打 zip 供下载 ----
    dist = os.path.join(BASE_DIR, "dist", "KaiCrawl")
    if not os.path.isfile(os.path.join(dist, "KaiCrawl.exe")):
        rel.status = "failed"
        rel.finished_at = datetime.now()
        rel.message = f"构建命令成功但未找到产物 {dist}\\KaiCrawl.exe"
        db.session.commit()
        _log(rid, rel.message, "error")
        return

    _log(rid, "正在压缩产物 zip …", "dim")
    zip_base = os.path.join(BASE_DIR, "dist", f"KaiCrawl_v{rel.version}_{rel.channel}")
    zip_abs = shutil.make_archive(zip_base, "zip",
                                  root_dir=os.path.join(BASE_DIR, "dist"),
                                  base_dir="KaiCrawl")

    size = 0
    for root, _dirs, names in os.walk(dist):
        for n in names:
            try:
                size += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass

    py_ver = ""
    if channel in ("smoke", "win10"):
        import platform
        py_ver = platform.python_version()
    else:
        venv_py = os.path.join(BASE_DIR, "build", "venv-win7", "Scripts", "python.exe")
        if os.path.isfile(venv_py):
            out = subprocess.run([venv_py, "-V"], capture_output=True, text=True)
            py_ver = (out.stdout or out.stderr or "").strip().replace("Python ", "")

    rel.dist_dir = dist
    rel.dist_size = size
    rel.zip_path = os.path.relpath(zip_abs, BASE_DIR).replace("\\", "/")
    rel.python_ver = py_ver
    rel.status = "success"
    rel.finished_at = datetime.now()
    rel.message = f"构建成功：dist/KaiCrawl（{size / 1048576:.1f} MB）"
    db.session.commit()
    _log(rid, f"v{rel.version} 构建完成 · 产物 {size / 1048576:.1f} MB · zip：{rel.zip_path}", "success")


# ===== 页面与接口 =====

@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int) or 1
    pagination = Release.query.order_by(Release.id.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False)
    latest_success = (Release.query.filter_by(status="success")
                      .order_by(Release.id.desc()).first())
    building = Release.query.filter_by(status="building").first()
    frozen = bool(getattr(sys, "frozen", False))
    toolchain = None if frozen else _find_toolchain()
    return render_template(
        "releases.html", releases=pagination.items, pagination=pagination,
        now=datetime.now(),
        current_version=(latest_success.version if latest_success else APP_VERSION),
        latest=latest_success, building=building, frozen=frozen,
        toolchain=str(toolchain) if toolchain else "",
        suggest=suggest_version(), channel_labels=CHANNEL_LABELS,
        cur_port=PORT, suggest_port=PORT + 1)


@bp.route("/build", methods=["POST"])
@permission_required("release:build")
def build():
    """发起一次构建；fetch 返回 JSON（新记录 id），表单提交则回列表页看提示。"""
    is_fetch = request.headers.get("X-Requested-With") == "fetch"
    if getattr(sys, "frozen", False):
        msg = "打包版内无法再构建：请在源码环境的电脑上打开发布管理页操作"
        if is_fetch:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("releases.index"))

    if Release.query.filter_by(status="building").first():
        msg = "已有构建正在进行，请等它结束再发起新的"
        if is_fetch:
            return jsonify({"ok": False, "error": msg}), 409
        flash(msg, "warning")
        return redirect(url_for("releases.index"))

    version = _norm_version(request.form.get("version"))
    channel = request.form.get("channel") or "win7"
    note = (request.form.get("note") or "").strip()
    if not version:
        msg = "版本号格式不对，应为 X.Y.Z 数字（如 1.0.1）"
        if is_fetch:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("releases.index"))
    if channel not in CHANNEL_LABELS:
        channel = "win7"
    port = _norm_port(request.form.get("port"))
    if port == -1:
        msg = "启动端口须为 1-65535 的数字（留空用默认 5000）"
        if is_fetch:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("releases.index"))
    dup = (Release.query.filter_by(version=version, channel=channel,
                                   status="success").first())
    if dup:
        msg = f"v{version}（{CHANNEL_LABELS[channel]}）已发布过，请递增版本号"
        if is_fetch:
            return jsonify({"ok": False, "error": msg}), 409
        flash(msg, "warning")
        return redirect(url_for("releases.index"))

    rel = Release(version=version, channel=channel, note=note, status="building", port=port)
    db.session.add(rel)
    db.session.commit()

    app = current_app._get_current_object()
    threading.Thread(target=_run_build, args=(app, rel.id, channel),
                     daemon=True).start()
    if is_fetch:
        return jsonify({"ok": True, "id": rel.id})
    flash(f"v{version} 构建已开始，可在列表查看实时日志", "success")
    return redirect(url_for("releases.index"))


@bp.route("/<int:rid>/lines")
def lines(rid):
    """轮询接口：返回 after 之后的新构建输出行 + 状态汇总。"""
    rel = db.session.get(Release, rid)
    if not rel:
        return jsonify({"error": "not found"}), 404
    after = request.args.get("after", 0, type=int) or 0
    rows = (ReleaseLine.query.filter_by(release_id=rid)
            .filter(ReleaseLine.seq > after)
            .order_by(ReleaseLine.seq).all())
    end = rel.finished_at or datetime.now()
    started = rel.started_at or end
    return jsonify({
        "release_id": rel.id,
        "status": rel.status,
        "finished": rel.status != "building",
        "finished_at": rel.finished_at.strftime("%Y-%m-%d %H:%M:%S") if rel.finished_at else None,
        "elapsed_seconds": int((end - started).total_seconds()),
        "message": rel.message or "",
        "last_seq": rows[-1].seq if rows else after,
        "lines": [{"seq": r.seq,
                   "ts": r.ts.strftime("%H:%M:%S") if r.ts else "",
                   "level": r.level,
                   "text": r.text} for r in rows],
    })


@bp.route("/<int:rid>/download")
def download(rid):
    """下载该次构建的产物 zip。"""
    rel = db.session.get(Release, rid)
    if not rel or not rel.zip_path:
        abort(404)
    abs_path = os.path.join(BASE_DIR, rel.zip_path)
    if not os.path.isfile(abs_path):
        abort(404)
    return send_file(abs_path, as_attachment=True,
                     download_name=os.path.basename(abs_path))


@bp.route("/<int:rid>/open")
def open_folder(rid):
    """在资源管理器中打开产物目录（无产物则打开 dist 根）。"""
    rel = db.session.get(Release, rid)
    if not rel:
        return jsonify({"error": "not found"}), 404
    target = rel.dist_dir if rel.dist_dir and os.path.isdir(rel.dist_dir) \
        else os.path.join(BASE_DIR, "dist")
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", target])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/<int:rid>/delete", methods=["POST"])
@permission_required("release:delete")
def delete(rid):
    """删除发布记录（磁盘上的 dist 产物不删，可手动清理）。"""
    rel = db.session.get(Release, rid)
    if not rel:
        abort(404)
    if rel.status == "building":
        flash("该构建正在进行，不能删除", "warning")
        return redirect(url_for("releases.index"))
    ReleaseLine.query.filter_by(release_id=rid).delete(synchronize_session=False)
    db.session.delete(rel)
    db.session.commit()
    flash(f"已删除发布记录 v{rel.version}（磁盘产物未动）", "success")
    return redirect(url_for("releases.index"))
