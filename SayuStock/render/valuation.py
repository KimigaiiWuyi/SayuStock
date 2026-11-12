"""Historical valuation curves and implemented dividend events."""

from math import isfinite
from bisect import bisect_right
from datetime import date, time, datetime

from .charts import Scale
from .palette import SERIES
from .chart_page import (
    LEFT,
    RIGHT,
    Dot,
    Mark,
    Rule,
    Panel,
    Legend,
    ChartPage,
    bounds,
    add_series,
    date_ticks,
    number_text,
    panel_scale,
    percent_text,
    add_end_labels,
)
from ..utils.market.enums import ValueKind
from ..utils.market.models import ValueSeries
from ..utils.market.display import source_footer
from ..utils.market.models.value import ValuePoint, DividendEvent


def _events(points: list[ValuePoint]) -> list[DividendEvent]:
    unique: dict[tuple[date, date | None], DividendEvent] = {}
    for point in points:
        for event in point.events:
            key = (event.ex_date, event.report_date)
            if not event.is_planned and points[0].day <= event.ex_date <= points[-1].day:
                unique.setdefault(key, event)
    ranked = sorted(unique.values(), key=lambda event: abs(event.contribution_pct or 0), reverse=True)
    return sorted(ranked[:8], key=lambda event: event.ex_date)


def _point_path(
    actual: dict[date, float], days: list[date], x: Scale, *, break_long_gaps: bool
) -> tuple[list[float], list[float | None]]:
    xs: list[float] = []
    ys: list[float | None] = []
    for i, day in enumerate(days):
        if i and break_long_gaps and (day - days[i - 1]).days > 7:
            xs.append(x((days[i - 1].toordinal() + day.toordinal()) / 2))
            ys.append(None)
        xs.append(x(day.toordinal()))
        ys.append(actual[day] if day in actual else None)
    return xs, ys


def _event_mark(panel: Panel, x: float, y: float, seq: int, color: str) -> None:
    anchor = "start" if x < RIGHT - 32 else "end"
    label_x = x + 8 if anchor == "start" else x - 8
    for offset in (-12, 16, -29, 33, -46, 50, -63, 67):
        label_y = y + offset
        if not 14 <= label_y <= panel.bottom - 3:
            continue
        if any(abs(label_x - mark.x) < 29 and abs(label_y - mark.y) < 15 for mark in panel.marks):
            continue
        panel.rules.append(Rule(x, y, label_x, label_y - 4, color, "2 3", 0.6))
        panel.marks.append(Mark(label_x, label_y, f"#{seq}", color, anchor))
        break


def prepare_valuation(
    series_list: list[ValueSeries], kind: ValueKind, label: str, *, failures: list[str] | None = None
) -> ChartPage | str:
    datasets = [
        (series, sorted((point for point in series.points if isfinite(point.value)), key=lambda point: point.day))
        for series in series_list
    ]
    datasets = [(series, points) for series, points in datasets if points]
    if not datasets:
        return "没有可绘制的估值数据"
    days = sorted({point.day for _, points in datasets for point in points})
    is_yield = kind == ValueKind.DY
    unit = "%" if is_yield else "倍"
    x = Scale(days[0].toordinal(), days[-1].toordinal(), LEFT, RIGHT)
    day_ticks = date_ticks([datetime.combine(day, time.min) for day in days], [x(day.toordinal()) for day in days])
    panel = Panel(f"{label} · {unit}", 475, [], day_ticks)
    lo, hi = bounds([point.value for _, points in datasets for point in points])
    y = panel_scale(panel, lo, hi, lambda value: f"{value:g}{unit if is_yield else ''}")
    legend: list[Legend] = []
    endpoints: list[Mark] = []
    event_notes: list[str] = []
    for i, (series, points) in enumerate(datasets):
        name = series.symbol.display_name or series.symbol.code
        label_with_code = f"{name} ({series.symbol.code})" if name != series.symbol.code else name
        color = SERIES[i % len(SERIES)]
        first, last = points[0], points[-1]
        actual = {point.day: point.value for point in points}
        # 股息率没有有效分红报告期的交易日保留为空，不用首尾值填补。
        observed_days = set(days if is_yield else actual)
        observed_days.update(day for day in series.missing_days if days[0] <= day <= days[-1])
        axis_days = sorted(observed_days)
        xs, ys = _point_path(actual, axis_days, x, break_long_gaps=is_yield)
        add_series(panel, xs, ys, y, color, end_dot=True, width=2)
        if is_yield:
            for pos, value in enumerate(ys):
                if (
                    value is not None
                    and (pos == 0 or ys[pos - 1] is None)
                    and (pos == len(ys) - 1 or ys[pos + 1] is None)
                ):
                    panel.dots.append(Dot(xs[pos], y(value), color, 2.4))
        end = f"{number_text(last.value)}{unit}"
        endpoints.append(Mark(x(last.day.toordinal()), y(last.value), end, color))
        change = (last.value / first.value - 1) * 100 if first.value else None
        legend.append(
            Legend(
                label_with_code,
                color,
                end,
                f"{first.day:%Y-%m-%d} 起点 {number_text(first.value)}{unit} · "
                f"{last.day:%Y-%m-%d} · 区间 {percent_text(change)}",
            )
        )
        if is_yield:
            point_days = [point.day for point in points]
            for event in _events(points):
                pos = bisect_right(point_days, event.ex_date) - 1
                marker_x, marker_y = x(event.ex_date.toordinal()), y(points[pos].value)
                seq = len(event_notes) + 1
                panel.dots.append(Dot(marker_x, marker_y, color, 4.6))
                _event_mark(panel, marker_x, marker_y, seq, color)
                bonus = f"{event.bonus_per_share:g}元" if event.bonus_per_share is not None else "—"
                contribution = percent_text(event.contribution_pct)
                event_notes.append(
                    f"#{seq}  {name} · 除权 {event.ex_date:%Y-%m-%d} · 每股 {bonus} · 贡献 {contribution}"
                )
    add_end_labels(panel, endpoints)
    notes = [f"未获取 · {failure}" for failure in failures or []]
    if event_notes:
        notes.extend(event_notes)
    return ChartPage(
        f"{label}历史走势对比",
        "估值观察",
        f"{days[0]:%Y-%m-%d} — {days[-1]:%Y-%m-%d} · {len(datasets)} 只标的",
        [panel],
        source_footer(*(series.provider for series, _ in datasets)),
        legend,
        notes,
    )
