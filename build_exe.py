# -*- coding: utf-8 -*-
"""发布打包：把整个工程打成可在 Win7 安装运行的 EXE（onedir 版）。

用法：
    python build_exe.py                 # 自动找 Python 3.8/3.9（Win7 兼容工具链）
    python build_exe.py --python "C:\\Python38\\python.exe"
    python build_exe.py --fast          # 复用已有构建环境，跳过依赖安装
    python build_exe.py --smoke         # 用当前 Python 打包（仅验证打包链路，产物不含 Win7 保证）

前提（正式发布）：
    本机装一个 Python 3.8.10（Win7 支持的最后主线版本，勾选 pip），
    或设环境变量 KAI_WIN7_PYTHON 指向其 python.exe。
    脚本会用它在 build/venv-win7 建独立环境，按 requirements-win7.txt
    装锁定版本依赖 + PyInstaller，再产出 dist/KaiCrawl/KaiCrawl.exe。

产出：
    dist/KaiCrawl/            绿色目录版（拷到 Win7 电脑即可运行）
    dist/KaiCrawl/ + Inno Setup 编译 build/KaiCrawl.iss 可得安装包（可选）
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
    ap = argparse.ArgumentParser(description="打包 KaiCrawl 为 Win7 可运行的 EXE")
    ap.add_argument("--python", help="指定 Win7 兼容 Python(3.8/3.9) 的 python.exe 路径")
    ap.add_argument("--fast", action="store_true", help="复用 build/venv-win7，跳过依赖安装")
    ap.add_argument("--smoke", action="store_true", help="用当前解释器打包（验证链路用，产物无 Win7 保证）")
    ap.add_argument("--version", default="", help="发布版本号（写入产物 VERSION.txt 并同步 Inno Setup 脚本）")
    args = ap.parse_args()

    if args.smoke:
        py = Path(sys.executable)
        venv_py = py
        print(f"[smoke] 用当前解释器 {_ver(py)} 打包（产物无 Win7 保证）")
    else:
        toolchain = find_win7_python(args.python)
        if not toolchain:
            print("未找到 Python 3.8/3.9（Win7 兼容工具链）。")
            print("请安装 Python 3.8.10：https://www.python.org/downloads/release/python-3810/")
            print("或用 --python / 环境变量 KAI_WIN7_PYTHON 指定其路径；")
            print("或先 --smoke 验证打包链路。")
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
    subprocess.run([str(venv_py), "-m", "PyInstaller",
                    "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"),
                    "--workpath", str(BUILD / "work"),
                    str(BUILD / "KaiCrawl.spec")], check=True, cwd=str(ROOT))

    exe = ROOT / "dist" / "KaiCrawl" / "KaiCrawl.exe"
    if args.version:
        _stamp_version(args.version)
    print("\n完成：", exe)
    print("绿色目录版：把 dist/KaiCrawl 整个文件夹拷到 Win7 电脑，双击 KaiCrawl.exe 即运行；")
    print("安装包（可选）：装 Inno Setup 6 后编译 build/KaiCrawl.iss 得到单文件安装程序。")


if __name__ == "__main__":
    main()
