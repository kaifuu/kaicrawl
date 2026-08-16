# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：python build_exe.py 会用本 spec 构建。
# onedir（目录版）而非 onefile：Win7 老机器上启动快、误报少、便于带 data/ 升级。
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent   # 项目根（spec 位于 build/ 下）

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
        # Win7 包不需要 playwright（Chromium 不支持 Win7）；tkinter/测试框架瘦身
        "playwright", "tkinter", "pytest", "setuptools", "pip",
    ],
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
