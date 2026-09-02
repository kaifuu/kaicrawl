# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：python build_exe.py 会用本 spec 构建。
# onedir（目录版）而非 onefile：老机器上启动快、误报少、便于带 data/ 升级。
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent   # 项目根（spec 位于 build/ 下）

# win10 渠道（build_exe.py 设 KAI_WITH_PLAYWRIGHT=1）保留 playwright——其官方
# PyInstaller hook 自动收集 node 驱动；Win7/smoke 包剔除（Chromium 不支持 Win7）。
# 浏览器二进制不进 PYZ：win10 构建后由 build_exe.py 拷到产物 ms-playwright\。
_WITH_PW = bool(os.environ.get("KAI_WITH_PLAYWRIGHT"))

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
        (str(ROOT / "爬虫数据来源.xlsx"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    excludes=[
        # playwright 按渠道保留/剔除；tkinter/测试框架瘦身；
        # chardet 是本机 paddlex 带进来的（版本超出 requests 兼容检查范围会刷
        # RequestsDependencyWarning），应用统一用 charset_normalizer，剔除以免随包
    ] + ([] if _WITH_PW else ["playwright"]) + ["tkinter", "pytest", "setuptools", "pip", "chardet"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KaiCrawl",
    debug=False,
    strip=False,
    upx=False,          # 老系统上 UPX 壳易被杀软误杀，不压
    console=True,       # 服务型程序：保留控制台窗口看日志，关窗即停
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="KaiCrawl",
)
