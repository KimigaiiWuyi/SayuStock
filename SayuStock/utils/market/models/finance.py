"""财务快照。"""

from __future__ import annotations

from typing import Literal
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FinancialSnapshot:
    code: str
    report_date: str
    roe: float | None
    revenue_yoy: float | None
    profit_yoy: float | None
    gross_margin: float | None
    net_margin: float | None
    debt_ratio: float | None
    eps: float | None
    bps: float | None
    net_interest_margin: float | None
    industry_type: Literal["standard", "bank"]
    missing_fields: tuple[str, ...]
