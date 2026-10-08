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

# 每档方向：1 上涨侧 / 0 平盘 / -1 下跌侧。涨跌家数合计、概览图配色都由此派生。
# 不要在渲染端按「第几根柱」对半切涨跌：分档一增删，那个下标就与口径脱钩
# （加「平」之后平盘柱被涂成上涨红，就是这么来的）。
BREADTH_DIRECTION: dict[str, int] = {
    "涨停": 1,
    "5~10": 1,
    "3~5": 1,
    "2~3": 1,
    "1~2": 1,
    "0~1": 1,
    "平": 0,
    "0~-1": -1,
    "-1~-2": -1,
    "-2~-3": -1,
    "-3~-5": -1,
    "-5~-10": -1,
    "跌停": -1,
}


@dataclass(frozen=True, slots=True)
class BreadthBar:
    """涨跌分布；buckets 为有序分档（口径见 BREADTH_BANDS）。

    raw 保留供应商原文，仅供尚未迁移到 buckets 的旧 draw 兼容期使用。
    """

    buckets: tuple[BreadthBucket, ...]
    raw: Any | None = None


def breadth_counts(bar: BreadthBar) -> dict[str, int]:
    """BreadthBar → {档位: 家数}，键序恒为 BREADTH_BANDS；缺失档位补 0。"""
    present = {b.label: b.count for b in bar.buckets}
    return {label: present[label] if label in present else 0 for label in BREADTH_BANDS}


def breadth_up_down(counts: dict[str, int]) -> tuple[int, int]:
    """{档位: 家数} → (上涨家数, 下跌家数)；平盘不计入任何一侧。

    档位归属由 BREADTH_DIRECTION 单点决定，不再在调用方按柱下标或字面档名两处
    各写一遍（两处口径一漂移就会出现「平盘算进上涨」这类错账）。
    """
    up = sum(n for label, n in counts.items() if BREADTH_DIRECTION[label] > 0)
    down = sum(n for label, n in counts.items() if BREADTH_DIRECTION[label] < 0)
    return up, down


@dataclass(frozen=True, slots=True)
class MarketTurnover:
    # 单市场源（如新浪指数盘口）只能给当日成交额，昨成交额为 None 而非 0 填充
    prev_amount: float | None
    amount: float
    last_trade_date: datetime | None
    # 命中源 id（由注册表盖章）。休市日会在源之间顺延，成交额数字必须能标出来源，
    # 否则图上那个数看不出是「东财的今天」还是「新浪的上一交易日」。
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class NorthboundFlow:
    sh_net_yi: float
    sz_net_yi: float
