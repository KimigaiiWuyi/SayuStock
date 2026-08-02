"""分时与 K 线序列。"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from .quote import Quote
from ..enums import KlinePeriod
from .symbol import SymbolRef


@dataclass(frozen=True, slots=True)
class IntradayPoint:
    ts: datetime
    price: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    avg_price: float


@dataclass(frozen=True, slots=True)
class IntradaySeries:
    symbol: SymbolRef
    points: tuple[IntradayPoint, ...]
    quote: Quote | None


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None
    amplitude: float | None
    change_pct: float | None
    change_amount: float | None
    turnover_rate: float | None


@dataclass(frozen=True, slots=True)
class KlineSeries:
    symbol: SymbolRef
    period: KlinePeriod
    bars: tuple[Bar, ...]
    adjusted: bool
