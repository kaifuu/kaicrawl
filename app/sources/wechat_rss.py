# -*- coding: utf-8 -*-
"""公众号 RSS 订阅解析器：把 RSS feed 当作「文章列表」入口。

为什么需要它：
    微信公众号没有公开 Web 列表，WechatParser.fetch_list() 无法自动发现文章。
    本解析器对接第三方「公众号转 RSS」服务（wechat2rss.xlab.app / WeWe-RSS 自部署
    / feeddd.org），用 RSS feed 作为列表来源，自动发现新文章 URL；正文抓取直接
    复用 WechatParser.fetch_detail()（mp.weixin.qq.com/s/xxx 单篇页解析已稳定）。

⚠️ 能力边界（务必知晓）：
    - RSS 只能拿到「订阅之后」发布的新文章（增量），历史文章回溯不了。
    - 只收录群发消息；非群发文章不会被 feed 收录。
    - 第三方 RSS 服务可能失效（被封 / 跑路）；已抓取的 WORD 落本地归档，永久保留。

使用方式：
    1. 在 wechat2rss / WeWe-RSS / feeddd 获取「北京东城」的 RSS feed 地址。
    2. 「数据源管理」→ 编辑「公众号(北京东城)」：
         URL 填 RSS feed 地址（https://.../feed.xml 或 .xml）
         解析器选「公众号RSS(微信)」
    3. 点「立即抓取」或新建每日定时任务，自动抓新文章生成 WORD。
"""
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .base import ParserError
from .wechat import WechatParser
from ..utils import http_get, clean_text
from config import MAX_ARTICLES_PER_RUN

# 从文本中提取微信文章永久链接（mp.weixin.qq.com/s/xxx 或 /s?__biz=...）
_WECHAT_URL_RE = re.compile(
    r"https?://mp\.weixin\.qq\.com/s[?/][^\s\"'<>]+", re.IGNORECASE
)
# 中文 / 松散日期：2025-07-02 / 2025年7月2日 / 2025.7.2
_YMD_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")


class WechatRssParser(WechatParser):
    """继承 WechatParser：fetch_detail 完全复用，仅覆盖 fetch_list 从 RSS 取列表。"""

    key = "wechat_rss"
    site_name = "公众号RSS(微信)"

    def fetch_list(self, since_date=None, limit=None):
        feed_url = (self.source.url or "").strip()
        if not feed_url.lower().startswith(("http://", "https://")):
            raise ParserError(
                "未配置 RSS feed。请在「数据源管理」编辑本来源：URL 填公众号 RSS 地址"
                "（如 wechat2rss 的 .xml），解析器选「公众号RSS(微信)」。"
            )

        resp = http_get(feed_url)
        if resp.status_code >= 400:
            raise ParserError(f"RSS 请求失败 HTTP {resp.status_code}：{feed_url}")
        resp.encoding = resp.apparent_encoding or "utf-8"

        items = self._parse_feed(resp.text)
        if not items:
            raise ParserError(
                f"RSS 未解析到任何微信文章，请检查 feed 地址：{feed_url}"
            )
        # 回溯抓取：仅保留 since_date 当天及之后的文章（无日期的保留，避免漏抓）
        if since_date:
            items = [it for it in items
                     if not it.get("publish_date") or it.get("publish_date") >= since_date]
        return items[:limit or MAX_ARTICLES_PER_RUN]

    # ---- RSS / Atom 解析 ----
    def _parse_feed(self, xml_text):
        """兼容 RSS 2.0 (<item>) 与 Atom (<entry>)。返回 [{url,title,publish_date}]。"""
        try:
            soup = BeautifulSoup(xml_text, "xml")
        except Exception:
            # RSS 偶有实体/格式瑕疵，回退到 HTML 解析（标签同名仍可取）
            soup = BeautifulSoup(xml_text, "lxml")

        nodes = soup.find_all("item") + soup.find_all("entry")
        items, seen = [], set()
        for node in nodes:
            url = self._node_link(node)
            if not url or "mp.weixin.qq.com/s" not in url.lower():
                # 兜底：从摘要 / 正文里提取微信链接
                blob = self._node_text(node, ["description", "content",
                                              "summary", "content:encoded"])
                m = _WECHAT_URL_RE.search(blob or "")
                if not m:
                    continue
                url = m.group(0)

            url = url.strip().replace("&amp;", "&")
            if url in seen:
                continue
            seen.add(url)

            title = clean_text(self._node_text(node, ["title"])) or "无标题"
            date = self._norm_date(
                self._node_text(node, ["pubDate", "published", "updated", "date"])
            )
            items.append({"url": url, "title": title, "publish_date": date})
        return items

    # ---- 节点字段抽取 ----
    @staticmethod
    def _node_text(node, names):
        """按优先级取第一个非空子标签文本。"""
        for n in names:
            child = node.find(n)
            if child and child.get_text(strip=True):
                return child.get_text(strip=True)
        return ""

    @staticmethod
    def _node_link(node):
        """取链接：RSS2.0 <link>text</link>；Atom <link href=".."/>。"""
        link = node.find("link")
        if not link:
            return ""
        href = link.get("href")        # Atom
        if href:
            return href.strip()
        return link.get_text(strip=True)   # RSS2.0

    @staticmethod
    def _norm_date(raw):
        """归一化日期为 YYYY-MM-DD。兼容 RFC822 / ISO8601 / 中文。"""
        if not raw:
            return ""
        raw = raw.strip()
        # RFC822（RSS pubDate）：Wed, 02 Jul 2025 10:00:00 +0800
        try:
            return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
        # ISO8601（Atom）：2025-07-02T10:00:00+08:00
        try:
            return datetime.fromisoformat(raw.rstrip("Z")).strftime("%Y-%m-%d")
        except ValueError:
            pass
        # 中文 / 松散
        m = _YMD_RE.search(raw)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return ""
