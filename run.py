# -*- coding: utf-8 -*-
"""入口：创建应用 + 启动调度器 + 提供服务。

优先用 waitress（Windows 友好、生产可用），未安装则回退到 Flask 开发服务器。
    python run.py
然后访问 http://127.0.0.1:5000
"""
from app import create_app
from config import HOST, PORT, DEBUG


def main():
    app = create_app(start_sched=True)
    try:
        from waitress import serve
        app.logger.info("waitress 提供服务：http://%s:%d", HOST, PORT)
        serve(app, host=HOST, port=PORT)
    except ImportError:
        app.logger.warning("未安装 waitress，回退到 Flask 开发服务器")
        app.run(host=HOST, port=PORT, debug=DEBUG)


if __name__ == "__main__":
    main()
