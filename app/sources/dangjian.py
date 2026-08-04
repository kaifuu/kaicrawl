# -*- coding: utf-8 -*-
"""党建网 (dangjian.cn)。

用于「党建要闻」(首页) 与「学史明理 / 中共党史」(xsml 栏目) 等分类。
同一解析器，入口 URL 不同（栏目页形如 list_50765_1.html）。

⚠ 列表由前端 JS 动态注入（静态 HTML 里是空 <ul>），文章数据实际存放在
   预生成的 JS 数据文件中：
     /js/{栏目id}/mi4_page_articles_guide.js        页码 -> 子文章数据文件名
     /js/{栏目id}/mi4_sub_articles_{YYYYMMDD}.js     该页文章 JSON: var MI4_PAGE_ARTICLE = [...]
   因此 fetch_list 不再扫 HTML 的 <a>，而是直接读取这些 JS 数据文件。

⚠ 详情页正文段落用 <div>（而非 <p>）承载，故重写 _content_to_blocks。
"""
import json
import re
from urllib.parse import urlparse

from ..utils import clean_text, ParserError
from config import MAX_ARTICLES_PER_RUN, MAX_ARTICLES_BACKFILL, MAX_LIST_PAGES
from .base import GenericGovParser


class DangjianParser(GenericGovParser):
    key = "dangjian"
    site_name = "党建网"

    title_selectors = ["h1", ".article-title", ".title", ".bt", ".news_title", ".detail_title"]
    content_selectors = [
        ".article-content", ".content", "#content", ".TRS_Editor",
        ".news_content", ".detail-content", ".article", ".detail_content",
        ".text", "#font_area",
    ]
    author_selectors = [".author", ".source", ".info", ".article-info", ".pubtime"]

    def is_article_link(self, a_tag, full_url):
        href = (a_tag.get("href") or "")
        if "dangjian.cn" not in full_url:
            return False
        # 党建网文章通常为 /xxxx/xxxx/xxxxxxxx.shtml 或类似
        return bool(href.endswith((".html", ".htm", ".shtml"))) or super().is_article_link(a_tag, full_url)

    # ---------------- 工具 ----------------
    def _column_id(self):
        """从 list_50765_1.html 这类入口 URL 提取栏目 id（50765）。"""
        m = re.search(r"list_(\d+)_", self.url or "")
        return m.group(1) if m else None

    def _js_base(self):
        pr = urlparse(self.url)
        return f"{pr.scheme}://{pr.netloc}"

    @staticmethod
    def _extract_js_json(text):
        """从 `var NAME = <json>;` 形态中提取首个 JSON 对象/数组。

        guide 文件含两次赋值（PAGE_INDEX_MAP50765=PAGE_INDEX_MAP = {...}），
        故从首个 {/[ 起做括号配平（兼容字符串内括号）截取后 json.loads。
        """
        start = -1
        for i, ch in enumerate(text):
            if ch in "[{":
                start = i
                break
        if start < 0:
            return None
        close = "]" if text[start] == "[" else "}"
        depth = 0
        in_str = esc = False
        quote = ""
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote:
                    in_str = False
            else:
                if ch in ("\"", "'"):
                    in_str = True
                    quote = ch
                elif ch == text[start]:
                    depth += 1
                elif ch == close:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except Exception:
                            return None
        return None

    @staticmethod
    def _norm_date(s):
        if not s:
            return ""
        m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return ""

    # ---------------- 列表 ----------------
    def fetch_list(self, since_date=None):
        """读取党建网 JS 数据文件获取文章列表。

        无栏目 id（如首页党建要闻）时回退到父类（扫 HTML <a>）的默认实现。
        """
        cid = self._column_id()
        if not cid:
            return super().fetch_list(since_date)

        base = self._js_base()
        cap = MAX_ARTICLES_BACKFILL if since_date else MAX_ARTICLES_PER_RUN

        # 1) 页码 -> 子文章数据文件名
        try:
            guide = self._get(f"{base}/js/{cid}/mi4_page_articles_guide.js").text
        except ParserError:
            # guide 取不到，说明该栏目不支持 JS 数据模式，回退默认
            return super().fetch_list(since_date)
        page_map = self._extract_js_json(guide) or {}
        page_nums = sorted((int(k) for k in page_map if str(k).isdigit()))

        items, seen, stop = [], set(), False
        for idx, pno in enumerate(page_nums):
            if stop or len(items) >= cap:
                break
            if since_date and idx >= MAX_LIST_PAGES:
                break
            fname = page_map.get(str(pno))
            if not fname:
                continue
            try:
                raw = self._get(f"{base}/js/{cid}/{fname}").text
            except ParserError:
                continue  # 单页缺失视为翻到底，跳过
            arts = self._extract_js_json(raw)
            if not isinstance(arts, list):
                continue
            for a in arts:
                url = a.get("url") or a.get("external_link") or ""
                title = clean_text(a.get("title") or "")
                if not url or len(title) < self.link_text_min_len or url in seen:
                    continue
                pub = self._norm_date(a.get("pub_date") or "")
                # 假定页码按日期降序：早于起始日期则收尾
                if since_date and pub and pub < since_date:
                    stop = True
                    break
                seen.add(url)
                items.append({"url": url, "title": title, "publish_date": pub})
                if len(items) >= cap:
                    stop = True
                    break
        return items

    # ---------------- 详情正文 ----------------
    def _content_to_blocks(self, container, base_url=None):
        """党建网正文段落用 <div>（非 <p>）承载：按容器直接子节点逐段提取文本/图片。"""
        if container is None:
            return []
        blocks, prev = [], None
        direct = container.find_all(recursive=False)
        nodes = direct if direct else list(container.descendants)
        for el in nodes:
            name = getattr(el, "name", None)
            if not name:
                continue
            # 图片：节点本身是 img，或其后代含 img
            imgs = [el] if name == "img" else el.find_all("img")
            for im in imgs:
                src = im.get("src") or im.get("data-src") or im.get("orgsrc") or ""
                if src:
                    blocks.append({"type": "image",
                                   "src": self.abs_url(src.strip(), base=base_url),
                                   "alt": (im.get("alt") or "").strip()})
                    prev = None
            text = clean_text(el.get_text())
            if text and text != prev:
                blocks.append({"type": "text", "data": text})
                prev = text
        return blocks
