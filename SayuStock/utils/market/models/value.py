"""估值时间序列。"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass

from ..enums import ValueKind
from .symbol import SymbolRef


@dataclass(frozen=True, slots=True)
class DividendEvent:
    ex_date: date
    report_date: date | None
    bonus_per_share: float | None
    contribution_pct: float | None
    is_planned: bool


@dataclass(frozen=True, slots=True)
class ValuePoint:
    day: date
    value: float
    events: tuple[DividendEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class ValueSeries:
    symbol: SymbolRef
    kind: ValueKind
    points: tuple[ValuePoint, ...]
    # 命中数据源 id（eastmoney/tencent/sina/okx/tiantian/vix），渲染层展示真实来源
    provider: str | None = None
    missing_days: tuple[date, ...] = ()
