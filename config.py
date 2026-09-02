# -*- coding: utf-8 -*-
"""全局配置：所有路径基于 BASE_DIR 相对计算，保证可移植。"""
import os
import sys

# PyInstaller 打包后（frozen）：
#   BASE_DIR     = EXE 所在目录 —— 数据库 / WORD 输出等可写数据放这里，升级安装不丢
#   RESOURCE_DIR = 解包资源目录（_MEIPASS）—— 模板 / 静态资源 / 种子 Excel 等只读资源
# 源码运行时两者同为项目根目录。
_FROZEN = getattr(sys, "frozen", False)
if _FROZEN:
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = BASE_DIR

# 数据目录（SQLite + WORD 输出）
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
DB_PATH = os.path.join(DATA_DIR, "crawler.db")
DB_URI = "sqlite:///" + DB_PATH.replace("\\", "/")

# 数据来源 Excel（启动时若库为空则自动种子导入）：打包后在资源目录，源码运行在项目根
EXCEL_PATH = os.path.join(RESOURCE_DIR, "爬虫数据来源.xlsx")

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

# 并发加速（渲染/详情/图片三级流水线）
RENDER_WORKERS = 3       # Playwright 渲染线程上限：每线程独占一个 Chromium，懒启动按需增长
DETAIL_PREFETCH = 3      # 详情页滑动窗口并发预取数（1 = 退回纯串行）
IMAGE_WORKERS = 4        # WORD 生成时图片并发下载线程数

# 服务
HOST = "127.0.0.1"
PORT = 5000

# 启动端口覆盖：EXE 同目录（源码运行时为项目根）存在 PORT.txt 且内容为合法端口
# (1-65535) 时以它为准。「发布管理」构建时按所选端口写入产物；部署后直接改该
# 文件即可换端口，无需重新打包。文件缺失/内容非法时保持上面的默认值。
try:
    with open(os.path.join(BASE_DIR, "PORT.txt"), encoding="ascii") as _pf:
        _port_override = int(_pf.read().strip())
    if 1 <= _port_override <= 65535:
        PORT = _port_override
except Exception:
    pass

DEBUG = False

# 应用版本（发布管理页的基线版本号；每次网页端构建可递增）
APP_VERSION = "1.0.0"

# WORD 字体规范
FONT_NAME = "宋体"
FONT_SIZE_PT = 10.5   # 五号

# WORD 插图尺寸：相对单页可用区域的比例（宽×高），等比缩放、只缩不放
IMAGE_WIDTH_RATIO = 0.65    # 单图宽度 ≤ 页面可用宽度的 65%
IMAGE_HEIGHT_RATIO = 0.55   # 单图高度 ≤ 页面可用高度的 55%，避免长截图占满整页
