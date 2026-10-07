"""行情数据源抽象层：业务只依赖本包公开 API。"""

from .port import MarketDataPort
from .enums import RankBy, BoardKind, ValueKind, AssetClass, KlinePeriod
from .errors import MarketError, is_market_error
from .models import (
    BREADTH_BANDS,
    RANKING_CAVEAT,
    Bar,
    Quote,
    RankRow,
    BoardRow,
    SymbolRef,
    BreadthBar,
    ValuePoint,
    BoardExtras,
    KlineSeries,
    ValueSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
    breadth_counts,
)
from .convert import board_to_df, kline_to_df, quote_fields, kline_to_cn_df
from .display import DisplayItem, from_quote, from_board_row, pick_display_items, board_rows_to_items
from .registry import get_market, set_market
from .fund_route import maybe_otc_fund_query
from .provider_registry import (
    PROVIDER_LABELS,
    ConfigurableEquityMarket,
    parse_priority_chain,
    normalize_provider_id,
)

__all__ = [
    "AssetClass",
    "Bar",
    "BoardExtras",
    "BoardKind",
    "BoardRow",
    "BoardSnapshot",
    "BreadthBar",
    "BREADTH_BANDS",
    "breadth_counts",
    "ConfigurableEquityMarket",
    "FinancialSnapshot",
    "IntradayPoint",
    "IntradaySeries",
    "KlinePeriod",
    "KlineSeries",
    "MarketDataPort",
    "MarketError",
    "MarketTurnover",
    "NorthboundFlow",
    "PROVIDER_LABELS",
    "Quote",
    "RANKING_CAVEAT",
    "RankBy",
    "RankRow",
    "RankSnapshot",
    "SymbolRef",
    "ValueKind",
    "ValuePoint",
    "ValueSeries",
    "DisplayItem",
    "board_rows_to_items",
    "board_to_df",
    "from_board_row",
    "from_quote",
    "pick_display_items",
    "get_market",
    "is_market_error",
    "kline_to_cn_df",
    "kline_to_df",
    "maybe_otc_fund_query",
    "normalize_provider_id",
    "parse_priority_chain",
    "quote_fields",
    "set_market",
]
