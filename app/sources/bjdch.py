# -*- coding: utf-8 -*-
"""北京东城区人民政府 - 要闻动态 (bjdch.gov.cn/ywdt/)。
用于「领导活动」「单位动态」分类（入口相同，分类不同）。"""
from .base import GenericGovParser, DATE_RE
from config import MAX_LIST_PAGES


class BjdchParser(GenericGovParser):
    key = "bjdch"
    site_name = "北京东城政府网"

    title_selectors = [
        "h1", ".article-title", ".bt", ".title", ".news_title",
        ".pageTitle", "#u1", ".content_title",
    ]
    content_selectors = [
        ".article-content", ".content", "#content", ".TRS_Editor",
        ".news_content", ".detail-content", ".article", "#zoom",
        ".view", ".main-text", ".txt",
    ]
    author_selectors = [".author", ".source", ".info", ".article-info", ".pubtime"]

    def list_page_urls(self):
        """bjdch 列表翻页：第1页=入口 URL，第2页起=目录下 index_N.html。"""
        yield self.url
        base = self.url if self.url.endswith("/") else self.url + "/"
        for n in range(1, MAX_LIST_PAGES + 1):
            yield f"{base}index_{n}.html"

    def _extract_item_date(self, a_tag):
        """优先读 <a> 的 <span> 兄弟（bjdch 列表结构：<a>标题</a><span>日期</span>）。

        避免标题里含日期（如「2026王府井论坛将于7月9日…」）被 DATE_RE 误匹配。
        """
        span = a_tag.find_next_sibling("span")
        if span:
            m = DATE_RE.search(span.get_text(strip=True))
            if m:
                y, mo, d = m.groups()
                return f"{y}-{int(mo):02d}-{int(d):02d}"
        return super()._extract_item_date(a_tag)
