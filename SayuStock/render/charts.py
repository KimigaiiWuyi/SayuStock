"""Chart geometry for Takumi templates: SVG shapes and label positions.

Charts are split: this module turns data into path strings, bars and tick
positions in pixels, and the templates draw the shapes in an inline ``<svg>``
and the labels as positioned HTML elements, which CSS can style (pills,
borders, wrapping). Takumi draws SVG text too (since 2.13.0), so a page may
use ``<text>`` where that is simpler.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence, TypeGuard
from dataclasses import dataclass

from .palette import SERIES


@dataclass(frozen=True)
class Scale:
    """Linear map from a data domain to a pixel range (``lo``..``hi``)."""

    domain_lo: float
    domain_hi: float
    lo: float
    hi: float

    def __call__(self, value: float) -> float:
        span = self.domain_hi - self.domain_lo
        if span == 0:
            return (self.lo + self.hi) / 2
        return self.lo + (value - self.domain_lo) / span * (self.hi - self.lo)


def _finite(value: Optional[float]) -> TypeGuard[float]:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def line_path(xs: Sequence[float], ys: Sequence[Optional[float]]) -> str:
    """``M/L`` path through the finite points; a gap starts a new segment."""
    parts: list[str] = []
    pen_down = False
    for x, y in zip(xs, ys):
        if not _finite(y):
            pen_down = False
            continue
        parts.append(f"{'L' if pen_down else 'M'}{x:.1f},{y:.1f}")
        pen_down = True
    return " ".join(parts)


def area_path(xs: Sequence[float], ys: Sequence[Optional[float]], base_y: float) -> str:
    """Closed areas between each finite segment and ``base_y``."""
    areas: list[str] = []
    segment: list[tuple[float, float]] = []

    def close() -> None:
        if len(segment) >= 2:
            points = " ".join(f"L{x:.1f},{y:.1f}" for x, y in segment)
            areas.append(f"M{segment[0][0]:.1f},{base_y:.1f} {points} L{segment[-1][0]:.1f},{base_y:.1f} Z")
        segment.clear()

    for x, y in zip(xs, ys):
        if _finite(y):
            segment.append((x, float(y)))
        else:
            close()
    close()
    return " ".join(areas)


def split_area_paths(xs: Sequence[float], ys: Sequence[Optional[float]], base_y: float) -> tuple[str, str]:
    """``area_path`` split at ``base_y``: the part above it and the part below.

    Each crossing gets a point on the baseline, so the two areas meet where the
    line crosses instead of overlapping (pixel y: above means smaller).
    """
    points_x: list[float] = []
    points_y: list[Optional[float]] = []
    previous: Optional[tuple[float, float]] = None
    for x, y in zip(xs, ys):
        if not _finite(y):
            points_x.append(x)
            points_y.append(None)
            previous = None
            continue
        y = float(y)
        if previous is not None and (previous[1] - base_y) * (y - base_y) < 0:
            share = (base_y - previous[1]) / (y - previous[1])
            points_x.append(previous[0] + (x - previous[0]) * share)
            points_y.append(base_y)
        points_x.append(x)
        points_y.append(y)
        previous = (x, y)
    above = [min(y, base_y) if y is not None else None for y in points_y]
    below = [max(y, base_y) if y is not None else None for y in points_y]
    return area_path(points_x, above, base_y), area_path(points_x, below, base_y)


def nice_step(span: float, target: int) -> float:
    """A 1/2/2.5/5 x 10^n step giving about ``target`` intervals over ``span``."""
    if span <= 0 or target <= 0:
        return 1.0
    raw = span / target
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Round tick values covering ``lo``..``hi``."""
    step = nice_step(hi - lo, target)
    first = math.ceil(lo / step - 1e-9) * step
    values = []
    value = first
    while value <= hi + step * 1e-9:
        values.append(round(value, 10))
        value += step
    return values


def symmetric_limit(values: Sequence[Optional[float]], minimum: float = 1.0, pad: float = 0.12) -> float:
    """Half-range for a zero-centred axis that fits every finite value."""
    peak = max((abs(v) for v in values if _finite(v)), default=0.0)
    return max(minimum, peak * (1 + pad))


def bar_width(count: int, width: float, fill: float = 0.72) -> float:
    return max(width / max(count, 1) * fill, 0.6)


PALETTE = SERIES


def squarify(
    values: Sequence[float], x: float, y: float, width: float, height: float
) -> list[tuple[float, float, float, float]]:
    """Squarified treemap (Bruls et al.): one ``(x, y, w, h)`` per value, in order.

    Pass values sorted from largest to smallest; non-positive values get
    empty rectangles so callers can zip the result with their items.
    """
    total = sum(value for value in values if value > 0)
    if total <= 0 or width <= 0 or height <= 0:
        return [(x, y, 0.0, 0.0) for _ in values]
    scale = width * height / total
    areas = [max(value, 0.0) * scale for value in values]
    rects: list[tuple[float, float, float, float]] = []
    row: list[float] = []

    def worst(items: list[float], side: float) -> float:
        area = sum(items)
        return max(max(side * side * a / (area * area), area * area / (side * side * a)) for a in items if a > 0)

    def lay(items: list[float]) -> None:
        nonlocal x, y, width, height
        area = sum(items)
        if width >= height:
            column = area / height if height else 0.0
            cursor = y
            for item in items:
                size = item / column if column else 0.0
                rects.append((x, cursor, column, size))
                cursor += size
            x, width = x + column, width - column
        else:
            band = area / width if width else 0.0
            cursor = x
            for item in items:
                size = item / band if band else 0.0
                rects.append((cursor, y, size, band))
                cursor += size
            y, height = y + band, height - band

    positive = [area for area in areas if area > 0]
    for area in positive:
        side = min(width, height)
        if not row or worst(row + [area], side) <= worst(row, side):
            row.append(area)
            continue
        lay(row)
        row = [area]
    if row:
        lay(row)
    laid = iter(rects)
    return [next(laid) if area > 0 else (x, y, 0.0, 0.0) for area in areas]
