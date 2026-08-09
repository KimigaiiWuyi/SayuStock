"""东财 f* 字段语义表（仅 adapter 内部）。

单位约定（fltt=2 时价格多为真实元，涨跌幅为百分点数值）。
"""

from __future__ import annotations

# stock/get 单票字段 → 语义名（文档化，解析代码直接用 f 键）
QUOTE_FIELD = {
    "code": "f57",
    "name": "f58",
    "price": "f43",
    "open": "f46",
    "high": "f44",
    "low": "f45",  # 最低价；不是涨跌幅
    "prev_close": "f60",
    "change_pct": "f170",  # 涨跌幅 %
    "change_amount": "f169",
    "volume": "f47",
    "amount": "f48",
    "turnover_rate": "f168",
    "pe": "f9",  # 部分接口用 f162 动态市盈率；解析层双读
    "pe_dyn": "f162",
    "pb": "f23",
    "pb_alt": "f167",
    "market_cap": "f20",
    "float_market_cap": "f21",
    "industry": "f127",
    "industry_alt": "f100",
    "limit_up": "f51",
    "limit_down": "f52",
    "sec_type_flag": "f107",  # 90=板块
}

# clist / hotmap 列表行
BOARD_FIELD = {
    "code": "f12",
    "name": "f14",
    "price": "f2",
    "change_pct": "f3",
    "amount": "f6",
    "market_cap": "f20",
    "industry": "f100",
    "lead_name": "f128",
    "lead_change_pct": "f136",
    "lead_code": "f140",
    "fall_name": "f207",
    "fall_change_pct": "f222",
    "up_count": "f104",
    "down_count": "f105",
    "pe": "f9",
    "turnover_rate": "f8",
    "volume_ratio": "f10",
    "float_market_cap": "f21",
}

# kline CSV 列序（fields2）
KLINE_CSV = (
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "change_pct",
    "change_amount",
    "turnover_rate",
)

# trends CSV 列序
TREND_CSV = (
    "datetime",
    "price",
    "open",
    "high",
    "low",
    "volume",
    "amount",
    "avg_price",
)

PROVIDER = "eastmoney"

# clist 选股字段（仅 transport / universe 拉取用）
CLIST_SCREENER_FIELDS = (
    "f12",
    "f14",
    "f2",
    "f3",
    "f6",
    "f8",
    "f9",
    "f10",
    "f20",
    "f21",
    "f100",
)

# 通用排行 clist 字段（仅 adapter parse_rank 使用）
# f193=主力净比(%)；f184=总营收同比(%)——勿把净比写成 f184
RANK_FIELD = {
    "code": "f12",
    "name": "f14",
    "price": "f2",
    "change_pct": "f3",
    "amount": "f6",
    "volume": "f5",
    "turnover_rate": "f8",
    "industry": "f100",
    "main_net_inflow": "f62",
    "main_net_inflow_pct": "f193",
    "super_large_net": "f66",
    "large_net": "f69",
    "roe": "f173",
    "debt_ratio": "f188",
    "profit_yoy": "f185",
    "revenue_yoy": "f184",
}

# 排行排序键 → clist fid
RANK_SORT = {
    "main_net_inflow": "f62",
    "turnover_rate": "f8",
    "roe": "f173",
    "amount": "f6",
    "volume": "f5",
    "profit_yoy": "f185",
}
