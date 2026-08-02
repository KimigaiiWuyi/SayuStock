"""默认装配与高层便捷 API。"""

from __future__ import annotations

from .port import MarketDataPort
from .enums import BoardKind, ValueKind, KlinePeriod
from .errors import MarketError, is_market_error
from .models import (
    Quote,
    SymbolRef,
    BreadthBar,
    KlineSeries,
    ValueSeries,
    BoardSnapshot,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)
from .adapters.composite import CompositeMarketData
from .adapters.okx.provider import OkxMarketData
from .adapters.vix.provider import VixMarketData
from .adapters.eastmoney.provider import EastMoneyMarketData


def build_default_market() -> MarketDataPort:
    return CompositeMarketData(
        equity=EastMoneyMarketData(),
        crypto=OkxMarketData(),
        vix=VixMarketData(),
    )


# 重新导出便于业务侧 from utils.market.facade import ...
__all__ = [
    "BoardKind",
    "BoardSnapshot",
    "BreadthBar",
    "FinancialSnapshot",
    "IntradaySeries",
    "KlinePeriod",
    "KlineSeries",
    "MarketError",
    "MarketTurnover",
    "NorthboundFlow",
    "Quote",
    "SymbolRef",
    "ValueKind",
    "ValueSeries",
    "build_default_market",
    "is_market_error",
]
