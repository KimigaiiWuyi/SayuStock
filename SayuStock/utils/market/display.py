"""绘图/列表用语义展示项（业务禁止再读 f*）。"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Quote, BoardRow


@dataclass(frozen=True, slots=True)
class DisplayItem:
    """大盘块/列表行通用展示。"""

    name: str
    price: float
    change_pct: float
    amount: float | None = None
    industry: str | None = None
    lead_name: str | None = None
    lead_change_pct: float | None = None
    fall_name: str | None = None
    fall_change_pct: float | None = None
    code: str = ""


def from_board_row(row: BoardRow) -> DisplayItem:
    return DisplayItem(
        name=row.name,
        price=float(row.price) if row.price is not None else 0.0,
        change_pct=float(row.change_pct) if row.change_pct is not None else 0.0,
        amount=row.amount,
        industry=row.industry,
        lead_name=row.lead_name,
        lead_change_pct=row.lead_change_pct,
        fall_name=row.fall_name,
        fall_change_pct=row.fall_change_pct,
        code=row.code,
    )


def from_quote(q: Quote) -> DisplayItem:
    return DisplayItem(
        name=q.symbol.name.split(" (")[0] if " (" in q.symbol.name else q.symbol.name,
        price=float(q.price),
        change_pct=float(q.change_pct) if q.change_pct is not None else 0.0,
        amount=q.amount,
        industry=q.industry,
        code=q.symbol.code,
    )


def board_rows_to_items(rows: tuple[BoardRow, ...] | list[BoardRow]) -> list[DisplayItem]:
    return [from_board_row(r) for r in rows]
