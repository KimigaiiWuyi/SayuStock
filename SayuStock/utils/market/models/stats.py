"""市场统计：涨跌分布、两市成交额、北向。"""

from __future__ import annotations

from typing import Any
from datetime import datetime
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BreadthBucket:
    label: str
    count: int


# 涨跌分布的标准分档（东财 updowndistribution 与自算全A共用同一口径）。
# 首尾两档即涨停/跌停；中间按涨跌幅绝对值分档，供概览图与 AI 文字共用。
BREADTH_BANDS: tuple[str, ...] = (
    "涨停",
    "5~10",
    "3~5",
    "2~3",
    "1~2",
    "0~1",
    "平",
    "0~-1",
    "-1~-2",
    "-2~-3",
    "-3~-5",
    "-5~-10",
    "跌停",
)


@dataclass(frozen=True, slots=True)
class BreadthBar:
    """涨跌分布；buckets 为有序分档（口径见 BREADTH_BANDS）。

    raw 保留供应商原文，仅供尚未迁移到 buckets 的旧 draw 兼容期使用。
    """

    buckets: tuple[BreadthBucket, ...]
    raw: Any | None = None


def breadth_counts(bar: BreadthBar) -> dict[str, int]:
    """BreadthBar → {档位: 家数}；缺失档位补 0，调用方无需判空。"""
    return {b.label: b.count for b in bar.buckets}


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
