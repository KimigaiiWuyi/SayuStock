"""行情层枚举：周期、板块、估值类型、资产类别。"""

from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
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
