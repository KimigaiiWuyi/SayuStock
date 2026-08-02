"""现货/盘口行情。"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from .symbol import SymbolRef


@dataclass(frozen=True, slots=True)
class Quote:
    """语义化盘口；change_pct 为百分比数值（1.23 表示 +1.23%）。"""

    symbol: SymbolRef
    price: float
    open: float | None
    high: float | None
    low: float | None
    prev_close: float | None
    change_pct: float | None
    change_amount: float | None
    volume: float | None
    amount: float | None
    turnover_rate: float | None
    pe: float | None
    pb: float | None
    market_cap: float | None
    float_market_cap: float | None
    industry: str | None
    limit_up: float | None
    limit_down: float | None
    as_of: datetime | None
