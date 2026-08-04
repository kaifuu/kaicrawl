# -*- coding: utf-8 -*-
"""公众号「北京东城」—— 正文可抓，列表靠「URL 导入」。

微信公众号没有公开可抓取的 Web 列表接口（历史文章仅在微信客户端内可见），
因此 fetch_list 抛错提示走 URL 导入；而单篇文章页（mp.weixin.qq.com/s/xxx）
结构统一、可稳定抓取，fetch_detail 负责解析正文。

使用流程：在微信内复制文章链接 → 在「URL 批量导入」页粘贴 → 自动生成 WORD。
"""
import re
from datetime import datetime

from bs4 import BeautifulSoup

from .base import BaseParser, ParserError
from ..utils import http_get, clean_text

_TEXT_TAGS = ("p", "section", "li", "h1", "h2", "h3", "h4", "h5", "strong", "span")


class WechatParser(BaseParser):
    key = "wechat"
    site_name = "公众号(北京东城)"

    def fetch_list(self):
        raise ParserError(
            "公众号无公开列表接口。请用「URL 批量导入」：在微信复制文章链接"
            "（mp.weixin.qq.com/s/xxx）粘贴到 URL 导入页，系统自动抓取正文。"
        )

    def fetch_detail(self, url):
        url = (url or "").strip()
        if "mp.weixin.qq.com" not in url:
            raise ParserError("仅支持微信公众号文章链接（mp.weixin.qq.com）")

        resp = http_get(url, extra_headers={"Referer": "https://mp.weixin.qq.com/"})
        if resp.status_code >= 400:
            raise ParserError(f"HTTP {resp.status_code}: {url}")
        resp.encoding = "utf-8"
        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        title = self._extract_title(soup, html)
        author = self._extract_author(soup, html)
        publish_date = self._extract_date(soup, html)

        content_el = (soup.select_one("#js_content")
                      or soup.select_one(".rich_media_content"))
        blocks = self._content_to_blocks(content_el) if content_el else []

        if not title and not blocks:
            raise ParserError(
                "未能解析正文：可能是临时链接已过期、文章被删除、或需在微信内打开。"
            )

        return {
            "title": title or "未命名",
            "author": author,
            "publish_date": publish_date,
            "blocks": blocks,
            "base_url": url,
        }

    # ---- 字段抽取 ----
    def _extract_title(self, soup, html):
        for sel in ("#activity-name", "#js_title", "h1.rich_media_title", "h1"):
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                return clean_text(el.get_text())
        m = re.search(r'"og:title"\s+content="([^"]+)"', html)
        if m:
            return clean_text(m.group(1))
        return clean_text(soup.title.get_text()) if soup.title else ""

    def _extract_author(self, soup, html):
        for sel in ("#js_name", ".profile_nickname", "a.wx_nickname",
                    "strong.profile_nickname"):
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                return clean_text(el.get_text())
        m = re.search(r'"og:nickname"\s+content="([^"]+)"', html)
        if m:
            return clean_text(m.group(1))
        return ""

    def _extract_date(self, soup, html):
        el = soup.select_one("#publish_time")
        if el and el.get_text(strip=True):
            txt = clean_text(el.get_text())
            m = re.match(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", txt)
            if m:
                return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            return txt[:10]
        # var ct = "1690000000"（unix 秒）
        m = re.search(r'var\s+ct\s*=\s*["\'](\d{10})["\']', html)
        if m:
            try:
                return datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")
            except Exception:
                pass
        m = re.search(r'(20\d{2})-(\d{1,2})-(\d{1,2})', html)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return ""

    def _content_to_blocks(self, container):
        """微信公众号正文：文本块 + 图片（data-src 懒加载）。"""
        blocks = []
        for el in container.descendants:
            name = getattr(el, "name", None)
            if not name:
                continue
            if name == "img":
                src = (el.get("data-src") or el.get("src")
                       or el.get("data-original") or "")
                if src and not src.startswith("data:"):
                    blocks.append({"type": "image",
                                   "src": self.abs_url(src.strip()),
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
