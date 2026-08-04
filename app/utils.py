# -*- coding: utf-8 -*-
"""通用工具：HTTP 抓取、文件名清洗、HTML 文本处理。"""
import os
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from config import DEFAULT_UA, REQUEST_TIMEOUT


class ParserError(Exception):
    """解析器层面的错误（如反爬拦截、结构变化），由编排层捕获记录。"""


def http_get(url, *, params=None, extra_headers=None, retries=2):
    """统一 GET：带浏览器 UA、超时、自动解码回退、简单重试。"""
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url, params=params, headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            # 优先用服务器声明，再尝试 utf-8 / gbk 回退
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding
            return resp
        except requests.RequestException as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise ParserError(f"请求失败：{url} -> {last_err}")


def safe_filename(name, max_len=80):
    """把标题清洗成合法、不含非法字符的文件名。"""
    if not name:
        return "untitled"
    # 去掉控制字符与 Windows 文件名非法字符
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        name = name[:max_len].strip()
    return name or "untitled"


def unique_path(dir_path, base_name, ext):
    """生成不冲突的完整路径：同名则追加 _2、_3。"""
    os.makedirs(dir_path, exist_ok=True)
    candidate = os.path.join(dir_path, f"{base_name}{ext}")
    idx = 2
    while os.path.exists(candidate):
        candidate = os.path.join(dir_path, f"{base_name}_{idx}{ext}")
        idx += 1
    return candidate


def html_to_blocks(html_snippet, base_url=None):
    """把一段正文 HTML 转成有序块列表，保留图片位置。

    返回 [{"type": "text", "data": "..."} | {"type": "image", "src": "...", "alt": ""}]
    """
    soup = BeautifulSoup(html_snippet or "", "lxml")
    blocks = []
    for el in soup.descendants:
        name = getattr(el, "name", None)
        if name == "img":
            src = el.get("src") or el.get("data-src") or ""
            if src:
                blocks.append({"type": "image", "src": src.strip(), "alt": (el.get("alt") or "").strip()})
        elif name in ("p", "div", "span"):
            text = el.get_text(strip=True)
            # 只对叶子文本块收集，避免重复累加父节点整段
            if text and not any(
                getattr(c, "name", None) in ("p", "div", "img") for c in el.children
            ):
                if not blocks or blocks[-1]["type"] != "text" or blocks[-1]["data"] != text:
                    blocks.append({"type": "text", "data": text})
    # 去重相邻完全相同的文本块
    deduped = []
    prev = None
    for b in blocks:
        key = (b["type"], b.get("data", b.get("src", "")))
        if key != prev:
            deduped.append(b)
            prev = key
    return deduped


def clean_text(text):
    """规整空白：合并空格、去除首尾空白。"""
    if not text:
        return ""
    return re.sub(r"[ \t　]+", " ", text).strip()
