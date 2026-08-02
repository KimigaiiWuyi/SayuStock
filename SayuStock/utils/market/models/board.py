"""板块/列表/云图行。"""

from __future__ import annotations

from dataclasses import dataclass

from ..enums import BoardKind


@dataclass(frozen=True, slots=True)
class BoardExtras:
    """选股等扩展字段；缺省为 None，不用裸 dict。"""

    pe: float | None = None
    pb: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    float_market_cap: float | None = None
    up_count: int | None = None
    down_count: int | None = None
    lead_code: str | None = None
    fall_name: str | None = None
    fall_change_pct: float | None = None


@dataclass(frozen=True, slots=True)
class BoardRow:
    code: str
    name: str
    price: float | None
    change_pct: float | None
    amount: float | None
    market_cap: float | None
    industry: str | None
    lead_name: str | None
    lead_change_pct: float | None
    fall_name: str | None = None
    fall_change_pct: float | None = None
    extras: BoardExtras | None = None


@dataclass(frozen=True, slots=True)
class BoardSnapshot:
    kind: BoardKind
    title: str
    rows: tuple[BoardRow, ...]
