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
import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import lxml.html
from lxml import etree as lxml_etree

from ..utils import ParserError, clean_text, http_get
from config import MAX_ARTICLES_PER_RUN, MAX_ARTICLES_BACKFILL, MAX_LIST_PAGES

_log = logging.getLogger(__name__)

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

    def fetch_list(self, since_date=None, limit=None):
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

    # ---- 通用 XPath 区域提取（source.list_xpath 配置时启用） ----
    def _page_text(self, url, *, wait_xpath=None, wait_css=None):
        """取页面 HTML：render_mode=browser 用 Playwright 渲染（SPA/反爬），否则 HTTP。

        wait_xpath / wait_css 为正文容器就绪标志（XPath 优先）：命中即返回，
        避免每篇详情都等必超时的 networkidle。
        """
        if (getattr(self.source, "render_mode", "static") or "static") == "browser":
            from .renderer import render_html
            return render_html(url, wait_xpath=wait_xpath, wait_css=wait_css)
        return self._get(url).text

    def _iter_pattern_pages(self):
        """分页 URL 序列：总是先入口页，再按 page_url_pattern 追加后续页。

        pattern 的 {page} 从 1 起（如 index_{page}.html / list_50765_{page}.html，
        可为绝对 URL 或相对入口的路径）。未配置或不含 {page} 时只返回入口页。
        与入口重复的页（如入口即第 1 页）由抓取侧 seen 去重兜住。
        """
        pat = (getattr(self.source, "page_url_pattern", "") or "").strip()
        if pat and "{page}" in pat:
            yield self.url
            full_pat = pat if "://" in pat else urljoin(self.url, pat)
            for n in range(1, MAX_LIST_PAGES + 1):
                yield full_pat.replace("{page}", str(n))
        else:
            yield self.url

    @staticmethod
    def _norm_date(text):
        """从文本提取 YYYY-MM-DD；DATE_RE 兼容 2026-08-14 / [2026年08月14日] 两种格式。"""
        m = DATE_RE.search(text or "")
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return ""

    def _xpath_item_date(self, a_el, date_xpath=""):
        """条目日期：优先 date_xpath（相对条目容器求值），否则条目容器文本启发式。"""
        container = None
        for parent in a_el.iterancestors():
            if parent.tag in ("li", "tr", "dd", "div"):
                container = parent
                break
        container = container or a_el
        if date_xpath:
            try:
                for n in container.xpath(date_xpath):
                    text = n if isinstance(n, str) else "".join(n.itertext())
                    got = self._norm_date(text)
                    if got:
                        return got
            except Exception:
                pass  # date_xpath 写错不致命，回退启发式
        return self._norm_date("".join(container.itertext()))

    def _fetch_list_by_xpath(self, since_date=None, limit=None):
        """按配置的 list_xpath 提取文章列表：新站点只需 URL + 区域 XPath，零代码接入。

        区域外的导航/分页链接天然不会进入；区域内 <a> 取标题与链接，日期按
        date_xpath 或条目容器启发式。跨页由 page_url_pattern 驱动，页序假定
        按日期降序，遇早于 since_date 的条目即收尾。
        """
        lx = (getattr(self.source, "list_xpath", "") or "").strip()
        dx = (getattr(self.source, "date_xpath", "") or "").strip()
        cap = limit or (MAX_ARTICLES_BACKFILL if since_date else MAX_ARTICLES_PER_RUN)
        items, seen, stop = [], set(), False
        page_iter = self._iter_pattern_pages()
        pages = page_iter if since_date else [next(page_iter)]
        fails = 0
        for idx, page_url in enumerate(pages):
            if stop:
                break
            try:
                text = self._page_text(page_url, wait_xpath=lx)
                fails = 0
            except ParserError:
                if idx == 0:
                    raise            # 首页失败 = 来源本身异常，上抛
                fails += 1
                if fails >= 3:
                    break            # 连续 3 页取不到 = 翻到底（容忍 index_1 这类零星缺页）
                continue
            try:
                doc = lxml.html.fromstring(text or "")
            except Exception as e:
                raise ParserError(f"列表页解析失败：{page_url} -> {e}")
            roots = [r for r in doc.xpath(lx) if hasattr(r, "tag")]
            if not roots:
                _log.warning("list_xpath 未匹配到区域：%s @ %s", lx, page_url)
            for root in roots:
                for a in root.xpath(".//a[@href]"):
                    full = self.abs_url(a.get("href") or "", base=page_url)
                    if not full:
                        continue
                    title = clean_text("".join(a.itertext()))
                    if len(title) < self.link_text_min_len:
                        continue
                    if full in seen:
                        continue
                    pub = self._xpath_item_date(a, dx)
                    if since_date and pub and pub < since_date:
                        stop = True
                        break
                    seen.add(full)
                    items.append({"url": full, "title": title, "publish_date": pub})
                    if len(items) >= cap:
                        stop = True
                        break
                if stop:
                    break
        return items

    def fetch_list(self, since_date=None, limit=None):
        """抓取列表。配置了 list_xpath 走通用区域提取，否则用启发式扫 <a>。

        since_date(YYYY-MM-DD) 非空时翻页回溯，跳过早于该日期的条目。
        页内/页间假定按日期降序：遇到首条 pub<since_date 即停止翻页。
        limit 为本次最多抓取篇数（来自界面输入）；留空则 since_date 用 MAX_ARTICLES_BACKFILL、
        否则用 MAX_ARTICLES_PER_RUN（默认 20）。since_date 为 None 时只抓首页。
        """
        if (getattr(self.source, "list_xpath", "") or "").strip():
            return self._fetch_list_by_xpath(since_date=since_date, limit=limit)
        cap = limit or (MAX_ARTICLES_BACKFILL if since_date else MAX_ARTICLES_PER_RUN)
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

    def _content_element(self, soup, resp_text):
        """定位正文容器，返回 BS4 tag 供 _content_to_blocks 处理。

        优先级：source.content_xpath（lxml 求值）> content_selectors（CSS 启发式）。
        XPath 留空或匹配为空 / 抛错时，记 warning 并回退默认选择器，
        不让一个写错的 XPath 拖垮整个来源抓取。
        """
        xpath = (getattr(self.source, "content_xpath", "") or "").strip()
        if xpath:
            try:
                doc = lxml.html.fromstring(resp_text or "")
                matches = doc.xpath(xpath)
                # xpath 可能返回元素 / 文本 / 字符串，只取元素节点
                el = next((m for m in matches if hasattr(m, "tag")), None)
                if el is not None:
                    frag = lxml_etree.tostring(el, encoding="unicode")
                    node = BeautifulSoup(frag, "lxml")
                    container = node.body or node
                    kids = container.find_all(recursive=False)
                    return kids[0] if len(kids) == 1 else container
                _log.warning("content_xpath 未匹配到元素，回退默认选择器：%s", xpath)
            except Exception as e:
                _log.warning("content_xpath 执行失败，回退默认选择器：%s -> %s", xpath, e)
        return self._select_first(soup, self.content_selectors)

    def _meta_text(self, soup, resp_text):
        """取「时间 + 来源」元信息行文本。返回 (文本, 是否 XPath 命中)。

        优先级：source.meta_xpath（lxml 求值）> author_selectors（CSS 启发式）。
        XPath 留空或匹配为空 / 抛错时，记 warning 并回退默认选择器，
        容错策略与 _content_element 的 content_xpath 一致。
        """
        xpath = (getattr(self.source, "meta_xpath", "") or "").strip()
        if xpath:
            try:
                doc = lxml.html.fromstring(resp_text or "")
                matches = doc.xpath(xpath)
                el = next((m for m in matches if hasattr(m, "tag")), None)
                if el is not None:
                    # 剔除阅读量/点击数计数节点、语音播报控件与脚本样式，避免混进来源名
                    for junk in el.xpath(
                            ".//script | .//style | .//*[contains(@class,'view')"
                            " or contains(@class,'hit') or contains(@class,'count')"
                            " or contains(@class,'click') or contains(@class,'voice')]"):
                        junk.getparent().remove(junk)
                    # 兄弟节点文本以空格连接：元信息行的各 span 间常无分隔符，
                    # text_content() 直连会把「来源：xx」「时间」「播报」粘成一串
                    text = clean_text(" ".join(t for t in el.itertext() if t.strip()))
                    if text:
                        return text, True
                    _log.warning("meta_xpath 命中的元素无文本（可能与浏览器节点口径有偏差），回退默认选择器：%s", xpath)
                else:
                    _log.warning("meta_xpath 未匹配到元素，回退默认选择器：%s", xpath)
            except Exception as e:
                _log.warning("meta_xpath 执行失败，回退默认选择器：%s -> %s", xpath, e)
        el = self._select_first(soup, self.author_selectors)
        return (clean_text(el.get_text(" ")) if el else ""), False

    def fetch_detail(self, url):
        wait_x = (getattr(self.source, "content_xpath", "") or "").strip() or None
        # 未配 content_xpath 时用首个内容选择器作就绪标志（CSS），命中即收
        wait_c = None
        if not wait_x and self.content_selectors:
            wait_c = self.content_selectors[0]
        resp_text = self._page_text(url, wait_xpath=wait_x, wait_css=wait_c)
        soup = BeautifulSoup(resp_text, "lxml")

        title_el = self._select_first(soup, self.title_selectors)
        title = clean_text(title_el.get_text()) if title_el else clean_text(soup.title.get_text() if soup.title else "")

        content_el = self._content_element(soup, resp_text)
        blocks = self._content_to_blocks(content_el, base_url=url) if content_el else []

        meta_text, meta_hit = self._meta_text(soup, resp_text)
        author = self._extract_author(meta_text)
        publish_date = self._extract_date(soup)
        if meta_hit:
            # 元信息行自带发布时间（如「日期：2026-08-12 16:28 来源：…」），比全页扫描准
            m = DATE_RE.search(meta_text)
            if m:
                y, mo, d = m.groups()
                publish_date = f"{y}-{int(mo):02d}-{int(d):02d}"

        return {
            "title": title,
            "author": author,
            "publish_date": publish_date,
            "blocks": blocks,
            "base_url": url,
        }

    def fetch_detail_custom(self, url, title_xpath="", author_xpath="",
                            date_xpath="", content_xpath=""):
        """按调用方显式给出的 XPath 解析详情页（URL 批量导入用）。

        四个 XPath 均为绝对/相对 lxml 表达式：正文区域必填，未命中抛 ParserError；
        标题/作者/日期未填或未命中时回退本解析器的启发式。正文区域命中后，
        会把标题/作者/日期命中的节点及其在正文容器内的最浅祖先（如 h1、
        元信息行 div）从正文中剔除，避免标题和元信息重复混进正文块。
        """
        if not (content_xpath or "").strip():
            raise ParserError("未填写正文区域 XPath")

        resp_text = self._page_text(url, wait_xpath=content_xpath.strip())
        doc = lxml.html.fromstring(resp_text or "")

        def _first_text(xpath):
            """首个命中元素的全文文本；未填/未命中/抛错一律返回空串走回退。"""
            xpath = (xpath or "").strip()
            if not xpath:
                return ""
            try:
                m = next((x for x in doc.xpath(xpath) if hasattr(x, "tag")), None)
            except Exception:
                return ""
            return clean_text(m.text_content()) if m is not None else ""

        title = _first_text(title_xpath)
        author = _first_text(author_xpath)
        date_raw = _first_text(date_xpath)
        publish_date = ""
        if date_raw:
            m = DATE_RE.search(date_raw)
            if m:
                y, mo, d = m.groups()
                publish_date = f"{y}-{int(mo):02d}-{int(d):02d}"

        try:
            matches = doc.xpath(content_xpath.strip())
        except Exception as e:
            raise ParserError(f"正文 XPath 执行失败：{content_xpath} -> {e}")
        el = next((x for x in matches if hasattr(x, "tag")), None)
        if el is None:
            raise ParserError(f"正文 XPath 未命中：{content_xpath}")

        # 从正文容器里剔除标题/作者/日期节点（连同其容器内最浅祖先，如 h1、元信息 div）
        drop = set()
        for xp in (title_xpath, author_xpath, date_xpath):
            xp = (xp or "").strip()
            if not xp:
                continue
            try:
                hit = [x for x in doc.xpath(xp) if hasattr(x, "tag")]
            except Exception:
                continue
            for node in hit:
                cur = node
                while cur is not None and cur is not el:
                    if cur.getparent() is el:
                        drop.add(cur)
                        break
                    cur = cur.getparent()
        for node in drop:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

        frag = lxml_etree.tostring(el, encoding="unicode")
        node = BeautifulSoup(frag, "lxml")
        container = node.body or node
        kids = container.find_all(recursive=False)
        container = kids[0] if len(kids) == 1 else container
        blocks = self._content_to_blocks(container, base_url=url) if container else []

        # 标题/作者/日期未命中 XPath 时，回退到本解析器的启发式
        if not (title or author or publish_date) or not blocks:
            soup = BeautifulSoup(resp_text or "", "lxml")
            if not title:
                t = self._select_first(soup, self.title_selectors)
                title = clean_text(t.get_text()) if t else \
                    clean_text(soup.title.get_text() if soup.title else "")
            if not author:
                meta_text, _ = self._meta_text(soup, resp_text)
                author = self._extract_author(meta_text)
            if not publish_date:
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
        """从「责任编辑：张三」「作者：李四」「来源：人民网」等抽取作者。

        文章页的 来源（如「日期：2026-08-12 16:28 来源：东城区审计局」）
        即发布单位，优先级排在责任编辑/作者之后、撰稿/编辑之前。
        """
        if not text:
            return ""
        for kw in ("责任编辑", "作者", "来源", "撰稿", "编辑"):
            m = re.search(kw + r"[：:]\s*([^\s，,。/]+)", text)
            if m:
                val = re.sub(r"\d+$", "", clean_text(m.group(1)))  # 去掉混入的阅读量/ID 尾数
                if val:
                    return val
        return ""

    def _extract_date(self, soup):
        blob = " ".join(s.get_text(" ", strip=True) for s in soup.find_all(["span", "div", "p", "em", "time"])[:60])
        m = DATE_RE.search(blob)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return ""
