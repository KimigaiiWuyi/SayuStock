"""默认 NewsPort。测试可 set_news_port 注入。"""

from __future__ import annotations

from .port import NewsPort

_port: NewsPort | None = None


def get_news_port() -> NewsPort:
    global _port
    if _port is None:
        from .fallback import build_default_news

        _port = build_default_news()
    return _port


def set_news_port(port: NewsPort | None) -> None:
    """注入或清空默认 port。传 None 时下次 get_news_port 会重建。"""
    global _port
    _port = port
