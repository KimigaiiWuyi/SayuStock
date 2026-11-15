"""Calendar-aligned comparison of the upstream normalized Kline series."""

from datetime import datetime
from dataclasses import dataclass

import pandas as pd

from ..utils import indicators as ind
from .charts import Scale
from .palette import UP, DOWN, SERIES
from .chart_page import (
    TOP,
    LEFT,
    Dot,
    Mark,
    Rule,
    Panel,
    Legend,
    TagBox,
    ChartPage,
    Rectangle,
    bounds,
    plot_x,
    numbers,
    add_series,
    date_ticks,
    timestamps,
    number_text,
    panel_scale,
    plot_bounds,
    frame_column,
    percent_text,
    add_end_labels,
)
from ..utils.constant import ErroText
from ..utils.render_data import build_compare_render_data
from ..utils.market.models import KlineSeries
from ..utils.market.display import source_footer

COMPARE_WIDTH = 1440
COMPARE_RIGHT_MARGIN = 195
_, COMPARE_RIGHT = plot_bounds(COMPARE_WIDTH, COMPARE_RIGHT_MARGIN)


@dataclass(slots=True)
class Trace:
    name: str
    values: dict[datetime, float]
    first_close: float
    last_close: float
    provider: str | None


@dataclass(slots=True)
class _BoxCandidate:
    anchor_x: float
    anchor_y: float
    lines: tuple[str, ...]
    color: str
    above: bool


def _trace(series: KlineSeries) -> Trace | None:
    data = build_compare_render_data([series])
    if isinstance(data, str):
        return None
    item = data.items[0]
    dates = timestamps(frame_column(item.df, "日期"))
    values = numbers(frame_column(item.df, "归一化"))
    closes = numbers(frame_column(item.df, "收盘"))
    points: dict[datetime, float] = {}
    usable_closes: list[float] = []
    for stamp, value, close in zip(dates, values, closes, strict=True):
        if value is not None and close is not None:
            points[stamp] = value * 100
            usable_closes.append(close)
    if not points:
        return None
    return Trace(item.name, points, usable_closes[0], usable_closes[-1], series.provider)


def _text_width(text: str) -> float:
    return sum(12.2 if ord(ch) > 127 else 7.4 for ch in text)


def _place_tag_boxes(panel: Panel, candidates: list[_BoxCandidate], right: int) -> None:
    placed: list[tuple[float, float, float, float]] = []
    min_x, max_x = float(LEFT + 4), float(right - 4)
    min_y, max_y = float(TOP + 4), float(panel.bottom - 4)
    for item in candidates:
        line_h = 15.0
        w = max(_text_width(line) for line in item.lines) + 18.0
        h = len(item.lines) * line_h + 8.0
        base_dy = -h - 14.0 if item.above else 14.0
        flip_dy = 14.0 if item.above else -h - 14.0
        step_dir = -1.0 if item.above else 1.0
        dxs = (-w / 2, -w - 10.0, 10.0, -w - 48.0, 48.0)
        dys = (
            base_dy,
            flip_dy,
            base_dy + 34.0 * step_dir,
            flip_dy - 36.0 * step_dir,
            base_dy + 68.0 * step_dir,
            flip_dy - 72.0 * step_dir,
            flip_dy - 108.0 * step_dir,
            flip_dy - 144.0 * step_dir,
        )
        offsets: list[tuple[float, float]] = [(0.0, 0.0)] * 0
        for dy in dys:
            for dx in dxs:
                offsets.append((dx, dy))
        offsets.extend([(-w - 14.0, -h / 2), (14.0, -h / 2)])
        best = (max(min_x, min(max_x - w, item.anchor_x - w / 2)), max(min_y, min(max_y - h, item.anchor_y + base_dy)))
        best_cost = float("inf")
        for dx, dy in offsets:
            bx = max(min_x, min(max_x - w, item.anchor_x + dx))
            by = max(min_y, min(max_y - h, item.anchor_y + dy))
            overlap = 0.0
            for px, py, pw, ph in placed:
                ox = max(0.0, min(bx + w + 8, px + pw) - max(bx - 8, px))
                oy = max(0.0, min(by + h + 8, py + ph) - max(by - 8, py))
                overlap += ox * oy
            dist = abs(bx + w / 2 - item.anchor_x) + abs(by + h / 2 - item.anchor_y) * 0.4
            cost = overlap * 100.0 + dist
            if cost < best_cost:
                best_cost = cost
                best = (bx, by)
                if overlap == 0.0:
                    break
        bx, by = best
        placed.append((bx, by, w, h))
        attach_x = max(bx, min(bx + w, item.anchor_x))
        attach_y = max(by, min(by + h, item.anchor_y))
        panel.boxes.append(
            TagBox(bx, by, w, h, item.anchor_x, item.anchor_y, attach_x, attach_y, item.lines, item.color, line_h)
        )


def _range_marks(
    panel: Panel,
    xs: list[float],
    scale: Scale,
    dates: list[datetime],
    name: str,
    actual: dict[datetime, float],
    color: str,
    candidates: list[_BoxCandidate],
) -> str:
    stamps = list(actual)
    values = list(actual.values())
    swing = ind.swing_points(pd.Series(values, index=pd.DatetimeIndex(stamps)))
    detail: list[str] = []
    positions = {stamp: i for i, stamp in enumerate(dates)}
    date_format = "%Y-%m-%d" if dates[0].year != dates[-1].year else "%m-%d"

    max_idx = max(range(len(values)), key=lambda i: values[i])
    min_idx = min(range(len(values)), key=lambda i: values[i])
    max_lines = [name, f"涨幅 {percent_text(values[max_idx])}"]
    min_lines = [name, f"跌幅 {percent_text(values[min_idx])}"]

    runup_extra: tuple[int, str, str] | None = None
    drawdown_extra: tuple[int, str, str] | None = None
    for kind, label, start, end, change in (
        ("up", "最大涨幅", swing.runup_start, swing.runup_end, swing.max_runup),
        ("down", "最大回撤", swing.drawdown_start, swing.drawdown_end, swing.max_drawdown),
    ):
        if start < 0 or end < 0:
            detail.append(f"{label} {percent_text(change)}")
            continue
        first, last = stamps[start], stamps[end]
        span = f"{first:{date_format}}→{last:{date_format}}"
        detail.append(f"{label} {percent_text(change)} · {span}")
        x0, x1 = xs[positions[first]], xs[positions[last]]
        y0, y1 = scale(values[start]), scale(values[end])
        if start != end:
            panel.rules.append(Rule(x0, y0, x1, y1, color, "4 4", 0.65))
            panel.dots.extend([Dot(x0, y0, color, 2.6), Dot(x1, y1, color, 3.6)])
        if kind == "up" and end == max_idx:
            max_lines.append(f"{label} {percent_text(change)} · {span}")
        elif kind == "up":
            runup_extra = (end, f"{label} {percent_text(change)}", span)
        elif kind == "down" and end == min_idx:
            min_lines.append(f"{label} {percent_text(change)} · {span}")
        else:
            drawdown_extra = (end, f"{label} {percent_text(change)}", span)

    if len(values) >= 2:
        mx, my = xs[positions[stamps[max_idx]]], scale(values[max_idx])
        nx, ny = xs[positions[stamps[min_idx]]], scale(values[min_idx])
        panel.dots.extend([Dot(mx, my, color, 3.2), Dot(nx, ny, color, 3.2)])
        candidates.append(_BoxCandidate(mx, my, tuple(max_lines), color, above=True))
        candidates.append(_BoxCandidate(nx, ny, tuple(min_lines), color, above=False))
        if runup_extra is not None:
            idx, head, span = runup_extra
            candidates.append(
                _BoxCandidate(xs[positions[stamps[idx]]], scale(values[idx]), (name, head, span), color, above=True)
            )
        if drawdown_extra is not None:
            idx, head, span = drawdown_extra
            candidates.append(
                _BoxCandidate(xs[positions[stamps[idx]]], scale(values[idx]), (name, head, span), color, above=False)
            )
    return "；".join(detail)


def prepare_compare(series_list: list[KlineSeries]) -> ChartPage | str:
    traces = [trace for series in series_list if (trace := _trace(series)) is not None]
    if not traces:
        return ErroText["notData"]
    dates = sorted({stamp for trace in traces for stamp in trace.values})
    xs = plot_x(len(dates), right=COMPARE_RIGHT)
    panel = Panel("累计涨跌幅 · %", 540, [], date_ticks(dates, xs))
    lo, hi = bounds([0, *(value for trace in traces for value in trace.values.values())], minimum=0.1)
    scale = panel_scale(panel, lo, hi, lambda value: f"{value:g}%")
    baseline = scale(0)
    panel.backdrop.extend(
        [
            Rectangle(LEFT, TOP, COMPARE_RIGHT - LEFT, baseline - TOP, UP, 0.08),
            Rectangle(LEFT, baseline, COMPARE_RIGHT - LEFT, panel.bottom - baseline, DOWN, 0.08),
        ]
    )
    panel.rules.append(Rule(LEFT, baseline, COMPARE_RIGHT, baseline))
    entries: list[Mark] = []
    legend: list[Legend] = []
    candidates: list[_BoxCandidate] = []
    for i, trace in enumerate(traces):
        color = SERIES[i % len(SERIES)]
        values: list[float | None] = []
        last: float | None = None
        last_stamp = max(trace.values)
        for stamp in dates:
            if stamp in trace.values:
                last = trace.values[stamp]
            values.append(last if stamp <= last_stamp else None)
        assert last is not None
        add_series(panel, xs, values, scale, color, width=2, end_dot=True)
        entries.append(Mark(xs[dates.index(last_stamp)], scale(last), f"{trace.name} {percent_text(last)}", color))
        extrema = _range_marks(panel, xs, scale, dates, trace.name, trace.values, color, candidates)
        detail = f"{number_text(trace.first_close, 3)} → {number_text(trace.last_close, 3)}"
        if extrema:
            detail += f" · {extrema}"
        legend.append(Legend(trace.name, color, percent_text(last), detail))
    _place_tag_boxes(panel, candidates, COMPARE_RIGHT)
    add_end_labels(panel, entries, right=COMPARE_RIGHT)
    return ChartPage(
        "个股对比",
        "区间表现",
        f"{dates[0]:%Y-%m-%d} — {dates[-1]:%Y-%m-%d} · 各标的以首个收盘价为 0%",
        [panel],
        source_footer(*(trace.provider for trace in traces)),
        legend,
        width=COMPARE_WIDTH,
        right_margin=COMPARE_RIGHT_MARGIN,
    )
