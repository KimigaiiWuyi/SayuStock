"""Presentation data shared by market cards and holdings rows."""

from __future__ import annotations

import math
from base64 import b64encode
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass
from collections.abc import Sequence

from . import charts
from .palette import UP, DOWN, FLAT, rgba
from ..utils.utils import number_to_chinese
from ..utils.render_data import _session_rows_from_intraday
from ..utils.market.models import Quote, IntradaySeries

_PLUGIN = Path(__file__).resolve().parents[1]
_MOOD_THRESHOLDS = (3.3, 2.7, 2.0, 1.0, 0.0, -0.5, -1.3, -2.1, -3.1, -4.0)
_MISSING = "—"


@lru_cache(maxsize=12)
def _png_uri(path: Path) -> str:
    return "data:image/png;base64," + b64encode(path.read_bytes()).decode("ascii")


def mood_number(change: float | None) -> str:
    if change is None:
        return "5"
    return str(next((i for i, limit in enumerate(_MOOD_THRESHOLDS, 1) if change >= limit), 11))


def mood_uri(number: str) -> str:
    return _png_uri(_PLUGIN / "stock_info" / "texture2d" / f"title{int(number)}.png")


def footer_uri() -> str:
    return _png_uri(_PLUGIN / "utils" / "texture2d" / "footer.png")


def tone(value: float | None) -> str:
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def signed_pct(value: float | None) -> str:
    return _MISSING if value is None else f"{value:+.2f}%"


def _quote_number(value: float, *, signed: bool = False) -> str:
    rounded = round(value, 6) or 0.0
    text = f"{rounded:+.6f}" if signed else f"{rounded:.6f}"
    return text[:-4] + text[-4:].rstrip("0")


def price_text(value: float | None) -> str:
    return _MISSING if value is None else _quote_number(value)


def price_move(quote: Quote) -> float | None:
    if quote.change_amount is not None:
        return quote.change_amount
    return quote.price - quote.prev_close if quote.prev_close is not None else None


def detail_text(code: str, turnover: float | None, amount: float | None) -> str:
    parts = [code] if code else []
    if turnover is not None:
        parts.append(f"换手 {turnover:.2f}%")
    if amount is not None:
        parts.append(f"额 {number_to_chinese(amount)}")
    return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class Card:
    name: str
    price: str
    change: str = ""
    tone: str = "flat"
    spark: str = ""


@dataclass(frozen=True, slots=True)
class QuoteRow:
    name: str
    details: str
    price: str = ""
    change: str = ""
    tone: str = "flat"
    move: str = ""
    market: str = ""
    spark: str = ""
    weight: str = ""
    reason: str = ""


def watch_quote_row(quote: Quote, code: str, spark: str = "") -> QuoteRow:
    move = price_move(quote)
    return QuoteRow(
        name=quote.symbol.name or quote.symbol.display_name or code,
        details=detail_text(quote.symbol.code or code, quote.turnover_rate, quote.amount),
        price=price_text(quote.price),
        change=signed_pct(quote.change_pct),
        tone=tone(quote.change_pct),
        move="" if move is None else _quote_number(move, signed=True),
        market=quote.symbol.sec_type,
        spark=spark,
    )


def row_spark(sparks: dict[str, str] | None, *keys: str) -> str:
    if sparks is not None:
        return next((sparks[key] for key in keys if key in sparks), "")
    return ""


def quote_cards(items: Sequence[tuple[str, Quote | None]]) -> list[Card]:
    return [
        Card(name, price_text(q.price), signed_pct(q.change_pct), tone(q.change_pct))
        if q is not None
        else Card(name, "暂无行情")
        for name, q in items
    ]


def _spark_prices(series: IntradaySeries) -> list[float | None]:
    prices: list[float | None] = []
    for row in _session_rows_from_intraday(
        series, now_bjt=series.points[-1].ts, fill_session_future=True, fill_session_gaps=True
    ):
        value: object = row["price"]
        prices.append(float(value) if isinstance(value, (float, int)) and math.isfinite(value) else None)
    return prices


def watch_sparkline(series: IntradaySeries, quote: Quote, *, width: float, height: float) -> str:
    if len(series.points) < 2:
        return ""
    prices = _spark_prices(series)
    observed = [(i, p) for i, p in enumerate(prices) if p is not None]
    if len(observed) < 2:
        return ""
    base = quote.prev_close or observed[0][1]
    reach = max(max(abs(p - base) for _, p in observed), abs(base) * 0.005, 1e-9) * 1.15
    x = charts.Scale(0, len(prices) - 1, 3, width - 3)
    y = charts.Scale(base - reach, base + reach, height - 3, 3)
    xs = [x(i) for i in range(len(prices))]
    ys = [y(p) if p is not None else None for p in prices]
    line = charts.line_path(xs, ys)
    last_i, last_price = observed[-1]
    color = {"up": UP, "down": DOWN, "flat": FLAT}[tone(quote.change_pct)]
    base_y = y(base)
    area = charts.area_path(xs, ys, base_y)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.1f} {height:.1f}" '
        'width="100%" height="100%" preserveAspectRatio="none">'
        f'<path d="{area}" fill="{rgba(color, 0.16)}"/>'
        f'<line x1="0" y1="{base_y:.1f}" x2="{width:.1f}" y2="{base_y:.1f}" '
        f'stroke="{FLAT}" stroke-width="1" stroke-dasharray="3 3" opacity="0.6"/>'
        f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>'
        f'<circle cx="{x(last_i):.1f}" cy="{y(last_price):.1f}" r="2.5" fill="{color}"/></svg>'
    )
