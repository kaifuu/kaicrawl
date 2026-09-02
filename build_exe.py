# -*- coding: utf-8 -*-
"""发布打包：把整个工程打成 EXE（onedir 版），按渠道区分：

    python build_exe.py                     # --channel win7：自动找 Python 3.8/3.9（Win7 兼容工具链）
    python build_exe.py --channel win10     # 用当前 Python，内置 Playwright+Chromium（Win10+，渲染可用）
    python build_exe.py --channel smoke     # 用当前 Python 快速验证打包链路（不含渲染组件）
    python build_exe.py --python "C:\\Python38\\python.exe"   # 显式指定 Win7 工具链
    python build_exe.py --fast              # 复用已有构建环境，跳过依赖安装（win7 渠道）

渠道说明：
    win7  — Py3.8/3.9 独立环境 + 锁定依赖；剔除 playwright（Chromium 110+ 不支持 Win7），
            render_mode=browser 的源在 Win7 上会得到可操作报错，static 模式不受影响。
    win10 — 当前解释器 + playwright（官方 PyInstaller hook 自动带 node 驱动），
            构建后把本机 ms-playwright 的 chromium 拷进产物（EXE 旁 ms-playwright\\，
            版本与 playwright 库配对、随包分发），render_mode=browser 开箱即用。

产出：
    dist/KaiCrawl/            绿色目录版（整目录拷走即可运行）
    dist/KaiCrawl/ + Inno Setup 编译 build/KaiCrawl.iss 可得安装包（可选）
    dist/KaiCrawl/data/crawler.db  当前数据源/任务/账户配置快照（运行期数据已清空）
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
VENV = BUILD / "venv-win7"
WIN7_MINORS = (8, 9)   # Win7 支持的 Python 大版本：3.8 / 3.9

# pip 子进程统一环境与镜像：
# - NO_PROXY=* 强制直连：系统代理开着时，venv 里的老 pip（urllib3 旧版）走 https 代理
#   会触发「check_hostname requires server_hostname」崩溃（TLS-in-TLS bug）；
# - 默认清华镜像：pypi.org 直连慢/易超时；可用环境变量 KAI_PIP_INDEX 换源或设 "" 用官方源。
PIP_ENV = dict(os.environ, NO_PROXY="*", no_proxy="*")
PIP_INDEX = os.environ.get("KAI_PIP_INDEX") or "https://pypi.tuna.tsinghua.edu.cn/simple"


def _ver(py: Path):
    try:
        out = subprocess.run([str(py), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True)
        return out.stdout.strip()
    except Exception:
        return ""


def _stamp_version(version: str):
    """把版本号写进产物（dist/KaiCrawl/VERSION.txt）并同步 Inno Setup 脚本。"""
    marker = ROOT / "dist" / "KaiCrawl" / "VERSION.txt"
    marker.write_text(f"{version}\n", encoding="utf-8")
    print(f"已写入版本标识：{marker}")
    iss = BUILD / "KaiCrawl.iss"
    if iss.is_file():
        text = iss.read_text(encoding="utf-8")
        new, n = re.subn(r'(#define MyAppVersion ")[^"]+(")', rf"\g<1>{version}\g<2>", text)
        if n:
            iss.write_text(new, encoding="utf-8")
            print(f"已同步安装包脚本版本：KaiCrawl.iss -> {version}")


def _stamp_port(port: int):
    """把启动端口写进产物（dist/KaiCrawl/PORT.txt）：EXE 启动时读取覆盖默认 5000。"""
    marker = ROOT / "dist" / "KaiCrawl" / "PORT.txt"
    marker.write_text(f"{port}\n", encoding="ascii")
    print(f"已写入启动端口：{marker} -> {port}")


def _playwright_browsers_dir(py: Path):
    """取本机 playwright 浏览器注册目录（ms-playwright 根目录）。问 playwright 本尊：
    dry-run 每个组件一条 "Install location: ...\\ms-playwright\\chromium-1228"，
    取公共父目录即注册表根；解析失败回退 LOCALAPPDATA 默认位置。"""
    out = subprocess.run(
        [str(py), "-m", "playwright", "install", "chromium", "--dry-run"],
        capture_output=True, text=True)
    locs = []
    for line in (out.stdout or "").splitlines():
        if "Install location" in line or "安装位置" in line:
            loc = line.split(":", 1)[-1].strip()
            if loc and Path(loc).is_dir():
                locs.append(Path(loc))
    if locs:
        root = Path(os.path.commonpath([str(p) for p in locs]))
        if any(d.name.startswith("chromium") for d in root.iterdir() if d.is_dir()):
            return root
        return locs[0].parent   # 单组件/异常布局：退一级
    la = os.environ.get("LOCALAPPDATA")
    return Path(la) / "ms-playwright" if la else None


def _bundle_browsers(py: Path):
    """win10 渠道：把本机 playwright 的 Chromium 拷进产物（EXE 旁 ms-playwright\\）。

    浏览器二进制与 playwright 库版本严格配对（目录名带版本号），必须随包分发；
    运行时 renderer 把 PLAYWRIGHT_BROWSERS_PATH 指到这里，目标机无需任何安装。
    """
    src = _playwright_browsers_dir(py)
    if not src or not src.is_dir():
        raise RuntimeError("未找到 playwright 浏览器目录，请先执行 python -m playwright install chromium")
    dst = ROOT / "dist" / "KaiCrawl" / "ms-playwright"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    copied = []
    for d in sorted(src.iterdir()):
        if d.is_dir() and d.name.startswith(("chromium", "ffmpeg", "winldd")):
            shutil.copytree(d, dst / d.name)
            copied.append(d.name)
    if not any(c.startswith("chromium") for c in copied):
        raise RuntimeError(f"{src} 下没有 chromium 目录，请先执行 python -m playwright install chromium")
    print(f"已内置浏览器渲染组件：{dst}（{', '.join(copied)}）")


def _bundle_data():
    """把当前配置库一并打进产物（dist/KaiCrawl/data/crawler.db）。

    用 SQLite backup API 从运行中的库安全快照（服务可能正在写），再清空
    运行期数据（文章/抓取日志/发布记录/操作日志/调度作业表），保留
    数据源(sources)、任务(tasks)与登录账户(users/roles/menus)——
    目标机开箱即得与本机一致的数据源管理、任务管理配置，无需重新配。
    运行期表会在使用中自然再生长；文章对应的 WORD 在 data/output，
    属于输出物不随包走，故文章记录也一并清空，避免下载指向空文件。
    """
    import sqlite3

    src = ROOT / "data" / "crawler.db"
    if not src.is_file():
        print("未找到 data/crawler.db，跳过配置库打包（目标机首次启动走 Excel 种子导入）")
        return
    dst_dir = ROOT / "dist" / "KaiCrawl" / "data"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "crawler.db"
    if dst.exists():
        dst.unlink()
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(dst))
    with dst_con:
        src_con.backup(dst_con)
    src_con.close()
    wiped = ("articles", "crawl_logs", "crawl_log_lines", "releases",
             "release_lines", "operation_logs", "login_logs", "apscheduler_jobs")
    for t in wiped:
        if dst_con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (t,)).fetchone():
            dst_con.execute(f"DELETE FROM {t}")
    dst_con.commit()
    dst_con.execute("VACUUM")
    n_src = dst_con.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    n_task = dst_con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    dst_con.close()
    print(f"已内置配置库：{dst}（数据源 {n_src} 条、任务 {n_task} 条，运行期数据已清空）")


def _ensure_module(py: Path, mod: str, pkgs):
    """当前解释器缺 mod 时自动 pip 安装 pkgs（win10/smoke 渠道用）。"""
    chk = subprocess.run([str(py), "-c", f"import {mod}"], capture_output=True)
    if chk.returncode == 0:
        return
    print(f"未安装 {mod}，自动安装中…")
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-i", PIP_INDEX] + pkgs,
                   env=PIP_ENV, check=True)
    print(f"{pkgs[0]} 安装完成")


def find_win7_python(explicit):
    """定位 Win7 兼容（3.8/3.9）的 Python 解释器。"""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("KAI_WIN7_PYTHON"):
        cands.append(Path(os.environ["KAI_WIN7_PYTHON"]))
    bases = [Path("C:\\"), Path("D:\\"), Path("E:\\")]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        bases.insert(0, Path(la) / "Programs" / "Python")
    for base in bases:
        if not base.is_dir():
            continue
        for minor in WIN7_MINORS:
            cands.append(base / f"Python3{minor}" / "python.exe")     # 官方安装布局
            for p in base.glob(f"Python3{minor}*/python.exe"):        # 带补丁号布局
                cands.append(p)
            cands += [base / d / "python.exe" for d in base.glob(f"python3{minor}*") if (base / d / "python.exe").exists()]
    for py in cands:
        if py.is_file() and _ver(py) in ("3.8", "3.9"):
            return py
    return None


def main():
    ap = argparse.ArgumentParser(description="打包 KaiCrawl 为 EXE（win7 / win10 / smoke 三渠道）")
    ap.add_argument("--channel", choices=("win7", "win10", "smoke"), default=None,
                    help="win7=Py3.8 正式包（无渲染）；win10=当前 Python 正式包（内置浏览器渲染）；smoke=链路验证")
    ap.add_argument("--python", help="指定 Win7 兼容 Python(3.8/3.9) 的 python.exe 路径")
    ap.add_argument("--fast", action="store_true", help="复用 build/venv-win7，跳过依赖安装")
    ap.add_argument("--smoke", action="store_true", help="（兼容旧用法）等价 --channel smoke")
    ap.add_argument("--version", default="", help="发布版本号（写入产物 VERSION.txt 并同步 Inno Setup 脚本）")
    ap.add_argument("--port", type=int, default=0,
                    help="打包版启动端口（写入产物 PORT.txt，缺省不写、EXE 用默认 5000）")
    args = ap.parse_args()

    if args.port and not 1 <= args.port <= 65535:
        ap.error("--port 须为 1-65535 的整数")
    channel = args.channel or ("smoke" if args.smoke else "win7")
    env = os.environ.copy()          # 传给 PyInstaller：win10 渠道保留 playwright（spec 按 env 分流）

    if channel in ("win10", "smoke"):
        py = Path(sys.executable)
        venv_py = py
        print(f"[{channel}] 用当前解释器 {_ver(py)} 打包")
        _ensure_module(py, "PyInstaller", ["pyinstaller"])
        if channel == "win10":
            _ensure_module(py, "playwright", ["playwright"])
            print("确保 Chromium 已下载（已存在则秒过）...")
            subprocess.run([str(py), "-m", "playwright", "install", "chromium"], check=True)
            env["KAI_WITH_PLAYWRIGHT"] = "1"
    else:
        toolchain = find_win7_python(args.python)
        if not toolchain:
            print("未找到 Python 3.8/3.9（Win7 兼容工具链）。")
            print("请安装 Python 3.8.10：https://www.python.org/downloads/release/python-3810/")
            print("或用 --python / 环境变量 KAI_WIN7_PYTHON 指定其路径；")
            print("或改用 --channel win10（Win10+ 目标机，内置浏览器渲染）。")
            sys.exit(1)
        print(f"Win7 工具链：{toolchain}（{_ver(toolchain)}）")
        if not (VENV / "Scripts" / "python.exe").exists():
            if VENV.exists():
                shutil.rmtree(VENV)
            print("创建构建虚拟环境 build/venv-win7 ...")
            subprocess.run([str(toolchain), "-m", "venv", str(VENV)], check=True)
        venv_py = VENV / "Scripts" / "python.exe"

        if not args.fast:
            print("安装锁定依赖（requirements-win7.txt）...")
            subprocess.run([str(venv_py), "-m", "pip", "install", "-q",
                            "--upgrade", "pip", "-i", PIP_INDEX],
                           env=PIP_ENV, check=True)
            subprocess.run([str(venv_py), "-m", "pip", "install", "-q", "-i", PIP_INDEX,
                            "-r", str(ROOT / "requirements-win7.txt")],
                           env=PIP_ENV, check=True)
            # 3.8/3.9 用 PyInstaller 5.x（6.x 引导器不再兼容 Win7）；其它版本装最新
            pin = "pyinstaller==5.13.2" if _ver(venv_py) in ("3.8", "3.9") else "pyinstaller"
            subprocess.run([str(venv_py), "-m", "pip", "install", "-q", "-i", PIP_INDEX, pin],
                           env=PIP_ENV, check=True)

    print("开始 PyInstaller 打包（onedir，输出 dist/KaiCrawl/）...")
    # PyInstaller 分析期执行的部分 hook 会 import requests：本机 paddlex 带进来的
    # chardet(7.x) 超出 requests 2.32 兼容检查范围，会在构建日志刷
    # RequestsDependencyWarning，按消息前缀压掉（对构建行为无影响）。
    env.setdefault("PYTHONWARNINGS", "ignore:urllib3")
    subprocess.run([str(venv_py), "-m", "PyInstaller",
                    "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"),
                    "--workpath", str(BUILD / "work"),
                    str(BUILD / "KaiCrawl.spec")],
                   check=True, cwd=str(ROOT), env=env)

    exe = ROOT / "dist" / "KaiCrawl" / "KaiCrawl.exe"
    if channel == "win10":
        _bundle_browsers(venv_py)
    _bundle_data()
    if args.port:
        _stamp_port(args.port)
    else:
        stale = ROOT / "dist" / "KaiCrawl" / "PORT.txt"
        if stale.is_file():
            stale.unlink()
            print(f"未指定端口，已移除上次构建的端口文件：{stale}")
    if args.version:
        _stamp_version(args.version)
    print("\n完成：", exe)
    print("绿色目录版：把 dist/KaiCrawl 整个文件夹拷到目标电脑，双击 KaiCrawl.exe 即运行；")
    print("安装包（可选）：装 Inno Setup 6 后编译 build/KaiCrawl.iss 得到单文件安装程序。")


if __name__ == "__main__":
    main()
