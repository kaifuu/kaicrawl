# -*- coding: utf-8 -*-
"""入口：创建应用 + 启动调度器 + 提供服务。

优先用 waitress（Windows 友好、生产可用），未安装则回退到 Flask 开发服务器。
    python run.py
然后访问 http://127.0.0.1:5000

PyInstaller 打包（frozen）后：数据存放在 EXE 同目录的 data/ 下；
服务就绪后自动打开默认浏览器，双击 EXE 即用。
"""
import sys
import threading
import webbrowser

from app import create_app
from config import HOST, PORT, DEBUG


def _open_browser_later():
    """服务启动后自动打开浏览器（仅打包版；本地开发不弹窗打扰）。"""
    def _open():
        import time
        time.sleep(1.2)
        webbrowser.open(f"http://{HOST}:{PORT}")
    threading.Thread(target=_open, daemon=True).start()


def main():
    app = create_app(start_sched=True)
    if getattr(sys, "frozen", False):
        _open_browser_later()
    try:
        from waitress import serve
        app.logger.info("waitress 提供服务：http://%s:%d", HOST, PORT)
        serve(app, host=HOST, port=PORT)
    except ImportError:
        app.logger.warning("未安装 waitress，回退到 Flask 开发服务器")
        app.run(host=HOST, port=PORT, debug=DEBUG)


if __name__ == "__main__":
    main()
