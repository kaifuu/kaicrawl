# -*- coding: utf-8 -*-
"""解析器注册表：parser_key -> 解析器类。

新增数据源只需：1) 写一个继承 BaseParser/GenericGovParser 的类；2) 在此注册。
"""
from .base import BaseParser, GenericGovParser, ParserError
from .bjdch import BjdchParser
from .people import PeopleParser
from .dangjian import DangjianParser
from .xuexi import XuexiParser
from .wechat import WechatParser
from .wechat_rss import WechatRssParser

PARSER_REGISTRY = {
    BjdchParser.key: BjdchParser,
    PeopleParser.key: PeopleParser,
    DangjianParser.key: DangjianParser,
    XuexiParser.key: XuexiParser,
    WechatParser.key: WechatParser,
    WechatRssParser.key: WechatRssParser,
}


def get_parser(source):
    """根据 Source.parser_key 取解析器实例；未注册则回退到通用解析器。"""
    cls = PARSER_REGISTRY.get(source.parser_key, GenericGovParser)
    return cls(source)


__all__ = [
    "PARSER_REGISTRY", "get_parser", "ParserError",
    "BaseParser", "GenericGovParser",
]
