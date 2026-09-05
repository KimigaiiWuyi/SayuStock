"""分时概览 sparkline：X 轴对齐个股（会话模板补洞 / 盘中补到收盘）。"""

from __future__ import annotations

from .render_data import _session_rows_from_intraday
from .market.models import IntradaySeries

_MAX_POINTS = 240
_UP_STROKE = "#ff5c5c"
_DOWN_STROKE = "#3ee36a"
_UP_FILL = "rgba(255,92,92,0.28)"
_DOWN_FILL = "rgba(62,227,106,0.28)"
_BASE_STROKE = "#8b95a8"


def sparkline_from_series(
    series: IntradaySeries,
    *,
    width: float = 160.0,
    height: float = 36.0,
) -> str:
    """``IntradaySeries`` → 内联 SVG。点不足时返回空串。"""
    if not series.points:
        return ""
    anchor = series.points[-1].ts
    rows = _session_rows_from_intraday(
        series,
        now_bjt=anchor,
        fill_session_future=True,
        fill_session_gaps=True,
    )
    prices = _prices_from_rows(rows)
    if sum(1 for p in prices if p is not None) < 2:
        prices = [p.price for p in series.points]
    prev = _ref_close(series)
    up = _is_up(series, prices, prev)
    return build_sparkline_svg(prices, prev, up=up, width=width, height=height)


def build_sparkline_svg(
    prices: list[float | None] | tuple[float | None, ...],
    prev_close: float | None,
    *,
    up: bool,
    width: float = 160.0,
    height: float = 36.0,
) -> str:
    """把已对齐的价列画成面积图；``None`` 为轴上占位（午休等），线段在此断开。"""
    seq = _compress(list(prices), _MAX_POINTS)
    finite = [p for p in seq if p is not None]
    if len(finite) < 2:
        return ""
    ref = prev_close if prev_close is not None else finite[0]
    y_min = min(min(finite), ref)
    y_max = max(max(finite), ref)
    pad = (y_max - y_min) * 0.08
    if pad <= 0:
        pad = abs(ref) * 0.002 if ref != 0 else 0.01
    y_min -= pad
    y_max += pad
    span = y_max - y_min
    n = len(seq)
    last_i = n - 1 if n > 1 else 1

    def x_at(index: int) -> float:
        return index / last_i * width

    def y_at(price: float) -> float:
        return height - (price - y_min) / span * height

    stroke = _UP_STROKE if up else _DOWN_STROKE
    fill = _UP_FILL if up else _DOWN_FILL
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.1f} {height:.1f}" '
        f'width="100%" height="100%" preserveAspectRatio="none">'
    ]
    base_y = y_at(ref)
    parts.append(
        f'<line x1="0" y1="{base_y:.2f}" x2="{width:.1f}" y2="{base_y:.2f}" '
        f'stroke="{_BASE_STROKE}" stroke-width="1" stroke-dasharray="3 2" opacity="0.75"/>'
    )
    for seg in _segments(seq):
        if len(seg) < 2:
            continue
        pts = " ".join(f" {x_at(i):.2f},{y_at(p):.2f}" for i, p in seg)
        first_x = x_at(seg[0][0])
        last_x = x_at(seg[-1][0])
        area = (
            f"M{x_at(seg[0][0]):.2f},{y_at(seg[0][1]):.2f}"
            + "".join(f"L{x_at(i):.2f},{y_at(p):.2f}" for i, p in seg[1:])
            + f"L{last_x:.2f},{height:.1f}L{first_x:.2f},{height:.1f}Z"
        )
        parts.append(f'<path d="{area}" fill="{fill}" stroke="none"/>')
        parts.append(
            f'<polyline points="{pts.strip()}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _prices_from_rows(rows: list[dict[str, object]]) -> list[float | None]:
    out: list[float | None] = []
    for row in rows:
        if "price" not in row:
            out.append(None)
            continue
        raw = row["price"]
        if raw is None:
            out.append(None)
        elif isinstance(raw, bool):
            out.append(None)
        elif isinstance(raw, (int, float)):
            out.append(float(raw))
        else:
            out.append(None)
    return out


def _ref_close(series: IntradaySeries) -> float | None:
    q = series.quote
    if q is not None and q.prev_close is not None and q.prev_close != 0:
        return float(q.prev_close)
    if q is not None and q.open is not None and q.open != 0:
        return float(q.open)
    if series.points:
        first = series.points[0]
        if first.open != 0:
            return float(first.open)
        return float(first.price)
    return None


def _is_up(series: IntradaySeries, prices: list[float | None], prev: float | None) -> bool:
    q = series.quote
    if q is not None and q.change_pct is not None:
        return q.change_pct >= 0
    last = next((p for p in reversed(prices) if p is not None), None)
    if last is None or prev is None:
        return True
    return last >= prev


def _compress(prices: list[float | None], max_n: int) -> list[float | None]:
    n = len(prices)
    if n <= max_n or max_n < 2:
        return prices
    out: list[float | None] = []
    for i in range(max_n):
        src = min(n - 1, int(round(i * (n - 1) / (max_n - 1))))
        out.append(prices[src])
    return out


def _segments(prices: list[float | None]) -> list[list[tuple[int, float]]]:
    segs: list[list[tuple[int, float]]] = []
    cur: list[tuple[int, float]] = []
    for i, p in enumerate(prices):
        if p is None:
            if cur:
                segs.append(cur)
                cur = []
            continue
        cur.append((i, p))
    if cur:
        segs.append(cur)
    return segs
