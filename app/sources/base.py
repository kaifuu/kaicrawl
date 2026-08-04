# -*- coding: utf-8 -*-
"""解析器基类与通用政府/党建网站解析器。

每个数据源对应一个解析器插件，实现：
  - fetch_list()   -> [{"url","title","publish_date?"}, ...]
  - fetch_detail() -> {"title","author","publish_date","blocks":[{"type":"text"/"image",...}]}
blocks 保持原文顺序，图片作为占位块插入，保证 WORD 中图片位置与原文一致。

GenericGovParser 提供基于「CSS 选择器 + 启发式」的默认实现，子站点只需覆盖
is_article_link / 标题/正文/作者选择器即可；结构变化时调整局限于单文件。
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..utils import ParserError, clean_text, http_get
from config import MAX_ARTICLES_PER_RUN, MAX_ARTICLES_BACKFILL, MAX_LIST_PAGES

DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")

# 视为块级文本的标签
_TEXT_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "strong")


class BaseParser:
    key = "base"
    site_name = ""

    def __init__(self, source):
        self.source = source
        self.url = source.url

    # ---- 供子类使用的辅助 ----
    def _get(self, url, **kwargs):
        resp = http_get(url, **kwargs)
        if resp.status_code >= 400:
            raise ParserError(f"HTTP {resp.status_code}: {url}")
        return resp

    def abs_url(self, link, base=None):
        if not link:
            return ""
        link = link.strip()
        if link.startswith("//"):
            return "http:" + link
        if link.startswith(("javascript:", "#", "mailto:")):
            return ""
        return urljoin(base or self.url, link)

    def same_host(self, link):
        try:
            return urlparse(link).netloc == urlparse(self.url).netloc
        except Exception:
            return False

    def fetch_list(self):
        raise NotImplementedError

    def fetch_detail(self, url):
        raise NotImplementedError


class GenericGovParser(BaseParser):
    """默认实现：抓取本站正文类链接 + 标准详情页。子类按需覆盖选择器。"""

    # 列表页：哪些 <a> 视为文章链接
    link_text_min_len = 8
    # 详情页选择器（按优先级尝试）
    title_selectors = ["h1", ".article-title", ".title", ".bt", "#title", ".news_title"]
    content_selectors = [
        ".article-content", ".content", "#content", ".TRS_Editor",
        ".news_content", ".detail-content", ".article", "#zoom",
        ".pages_content", ".text", ".main-content",
    ]
    author_selectors = [
        ".author", ".article-author", ".source", ".info", ".meta",
        ".article-info", ".news_info",
    ]

    # ---- 列表 ----
    def is_article_link(self, a_tag, full_url):
        href = (a_tag.get("href") or "").lower()
        if not self.same_host(full_url):
            return False
        # 排除明显非正文：导航、分页、索引
        if re.search(r"(index|list|column|channel|home|nav)", href):
            return False
        # 正文链接通常含日期目录/数字 id/常见后缀
        return bool(
            re.search(r"(20\d{2}|\d{3,}|\.s?html?$|/\d+_\d+|/art/|/n\d|/c\d)", href)
        )

    def list_page_urls(self):
        """生成需要抓取的列表页 URL 序列。默认只抓首页（self.url）。

        支持翻页的子站点覆盖此方法，按日期降序 yield 后续页（如 index_N.html）。
        """
        yield self.url

    def _extract_item_date(self, a_tag):
        """从列表项的 <a> 附近抽取发布日期，返回 YYYY-MM-DD 或 ""。

        默认启发式：取 <a> 最近容器（li/tr/dd/div）的文本，用 DATE_RE 匹配。
        子类可按站点结构精确读取（如 bjdch 的 <span> 兄弟），避免标题内日期误匹配。
        """
        container = a_tag.find_parent(["li", "tr", "dd", "div"]) or a_tag.parent
        blob = container.get_text(" ", strip=True) if container else ""
        m = DATE_RE.search(blob)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return ""

    def fetch_list(self, since_date=None):
        """抓取列表。since_date(YYYY-MM-DD) 非空时翻页回溯，跳过早于该日期的条目。

        页内/页间假定按日期降序：遇到首条 pub<since_date 即停止翻页。
        since_date 为 None 时只抓首页、受 MAX_ARTICLES_PER_RUN 约束（旧行为不变）。
        """
        cap = MAX_ARTICLES_BACKFILL if since_date else MAX_ARTICLES_PER_RUN
        items, seen, stop = [], set(), False
        # 不指定日期只抓首页（保持旧行为）；指定日期才翻页回溯
        page_iter = self.list_page_urls()
        pages = page_iter if since_date else [next(page_iter)]
        for idx, page_url in enumerate(pages):
            if stop:
                break
            try:
                resp = self._get(page_url)
            except ParserError:
                # 首页失败=来源本身异常，照常上抛；后续页缺失=翻到底
                if idx == 0:
                    raise
                break
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                full = self.abs_url(a["href"])
                if not full or not self.is_article_link(a, full):
                    continue
                title = clean_text(a.get_text())
                if len(title) < self.link_text_min_len:
                    continue
                if full in seen:
                    continue
                pub = self._extract_item_date(a)
                # 降序：首条早于起始日期 -> 收尾（不收本条，也不续翻）
                if since_date and pub and pub < since_date:
                    stop = True
                    break
                seen.add(full)
                items.append({"url": full, "title": title, "publish_date": pub})
                if len(items) >= cap:
                    stop = True
                    break
        return items

    # ---- 详情 ----
    def _select_first(self, soup, selectors):
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el
        return None

    def fetch_detail(self, url):
        resp = self._get(url)
        soup = BeautifulSoup(resp.text, "lxml")

        title_el = self._select_first(soup, self.title_selectors)
        title = clean_text(title_el.get_text()) if title_el else clean_text(soup.title.get_text() if soup.title else "")

        content_el = self._select_first(soup, self.content_selectors)
        blocks = self._content_to_blocks(content_el, base_url=url) if content_el else []

        author = ""
        author_el = self._select_first(soup, self.author_selectors)
        if author_el:
            author = self._extract_author(author_el.get_text())
        publish_date = self._extract_date(soup)

        return {
            "title": title,
            "author": author,
            "publish_date": publish_date,
            "blocks": blocks,
            "base_url": url,
        }

    def _content_to_blocks(self, container, base_url=None):
        """按文档顺序提取正文文本块与图片。

        base_url 为详情页 URL，用于把相对图片地址解析成绝对地址。详情页图片通常与
        文章 HTML 同目录，必须按详情 URL 解析；若按来源列表 URL 解析会丢目录段导致 404。
        """
        blocks = []
        for el in container.descendants:
            name = getattr(el, "name", None)
            if not name:
                continue
            if name == "img":
                src = el.get("src") or el.get("data-src") or el.get("orgsrc") or ""
                if src:
                    blocks.append({"type": "image", "src": self.abs_url(src.strip(), base=base_url),
                                   "alt": (el.get("alt") or "").strip()})
            elif name in _TEXT_TAGS:
                text = clean_text(el.get_text())
                if text:
                    blocks.append({"type": "text", "data": text})
        # 相邻相同文本去重
        result, prev = [], None
        for b in blocks:
            if b["type"] == "text":
                if b["data"] == prev:
                    continue
                prev = b["data"]
            else:
                prev = None
            result.append(b)
        return result

    def _extract_author(self, text):
        """从「责任编辑：张三」「作者：李四」「来源：人民网」等抽取作者。"""
        if not text:
            return ""
        for kw in ("责任编辑", "作者", "撰稿", "编辑"):
            m = re.search(kw + r"[：:]\s*([^\s，,。/]+)", text)
            if m:
                return clean_text(m.group(1))
        return ""

    def _extract_date(self, soup):
        blob = " ".join(s.get_text(" ", strip=True) for s in soup.find_all(["span", "div", "p", "em", "time"])[:60])
        m = DATE_RE.search(blob)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return ""
