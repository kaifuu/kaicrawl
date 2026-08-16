# -*- coding: utf-8 -*-
"""从《爬虫数据来源.xlsx》导入数据源。

Excel 结构（Sheet1）：
  列：序号 | 新闻(分类) | 来源(名称+URL 混写) | 备注 | 格式
  每个分类在第一行给出序号+新闻，其后若干行（序号为空）同属该分类。
本模块把每行解析成 (category, name, url) 三元组，并按域名指派 parser_key。
"""
import re
from urllib.parse import urlparse

import openpyxl

from .extensions import db
from .models import Source

URL_RE = re.compile(r"https?://[^\s）)]+", re.IGNORECASE)


def parse_source_cell(text, fallback_name=None):
    """从来源单元格解析 (name, url, source_type, parser_key)。

    name 优先级：fallback_name（「栏目」列）> 单元格去掉 URL 后的剩余文字 > "未命名"。
    兼容两种 Excel 写法：「栏目列 + 纯 URL」与旧式「名称(URL) 混写」。
    """
    text = (text or "").strip()
    url = ""
    m = URL_RE.search(text)
    if m:
        url = m.group(0).rstrip(").，。；;")

    # 公众号：无 URL，特殊处理
    if "公众号" in text:
        # 如「公众号（北京东城）」
        acc = re.sub(r"^公众号", "", text)
        acc = acc.replace("（", "").replace("）", "").replace("(", "").replace(")", "").strip()
        name = f"公众号({acc})" if acc else "公众号"
        return name, "", "wechat", "wechat"

    # 名称：优先用「栏目」列；否则从来源单元格去掉 URL 后取剩余文字
    name = (fallback_name or "").strip()
    if not name:
        name_part = text.replace(url, "") if url else text
        name_part = re.sub(r"[（()）\s]", "", name_part)
        name = name_part or "未命名"

    source_type, parser_key = infer_parser(url)
    return name, url, source_type, parser_key


def infer_parser(url):
    """根据 URL 域名推断 source_type 与 parser_key。"""
    if not url:
        return "website", "bjdch"
    host = urlparse(url).netloc.lower()
    if "bjdch.gov.cn" in host:
        return "website", "bjdch"
    if "people.com.cn" in host:
        return "website", "people"
    if "dangjian.cn" in host:
        return "website", "dangjian"
    if "xuexi.cn" in host:
        return "xuexi", "xuexi"
    return "website", "bjdch"


def read_sources_from_excel(path):
    """读取 Excel，返回 [{category,name,url,source_type,parser_key,remark}, ...]。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # 定位表头列：新闻(分类) / 栏目(来源名) / 来源(URL) / 备注 / 抓取配置（可选新列）
    header = rows[0]
    col_idx = {str(h or "").strip(): i for i, h in enumerate(header)}
    i_cat = col_idx.get("新闻") or col_idx.get("分类") or 1
    i_col = col_idx.get("栏目")                 # 栏目列：作为来源名称（可选）
    i_src = col_idx.get("来源") or 2
    i_remark = col_idx.get("备注")              # 备注列（无则为 None，不再误取来源列）
    i_lx = col_idx.get("列表区域XPath")          # 列表页文章区域 XPath（可选）
    i_pg = col_idx.get("分页URL模板")            # 翻页 URL 模板（可选）
    i_rm = col_idx.get("渲染模式")               # static / browser（可选）

    def _cell(row, idx):
        return row[idx] if idx is not None and idx < len(row) else None

    result = []
    current_category = ""
    for row in rows[1:]:
        if row is None:
            continue
        cat = _cell(row, i_cat)
        col = _cell(row, i_col)
        src = _cell(row, i_src)
        remark = _cell(row, i_remark)
        if cat:
            current_category = str(cat).strip()
        if not src:
            continue
        name, url, stype, pkey = parse_source_cell(str(src), fallback_name=str(col or "").strip())
        result.append({
            "category": current_category,
            "name": name,
            "url": url,
            "source_type": stype,
            "parser_key": pkey,
            "remark": str(remark or "").strip(),
            "list_xpath": str(_cell(row, i_lx) or "").strip(),
            "page_url_pattern": str(_cell(row, i_pg) or "").strip(),
            "render_mode": str(_cell(row, i_rm) or "").strip(),
        })
    return result


def import_from_excel(path, *, replace=False):
    """把 Excel 数据源导入数据库。replace=True 时先清空再导入。返回导入条数。"""
    records = read_sources_from_excel(path)
    if replace:
        Source.query.delete()
        db.session.commit()

    existing = {(s.category, s.name, s.url): s for s in Source.query.all()}
    count = 0
    for r in records:
        key = (r["category"], r["name"], r["url"])
        if key in existing:
            # 更新可变字段
            s = existing[key]
            s.source_type = r["source_type"]
            s.parser_key = r["parser_key"]
            if r["remark"]:
                s.remark = r["remark"]
            # 抓取配置：Excel 填写了才覆盖（留空=保留界面手工调整值）
            if r["list_xpath"]:
                s.list_xpath = r["list_xpath"]
            if r["page_url_pattern"]:
                s.page_url_pattern = r["page_url_pattern"]
            if r["render_mode"]:
                s.render_mode = r["render_mode"]
        else:
            author_policy = "各单位" if r["category"] == "单位动态" else ""
            s = Source(
                category=r["category"], name=r["name"], url=r["url"],
                source_type=r["source_type"], parser_key=r["parser_key"],
                author_policy=author_policy, enabled=True, remark=r["remark"],
                list_xpath=r["list_xpath"], page_url_pattern=r["page_url_pattern"],
                render_mode=r["render_mode"] or "static",
            )
            db.session.add(s)
            existing[key] = s
        count += 1
    db.session.commit()
    return count


def seed_if_empty(path):
    """仅在数据源表为空时执行一次种子导入。"""
    if Source.query.count() == 0:
        return import_from_excel(path, replace=False)
    return 0
