# -*- coding: utf-8 -*-
"""学习强国 (xuexi.cn) —— 尽力而为（best-effort）。

注意：学习强国站点重度依赖 JS 渲染且有反爬，公开页面通常无法用 requests 直接
拿到文章列表。这里提供基于通用解析器的尝试；若取不到列表，会抛出 ParserError，
由编排层记录为 error，不影响其他来源。
架构上可后续替换为：带 cookie 的接口抓取 / Selenium / 手动导入。
"""
from .base import GenericGovParser, ParserError
from config import MAX_ARTICLES_PER_RUN


class XuexiParser(GenericGovParser):
    key = "xuexi"
    site_name = "学习强国"

    link_text_min_len = 6

    def fetch_list(self):
        items = super().fetch_list()
        if not items:
            name = getattr(self.source, "name", "") or ""
            url = getattr(self.source, "url", "") or ""
            raise ParserError(
                f"「{name}」为学习强国页面，JS 渲染 + 反爬，requests 无法自动获取列表与正文。"
                f"请走「手动导入」：浏览器打开该来源 → 复制文章标题/作者/正文 → "
                f"在「手动导入」页粘贴生成 WORD。来源地址：{url}"
            )
        return items[:MAX_ARTICLES_PER_RUN]
