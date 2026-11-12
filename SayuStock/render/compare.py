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
    RIGHT,
    Dot,
    Mark,
    Rule,
    Panel,
    Legend,
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
    frame_column,
    percent_text,
    add_end_labels,
)
from ..utils.constant import ErroText
from ..utils.render_data import build_compare_render_data
from ..utils.market.models import KlineSeries
from ..utils.market.display import source_footer


@dataclass(slots=True)
class Trace:
    name: str
    values: dict[datetime, float]
    first_close: float
    last_close: float
    provider: str | None


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


def _range_marks(
    panel: Panel,
    xs: list[float],
    scale: Scale,
    dates: list[datetime],
    actual: dict[datetime, float],
    color: str,
) -> str:
    stamps = list(actual)
    values = list(actual.values())
    swing = ind.swing_points(pd.Series(values, index=pd.DatetimeIndex(stamps)))
    detail: list[str] = []
    positions = {stamp: i for i, stamp in enumerate(dates)}
    for label, start, end, change in (
        ("最大涨幅", swing.runup_start, swing.runup_end, swing.max_runup),
        ("最大回撤", swing.drawdown_start, swing.drawdown_end, swing.max_drawdown),
    ):
        if start < 0 or end < 0:
            detail.append(f"{label} {percent_text(change)}")
            continue
        first, last = stamps[start], stamps[end]
        date_format = "%Y-%m-%d" if dates[0].year != dates[-1].year else "%m-%d"
        detail.append(f"{label} {percent_text(change)} · {first:{date_format}}→{last:{date_format}}")
        x0, x1 = xs[positions[first]], xs[positions[last]]
        y0, y1 = scale(values[start]), scale(values[end])
        if start != end:
            panel.rules.append(Rule(x0, y0, x1, y1, color, "4 4", 0.65))
            panel.dots.extend([Dot(x0, y0, color, 2.6), Dot(x1, y1, color, 3.6)])
    return "；".join(detail)


def prepare_compare(series_list: list[KlineSeries]) -> ChartPage | str:
    traces = [trace for series in series_list if (trace := _trace(series)) is not None]
    if not traces:
        return ErroText["notData"]
    dates = sorted({stamp for trace in traces for stamp in trace.values})
    xs = plot_x(len(dates))
    panel = Panel("累计涨跌幅 · %", 485, [], date_ticks(dates, xs))
    lo, hi = bounds([0, *(value for trace in traces for value in trace.values.values())], minimum=0.1)
    scale = panel_scale(panel, lo, hi, lambda value: f"{value:g}%")
    baseline = scale(0)
    panel.backdrop.extend(
        [
            Rectangle(LEFT, TOP, RIGHT - LEFT, baseline - TOP, UP, 0.08),
            Rectangle(LEFT, baseline, RIGHT - LEFT, panel.bottom - baseline, DOWN, 0.08),
        ]
    )
    panel.rules.append(Rule(LEFT, baseline, RIGHT, baseline))
    entries: list[Mark] = []
    legend: list[Legend] = []
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
        entries.append(Mark(xs[dates.index(last_stamp)], scale(last), percent_text(last), color))
        extrema = _range_marks(panel, xs, scale, dates, trace.values, color)
        detail = f"{number_text(trace.first_close, 3)} → {number_text(trace.last_close, 3)}"
        if extrema:
            detail += f" · {extrema}"
        legend.append(Legend(trace.name, color, percent_text(last), detail))
    add_end_labels(panel, entries)
    return ChartPage(
        "个股对比",
        "区间表现",
        f"{dates[0]:%Y-%m-%d} — {dates[-1]:%Y-%m-%d} · 各标的以首个收盘价为 0%",
        [panel],
        source_footer(*(trace.provider for trace in traces)),
        legend,
    )
