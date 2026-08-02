"""行情数据源抽象层：业务只依赖本包公开 API。"""

from .port import MarketDataPort
from .enums import BoardKind, ValueKind, AssetClass, KlinePeriod
from .errors import MarketError, is_market_error
from .models import (
    Bar,
    Quote,
    BoardRow,
    SymbolRef,
    BreadthBar,
    ValuePoint,
    BoardExtras,
    KlineSeries,
    ValueSeries,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)
from .convert import board_to_df, kline_to_df, quote_fields, kline_to_cn_df
from .display import DisplayItem, from_quote, from_board_row, board_rows_to_items
from .registry import get_market, set_market

__all__ = [
    "AssetClass",
    "Bar",
    "BoardExtras",
    "BoardKind",
    "BoardRow",
    "BoardSnapshot",
    "BreadthBar",
    "FinancialSnapshot",
    "IntradayPoint",
    "IntradaySeries",
    "KlinePeriod",
    "KlineSeries",
    "MarketDataPort",
    "MarketError",
    "MarketTurnover",
    "NorthboundFlow",
    "Quote",
    "SymbolRef",
    "ValueKind",
    "ValuePoint",
    "ValueSeries",
    "DisplayItem",
    "board_rows_to_items",
    "board_to_df",
    "from_board_row",
    "from_quote",
    "get_market",
    "is_market_error",
    "kline_to_cn_df",
    "kline_to_df",
    "quote_fields",
    "set_market",
]
