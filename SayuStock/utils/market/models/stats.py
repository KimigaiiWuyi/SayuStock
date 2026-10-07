"""市场统计：涨跌分布、两市成交额、北向。"""

from __future__ import annotations

from typing import Any
from datetime import datetime
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BreadthBucket:
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class BreadthBar:
    """涨跌分布；raw 保留供应商原文供旧 draw_bar 兼容期使用。"""

    buckets: tuple[BreadthBucket, ...]
    raw: Any | None = None


@dataclass(frozen=True, slots=True)
class MarketTurnover:
    # 单市场源（如新浪指数盘口）只能给当日成交额，昨成交额为 None 而非 0 填充
    prev_amount: float | None
    amount: float
    last_trade_date: datetime | None


@dataclass(frozen=True, slots=True)
class NorthboundFlow:
    sh_net_yi: float
    sz_net_yi: float
