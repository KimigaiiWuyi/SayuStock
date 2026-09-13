"""策略共用 / 按策略复用的工具名。

账本读写是所有盘共用的；研究类工具按策略挂，避免量能盘每轮去打财报/榜单。
"""

from __future__ import annotations

__all__ = [
    "BOOK_READ_TOOLS",
    "BOOK_WRITE_TOOLS",
    "CALENDAR_TOOLS",
    "MACRO_READ_TOOLS",
    "MACRO_WRITE_TOOLS",
    "CORE_DECISION_TOOLS",
    "MULTIFACTOR_RESEARCH_TOOLS",
    "VOLUME_RESEARCH_TOOLS",
]

BOOK_READ_TOOLS: tuple[str, ...] = (
    "papertrade_account_query",
    "papertrade_position_list",
    "papertrade_trade_list",
    "papertrade_decision_list",
    "papertrade_watchlist_list",
    "papertrade_agent_pool_list",
)

BOOK_WRITE_TOOLS: tuple[str, ...] = (
    "papertrade_decision_insert",
    "papertrade_trade_insert",
    "papertrade_position_upsert",
    "papertrade_match_order",
    "papertrade_candidate_refresh",
)

CALENDAR_TOOLS: tuple[str, ...] = ("stock_is_trading_day",)

# 宏观事件表：所有策略都要能读（档位决定仓位上限），登记 / 更新只给多因子盘
MACRO_READ_TOOLS: tuple[str, ...] = ("macro_event_list",)
MACRO_WRITE_TOOLS: tuple[str, ...] = ("macro_event_upsert",)

CORE_DECISION_TOOLS: tuple[str, ...] = BOOK_READ_TOOLS + BOOK_WRITE_TOOLS + CALENDAR_TOOLS + MACRO_READ_TOOLS

MULTIFACTOR_RESEARCH_TOOLS: tuple[str, ...] = (
    "stock_indicators",
    "stock_financials",
    "send_stock_PB_info",
    "get_market_overview",
    "get_sector_heatmap",
    "get_market_ranking",
    "get_latest_news",
    "get_vix_index",
    "search_stock",
    "get_stock_change_rate",
    "send_cloudmap_img",
    *MACRO_WRITE_TOOLS,
)

VOLUME_RESEARCH_TOOLS: tuple[str, ...] = (
    "papertrade_volume_scan",
    "stock_indicators",
)
