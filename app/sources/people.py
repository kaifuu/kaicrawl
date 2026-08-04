# -*- coding: utf-8 -*-
"""人民网·中国共产党新闻网 (cpc.people.com.cn)。
用于「时政新闻」分类。结构标准，使用通用解析器。"""
from .base import GenericGovParser


class PeopleParser(GenericGovParser):
    key = "people"
    site_name = "人民网"

    title_selectors = ["h1", ".article-title", ".title", ".box_con h1", "#titleArea", ".show_text h1"]
    content_selectors = [
        "#rm_txt_zw", ".show_text", ".text_con",
        ".article-content", ".content", "#content", ".TRS_Editor",
        ".rm_txt_con", ".news_content", ".article", "#zoom", ".box_con",
    ]
    author_selectors = [".author", ".source", ".box_dir", ".info", ".editor", ".text_con .box_dir"]

    def is_article_link(self, a_tag, full_url):
        # 人民网文章形如 /n1/2024/.../cMMMM-0.html
        href = (a_tag.get("href") or "")
        if "people.com.cn" not in full_url:
            return False
        return "/n1/" in href or super().is_article_link(a_tag, full_url)
