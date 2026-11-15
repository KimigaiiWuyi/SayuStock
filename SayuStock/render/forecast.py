"""Kronos history, backtest and prediction without filling missing model outputs."""

from datetime import datetime
from dataclasses import dataclass
from collections.abc import Sequence

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from .charts import Scale
from .palette import BLUE, GREEN, ORANGE
from .chart_page import (
    TOP,
    LEFT,
    RIGHT,
    Fill,
    Mark,
    Rule,
    Tick,
    Panel,
    Legend,
    ChartPage,
    Rectangle,
    bounds,
    finite,
    add_series,
    date_ticks,
    number_text,
    panel_scale,
    sample_indices,
)

Values = Sequence[object] | NDArray[np.floating]


@dataclass(slots=True)
class ForecastTrace:
    dates: list[datetime]
    mean: list[float | None]
    low: list[float | None]
    high: list[float | None]

    def extents(self) -> list[float | None]:
        return [*self.mean, *self.low, *self.high]


def _stamp(value: object) -> datetime | None:
    if value is pd.NaT:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.to_pydatetime().replace(tzinfo=None) if isinstance(parsed, pd.Timestamp) else None


def _trace(times: Sequence[object], mean: Values, low: Values = (), high: Values = ()) -> ForecastTrace:
    dates: list[datetime] = []
    values: list[float | None] = []
    lower: list[float | None] = []
    upper: list[float | None] = []
    has_range = len(low) == len(high) == len(times)
    for i, (time, value) in enumerate(zip(times, mean)):
        stamp = _stamp(time)
        if stamp is not None:
            dates.append(stamp)
            values.append(finite(value))
            lower.append(finite(low[i]) if has_range else None)
            upper.append(finite(high[i]) if has_range else None)
    return ForecastTrace(dates, values, lower, upper)


def _band(panel: Panel, xs: list[float], trace: ForecastTrace, scale: Scale, color: str) -> None:
    run: list[tuple[float, float, float]] = []

    def close() -> None:
        if len(run) > 1:
            top = " ".join(f"{x:.1f},{hi:.1f}" for x, _, hi in run)
            bottom = " ".join(f"{x:.1f},{lo:.1f}" for x, lo, _ in reversed(run))
            panel.fills.append(Fill(f"M {top} L {bottom} Z", color, 0.22))
        run.clear()

    for x, low, high in zip(xs, trace.low, trace.high, strict=True):
        if low is not None and high is not None:
            run.append((x, scale(low), scale(high)))
        else:
            close()
    close()


def prepare_forecast(
    *,
    title: str,
    tokenizer: str = "",
    model: str = "",
    device: str = "",
    source: str = "",
    period: str = "",
    hist_t: Sequence[object],
    hist_y: Values,
    backtest_t: Sequence[object],
    backtest_mean: Values,
    backtest_min: Values,
    backtest_max: Values,
    future_t: Sequence[object],
    future_mean: Values,
    future_min: Values,
    future_max: Values,
    last_close: float,
    backtest_start: object,
    future_start: object,
) -> ChartPage | str:
    hist = _trace(hist_t, hist_y)
    bt = _trace(backtest_t, backtest_mean, backtest_min, backtest_max)
    fu = _trace(future_t, future_mean, future_min, future_max)
    dates = sorted(set(hist.dates + bt.dates + fu.dates))
    if not dates or all(value is None for value in hist.mean + bt.mean + fu.mean):
        return "没有可绘制的预测数据"
    offsets = {stamp: (stamp - dates[0]).total_seconds() for stamp in dates}
    x = Scale(0, offsets[dates[-1]], LEFT, RIGHT)
    xs = [x(offsets[date]) for date in dates]
    axis = date_ticks(dates, xs, intraday=dates[0].date() == dates[-1].date())
    if dates[0].year != dates[-1].year:
        axis = [Tick(xs[i], dates[i].strftime("%Y-%m-%d")) for i in sample_indices(len(xs), 7)]
    panel = Panel("价格走势", 510, [], axis)
    minimum, maximum = bounds([*hist.extents(), *bt.extents(), *fu.extents(), finite(last_close)])
    y = panel_scale(panel, minimum, maximum, lambda value: f"{value:.2f}")
    panel.legend.extend(
        [Legend("历史实际", BLUE), Legend("回测预测均值 / 范围", GREEN), Legend("未来预测均值 / 范围", ORANGE)]
    )
    for phase, when, color, label_y in (
        ("回测开始", _stamp(backtest_start), GREEN, TOP + 12),
        ("预测开始", _stamp(future_start), ORANGE, TOP + 29),
    ):
        if when is not None and dates[0] <= when <= dates[-1]:
            xpos = x((when - dates[0]).total_seconds())
            if phase == "预测开始":
                panel.backdrop.append(Rectangle(xpos, TOP, RIGHT - xpos, panel.bottom - TOP, ORANGE, 0.07))
            panel.rules.append(Rule(xpos, TOP, xpos, panel.bottom, color, "5 4", 0.8))
            stamp = when.strftime("%m-%d %H:%M" if when.hour or when.minute else "%Y-%m-%d")
            left_anchor = xpos < LEFT + 150
            panel.marks.append(
                Mark(
                    xpos + (7 if left_anchor else -7),
                    label_y,
                    f"{phase} {stamp}",
                    color,
                    "start" if left_anchor else "end",
                )
            )
    hist_x = [x(offsets[date]) for date in hist.dates]
    bt_x = [x(offsets[date]) for date in bt.dates]
    fu_x = [x(offsets[date]) for date in fu.dates]
    _band(panel, bt_x, bt, y, GREEN)
    _band(panel, fu_x, fu, y, ORANGE)
    add_series(panel, hist_x, hist.mean, y, BLUE, width=2, end_dot=True)
    add_series(panel, bt_x, bt.mean, y, GREEN, width=2, dash="3 4", end_dot=True)
    future_values = fu.mean
    if hist_x and fu_x:
        fu_x = [hist_x[-1], *fu_x]
        future_values = [finite(last_close), *future_values]
    add_series(panel, fu_x, future_values, y, ORANGE, width=2, end_dot=True)
    summary = [
        Legend("历史末价", BLUE, number_text(finite(last_close))),
    ]
    if bt.mean:
        summary.append(Legend("回测末点均值", GREEN, number_text(bt.mean[-1]), f"{bt.dates[-1]:%Y-%m-%d %H:%M}"))
    if fu.mean:
        summary.append(Legend("预测末点均值", ORANGE, number_text(fu.mean[-1]), f"{fu.dates[-1]:%Y-%m-%d %H:%M}"))
    note = " · ".join(
        value
        for value in (
            period,
            f"模型 {model}" if model else "",
            f"Tokenizer {tokenizer}" if tokenizer else "",
            f"设备 {device}" if device else "",
        )
        if value
    )
    footer = f"{source} · " if source else ""
    footer += "阴影为模型采样最小值—最大值"
    return ChartPage(title, "Kronos · 回测与预测", note, [panel], footer, summary)
