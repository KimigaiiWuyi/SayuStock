"""默认 MarketDataPort 注册表（测试可 set_market 注入）。"""

from __future__ import annotations

from .port import MarketDataPort

_provider: MarketDataPort | None = None


def get_market() -> MarketDataPort:
    global _provider
    if _provider is None:
        from .facade import build_default_market

        _provider = build_default_market()
    return _provider


def set_market(provider: MarketDataPort | None) -> None:
    """注入或清空默认 provider；传 None 下次 get_market 会重建默认装配。"""
    global _provider
    _provider = provider
