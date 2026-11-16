"""Typed holding rows and the simulation watchlist presentation."""

from __future__ import annotations

from math import cos, sin, tau
from typing import Literal
from dataclasses import dataclass
from collections.abc import Sequence

from markupsafe import Markup

from .market import DisplayItem
from .warn_box import Failure, failure_lines
from .market.display import source_footer
from ..render.holdings import PAD, MOOD_WIDTH, MOOD_HEIGHT
from ..render.extras_ui import Metric, direction, fmt_number
from ..render.templates import render_template
from ..render.card_widgets import (
    Card,
    tone,
    mood_uri,
    footer_uri,
    price_text,
    signed_pct,
    mood_number,
    heat_card_bg,
)

PAPER_HOLDINGS_WIDTH = 720
SPARK_W = 100.0
SPARK_H = 42.0

PriceBasis = Literal["最新行情", "上次报价", "成本估值"]


@dataclass(frozen=True, slots=True)
class HoldingBarRow:
    """单只持仓条（简图用，不含流水）。"""

    code: str
    name: str
    qty: int
    avg_cost: float
    current_price: float
    day_change_pct: float | None
    unrealized_pnl: float
    unrealized_pnl_pct: float
    market_value: float
    provider: str | None = None
    quote_error: str = ""
    price_basis: PriceBasis = "最新行情"
    spark_provider: str | None = None


@dataclass(frozen=True, slots=True)
class _HoldingDisplay:
    holding: HoldingBarRow
    cost: str
    price: str
    weight: str
    row_tone: str
    row_bg: str
    day_text: str
    day_tone: str
    hold_text: str
    hold_tone: str
    spark: Markup


def _money(value: float) -> str:
    absolute = abs(value)
    for threshold, suffix in ((100_000_000, "亿"), (10_000, "万")):
        if absolute >= threshold:
            return f"{value / threshold:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.0f}"


def _signed_money(value: float) -> str:
    return ("+" if value > 0 else "") + _money(value)


def format_holding_price(value: float) -> str:
    return fmt_number(value, 2 if abs(value) >= 10 else 3)


def _allocation_arc(share: float) -> str:
    if share <= 0:
        return ""
    if share >= 1:
        return "M18,2.1 A15.9,15.9 0 0 1 18,33.9 A15.9,15.9 0 0 1 18,2.1"
    x = 18 + 15.9 * sin(share * tau)
    y = 18 - 15.9 * cos(share * tau)
    return f"M18,2.1 A15.9,15.9 0 {int(share > 0.5)} 1 {x:.4f},{y:.4f}"


def _holding_display(holding: HoldingBarRow, equity: float, spark: str) -> _HoldingDisplay:
    day = holding.day_change_pct
    unrealized = holding.unrealized_pnl_pct
    failed = bool(holding.quote_error)
    return _HoldingDisplay(
        holding=holding,
        cost=format_holding_price(holding.avg_cost),
        price=format_holding_price(holding.current_price),
        weight=f"{holding.market_value / equity if equity else 0:.1%}",
        row_tone="failed" if failed else tone(day),
        row_bg="" if failed else heat_card_bg(day, angle=105),
        day_text="未获取" if day is None else fmt_number(day, signed=True, suffix="%"),
        day_tone="warn" if day is None else tone(day),
        hold_text=fmt_number(unrealized, signed=True, suffix="%"),
        hold_tone=tone(unrealized),
        spark=Markup(spark),
    )


def build_paper_holdings_html(
    *,
    account_name: str,
    strategy_id: str,
    enabled: bool,
    cash: float,
    initial_cash: float,
    holdings: list[HoldingBarRow],
    index_items: list[DisplayItem],
    sparklines: dict[str, str] | None = None,
    failures: Sequence[Failure] = (),
    warn_title: str = "",
    title_num: str | None = None,
) -> str:
    position_value = sum(row.market_value for row in holdings)
    equity = cash + position_value
    unreal = sum(row.unrealized_pnl for row in holdings)
    unreal_pct = unreal / position_value if position_value else 0.0
    total_pnl = equity - initial_cash
    total_pnl_pct = total_pnl / initial_cash if initial_cash else 0.0
    position_share = position_value / equity if equity else 0.0
    cash_share = cash / equity if equity else 1.0
    metrics = (
        Metric("总资产", _money(equity), note=f"期初 {_money(initial_cash)}"),
        Metric("现金", _money(cash), note=f"{cash_share:.1%}"),
        Metric("持仓市值", _money(position_value), note=f"{len(holdings)} 只"),
        Metric("浮盈", _signed_money(unreal), direction(unreal), f"{unreal_pct:+.2%}"),
        Metric("累计盈亏", _signed_money(total_pnl), direction(total_pnl), f"{total_pnl_pct:+.2%}"),
    )
    day_values = [row.day_change_pct for row in holdings if row.day_change_pct is not None]
    avg_day = sum(day_values) / len(day_values) if day_values else None
    mood_id = title_num or mood_number(avg_day)
    mood_width = PAPER_HOLDINGS_WIDTH - 2 * PAD
    cards = [
        Card(
            item.name.split("(")[0].strip(),
            price_text(item.price),
            signed_pct(item.change_pct),
            tone(item.change_pct),
            bg=heat_card_bg(item.change_pct, angle=120),
        )
        for item in index_items
    ]
    sparks = sparklines or {}
    rows = tuple(_holding_display(row, equity, sparks[row.code] if row.code in sparks else "") for row in holdings)
    providers = [item.provider for item in index_items if item.provider]
    providers.extend(provider for row in holdings for provider in (row.provider, row.spark_provider) if provider)
    source = source_footer(*providers, note="模拟盘持仓") if providers else "数据来源：未标注 | SayuStock · 模拟盘持仓"
    return render_template(
        "paper_holdings.html",
        width=PAPER_HOLDINGS_WIDTH,
        account_name=account_name,
        strategy_id=strategy_id,
        enabled=enabled,
        mood=mood_uri(mood_id),
        mood_width=mood_width,
        mood_height=round(mood_width * MOOD_HEIGHT / MOOD_WIDTH, 2),
        position_share=f"{position_share:.0%}",
        position_arc=_allocation_arc(position_share),
        metrics_values=metrics,
        average_day=signed_pct(avg_day) if avg_day is not None else "",
        average_tone=tone(avg_day),
        cards=cards,
        rows=rows,
        warn_title=warn_title or f"⚠ {len(failures)}/{len(holdings)} 只持仓今日行情获取失败",
        warning_lines=failure_lines(failures, per_line=1),
        source=source,
        brand=footer_uri(),
    )
