"""Presentation adapter for fund holdings; failure is shown on its own row."""

from __future__ import annotations

from dataclasses import dataclass

from .market import DisplayItem
from .market.display import source_footer
from ..render.holdings import SINGLE_WIDTH, holdings_html
from ..render.card_widgets import Card, QuoteRow, tone, row_spark, price_text, signed_pct, detail_text, mood_number

PAGE_W = SINGLE_WIDTH
SPARK_W = 130.0
SPARK_H = 40.0


@dataclass(frozen=True, slots=True)
class FundHoldingRow:
    code: str
    name: str
    weight: str
    price: float | None = None
    change_pct: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None
    provider: str | None = None
    failed: bool = False
    reason: str = ""


def _row(item: FundHoldingRow, sparklines: dict[str, str] | None) -> QuoteRow:
    weight = f"{item.weight}%" if item.weight else ""
    if item.failed:
        return QuoteRow(
            item.name or item.code,
            item.code,
            change="失败",
            tone="failed",
            weight=weight,
            reason=item.reason or "行情获取失败",
        )
    return QuoteRow(
        item.name or item.code,
        detail_text(item.code, item.turnover_rate, item.amount),
        price=price_text(item.price),
        change=signed_pct(item.change_pct),
        tone=tone(item.change_pct),
        spark=row_spark(sparklines, item.code, item.name),
        weight=weight,
    )


def build_fund_holdings_html(
    *,
    fund_name: str,
    fund_code: str,
    rows: list[FundHoldingRow],
    index_items: list[DisplayItem],
    meta: str,
    average: float | None,
    report_period: str = "",
    sparklines: dict[str, str] | None = None,
    sources: tuple[str | None, ...] = (),
) -> str:
    subtitle = " · ".join(part for part in (fund_code, report_period, meta) if part)
    return holdings_html(
        eyebrow="基金持仓",
        title=fund_name,
        subtitle=subtitle,
        average=average,
        title_num=mood_number(average),
        cards=[Card(i.name, price_text(i.price), signed_pct(i.change_pct), tone(i.change_pct)) for i in index_items],
        rows=[_row(row, sparklines) for row in rows],
        section_title="重仓股",
        section_note="当日分时 · 基金持仓权重 · 涨跌幅",
        source=source_footer("eastmoney", *sources, *(i.provider for i in index_items), *(r.provider for r in rows)),
        width=PAGE_W,
    )
