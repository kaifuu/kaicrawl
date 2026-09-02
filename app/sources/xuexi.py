# -*- coding: utf-8 -*-
"""学习强国 (xuexi.cn)。

学习强国为低代码平台渲染的 SPA（数据走内部 dataChannel），且列表项无
<a href>、点击经 window.open 弹出新页 —— requests 只能拿到空壳 HTML。

- render_mode=browser（推荐）：
    列表 = Playwright 渲染栏目页 + 逐项点击采集弹窗 URL（popup 方案，
    目标形如 lgpage/detail/index.html?id=xxx，公开内容无需登录）；
    详情 = 渲染文章页后按 detail-title / render-detail-* 选择器取标题正文。
- render_mode=static（默认）：best-effort，取不到列表时抛 ParserError
  提示改用 browser 模式或走手动导入，由编排层记录为 error，不影响其他来源。
"""
from .base import GenericGovParser, ParserError
from config import MAX_ARTICLES_PER_RUN, MAX_ARTICLES_BACKFILL


class XuexiParser(GenericGovParser):
    key = "xuexi"
    site_name = "学习强国"

    link_text_min_len = 6

    # 渲染后列表项容器（标题+日期两行文本）
    item_selector = ".text-link-item-title"
    # 渲染后详情页结构（低代码平台 class 名，标题容器为 render-detail-titles）
    title_selectors = [".render-detail-titles", "div[class*='detail-title']", "h1", ".title"]
    content_selectors = [
        ".render-detail-article-content", ".render-detail-content",
        ".render-detail-article", ".article-content", ".content",
    ]

    def fetch_list(self, since_date=None, limit=None):
        if (getattr(self.source, "render_mode", "static") or "static") == "browser":
            return self._fetch_list_by_clicks(since_date=since_date, limit=limit)
        items = super().fetch_list(since_date=since_date, limit=limit)
        if not items:
            name = getattr(self.source, "name", "") or ""
            url = getattr(self.source, "url", "") or ""
            raise ParserError(
                f"「{name}」为学习强国页面，JS 渲染 + 反爬，requests 无法自动获取列表与正文。"
                f"请将该来源「渲染模式」设为 browser（无头浏览器渲染 + 点击采集），"
                f"或走「手动导入」粘贴生成 WORD。来源地址：{url}"
            )
        return items[:MAX_ARTICLES_PER_RUN]

    def _fetch_list_by_clicks(self, since_date=None, limit=None):
        """浏览器渲染 + 逐项点击弹窗采集：学习强国列表项无 <a>，URL 仅在弹窗目标里。

        配置了 source.list_xpath 时点击项限定在该区域内（栏目页多栏并存时
        只采目标栏，不混入推荐位/侧栏里的同名节点）；未配置则全页按
        item_selector 扫（兼容旧行为）。
        """
        from .renderer import collect_click_links
        cap = limit or (MAX_ARTICLES_BACKFILL if since_date else MAX_ARTICLES_PER_RUN)
        lx = (getattr(self.source, "list_xpath", "") or "").strip()
        selector = (f"xpath={lx}//*[contains(@class,'text-link-item-title')]"
                    if lx else self.item_selector)
        pairs = collect_click_links(self.url, selector, max_items=cap)
        items, seen = [], set()
        for text, url in pairs:
            if not url or url in seen:
                continue
            seen.add(url)
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            title = lines[0] if lines else ""
            pub = self._norm_date(text or "")
            items.append({"url": url, "title": title, "publish_date": pub})
        if not items:
            name = getattr(self.source, "name", "") or ""
            where = "列表区域 XPath 内的列表项" if lx else f"列表项选择器（{self.item_selector}）"
            raise ParserError(
                f"「{name}」浏览器渲染后未采集到文章链接：页面结构可能已变化，"
                f"请检查{where}。来源：{self.url}"
            )
        if since_date:
            items = [it for it in items
                     if not it["publish_date"] or it["publish_date"] >= since_date]
        return items