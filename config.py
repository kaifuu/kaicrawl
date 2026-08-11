# -*- coding: utf-8 -*-
"""全局配置：所有路径基于 BASE_DIR 相对计算，保证可移植。"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据目录（SQLite + WORD 输出）
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
DB_PATH = os.path.join(DATA_DIR, "crawler.db")
DB_URI = "sqlite:///" + DB_PATH.replace("\\", "/")

# 数据来源 Excel（启动时若库为空则自动种子导入）
EXCEL_PATH = os.path.join(BASE_DIR, "爬虫数据来源.xlsx")

# 抓取相关
REQUEST_TIMEOUT = 20          # 秒
REQUEST_DELAY = 0.8           # 每篇文章之间的间隔，礼貌抓取
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 每次抓取列表最多处理多少篇文章（防止一次跑过久）
# 此为默认上限；界面的「抓取」可在 limit 输入框临时覆盖（留空则用此默认）
MAX_ARTICLES_PER_RUN = 20

# 回溯抓取（指定起始日期时）：最多翻多少页 / 单次最多抓多少篇（日期停止通常先触发）
MAX_LIST_PAGES = 50
MAX_ARTICLES_BACKFILL = 500

# 服务
HOST = "127.0.0.1"
PORT = 5000
DEBUG = False

# WORD 字体规范
FONT_NAME = "宋体"
FONT_SIZE_PT = 10.5   # 五号
