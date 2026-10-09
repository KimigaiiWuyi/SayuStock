"""行情层枚举：周期、板块、估值类型、资产类别。"""

from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    FUND = "fund"
    INDEX = "index"
    FUTURE = "future"
    CRYPTO = "crypto"
    VIX = "vix"
    BOND = "bond"
    OTHER = "other"


class KlinePeriod(str, Enum):
    """与业务周期字符串对齐；EM adapter 内再映射 klt 码。"""

    M5 = "5"
    M15 = "15"
    M30 = "30"
    M60 = "60"
    D1 = "101"
    W1 = "102"
    MON1 = "103"
    Q1 = "104"
    H1 = "105"
    Y1 = "106"
    # 短窗日 K 兼容旧 sector 后缀
    D1_RECENT = "100"
    D1_YEAR = "111"


class BoardKind(str, Enum):
    INDEX = "index"
    INDUSTRY = "industry"
    CONCEPT = "concept"
    A_SHARE = "a_share"
    HOTMAP = "hotmap"
    CUSTOM = "custom"
    INTERNATIONAL = "international"
    COMMODITY = "commodity"
    FX = "fx"
    OTHER = "other"


class ValueKind(str, Enum):
    PE = "pe"
    PB = "pb"
    DY = "dy"


class RankBy(str, Enum):
    """沪深 A 通用排行键（与业务/AI 工具别名层对齐）。"""

    MAIN_INFLOW = "main_inflow"
    MAIN_OUTFLOW = "main_outflow"
    TURNOVER = "turnover"
    ROE = "roe"
    AMOUNT = "amount"
    VOLUME = "volume"
    PROFIT_YOY = "profit_yoy"


def resolve_rank_by(rank_by: RankBy | str) -> RankBy | None:
    """排行键归一化：枚举直通，字符串按 value（忽略大小写）匹配。"""
    if isinstance(rank_by, RankBy):
        return rank_by
    raw = (rank_by or "").strip()
    if not raw:
        return None
    for m in RankBy:
        if raw == m.value or raw.lower() == m.value:
            return m
    return None


class IpoMarket(str, Enum):
    """IPO 日历市场。"""

    CN = "cn"
    HK = "hk"
    US = "us"


_IPO_MARKET_ALIASES = {"cn": IpoMarket.CN, "hk": IpoMarket.HK, "us": IpoMarket.US}


def coerce_ipo_market(value: IpoMarket | str) -> IpoMarket | None:
    """市场归一化：枚举直通；字符串接受 cn/hk/us 与 A股/港股/美股。"""
    if isinstance(value, IpoMarket):
        return value
    raw = (value or "").strip().lower()
    if raw in _IPO_MARKET_ALIASES:
        return _IPO_MARKET_ALIASES[raw]
    for alias, m in (("a股", IpoMarket.CN), ("沪深", IpoMarket.CN), ("港股", IpoMarket.HK), ("美股", IpoMarket.US)):
        if raw == alias:
            return m
    return None


class IpoStage(str, Enum):
    """IPO 阶段（相对查看日 anchor 推导，见 ``IpoEvent.stage_on``）。"""

    FILED = "filed"  # 已申报（美股纳斯达克源）
    APPLY = "apply"  # A股申购日已到或未到
    PENDING = "pending"  # 待上市
    LISTED = "listed"  # 已上市
