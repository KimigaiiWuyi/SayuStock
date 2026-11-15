"""Candles and technical panels using the upstream indicator definitions."""

from datetime import datetime
from itertools import chain
from collections.abc import Sequence

import pandas as pd

from ..utils import indicators as ind
from .charts import Scale, ticks, bar_width
from .palette import UP, RED, SKY, BLUE, DOWN, FLAT, PINK, TEAL, AMBER, GREEN, LILAC, MUTED, VIOLET, YELLOW
from .chart_page import (
    TOP,
    LEFT,
    Dot,
    Fill,
    Mark,
    Rule,
    Tick,
    Panel,
    Legend,
    Stroke,
    Callout,
    ChartPage,
    Rectangle,
    bounds,
    plot_x,
    numbers,
    add_series,
    date_ticks,
    last_value,
    timestamps,
    number_text,
    panel_scale,
    plot_bounds,
    frame_column,
    compact_number,
)
from ..utils.constant import ErroText
from ..utils.render_data import build_kline_render_data
from ..utils.market.enums import KlinePeriod
from ..utils.market.models import Bar, KlineSeries
from ..utils.market.display import source_footer

KLINE_WIDTH = 1600
_, KLINE_RIGHT = plot_bounds(KLINE_WIDTH)

PERIOD_NAMES = {
    KlinePeriod.M5: "5分钟K线",
    KlinePeriod.M15: "15分钟K线",
    KlinePeriod.M30: "30分钟K线",
    KlinePeriod.M60: "60分钟K线",
    KlinePeriod.D1: "日K线",
    KlinePeriod.D1_RECENT: "日K线",
    KlinePeriod.D1_YEAR: "日K线",
    KlinePeriod.W1: "周K线",
    KlinePeriod.MON1: "月K线",
    KlinePeriod.Q1: "季K线",
    KlinePeriod.H1: "半年K线",
    KlinePeriod.Y1: "年K线",
}
MINUTE_PERIODS = {KlinePeriod.M5, KlinePeriod.M15, KlinePeriod.M30, KlinePeriod.M60}


def _legend_value(values: Sequence[float | None], multiplier: float, suffix: str) -> str:
    # 末点缺失时显示缺失，不能用前一点冒充最新指标。
    value = values[-1] if values else None
    return number_text(value * multiplier if value is not None else None) + (suffix if value is not None else "")


def _indicator_line(
    panel: Panel,
    xs: list[float],
    values: list[float | None],
    scale: Scale,
    label: str,
    color: str,
    *,
    multiplier: float = 1.0,
    suffix: str = "",
) -> None:
    panel.legend.append(Legend(label, color, _legend_value(values, multiplier, suffix)))
    add_series(panel, xs, values, scale, color)


def _band(
    panel: Panel,
    xs: list[float],
    scale: Scale,
    lows: list[float | None],
    highs: list[float | None],
    color: str,
) -> None:
    valid = [
        (x, scale(low), scale(high))
        for x, low, high in zip(xs, lows, highs, strict=True)
        if low is not None and high is not None
    ]
    if not valid:
        return
    top = " ".join(f"{x:.2f},{high:.2f}" for x, _, high in valid)
    bottom = " ".join(f"{x:.2f},{low:.2f}" for x, low, _ in reversed(valid))
    panel.fills.append(Fill(f"M {top} L {bottom} Z", color, 0.12))
    add_series(panel, xs, lows, scale, color, width=0.9, dash="3 3")
    add_series(panel, xs, highs, scale, color, width=0.9, dash="3 3")


def _price_panel(
    xs: list[float],
    frame: pd.DataFrame,
    highs: list[float | None],
    lows: list[float | None],
    close: pd.Series,
    volumes: list[float | None],
) -> Panel:
    panel = Panel("价格 · OHLC / 成交量", 560, [])
    opens = numbers(frame_column(frame, "open"))
    values = numbers(close)
    _, upper2, lower2 = ind.boll(close, 60, 2)
    _, upper3, lower3 = ind.boll(close, 60, 3)
    lo, hi = bounds([*lows, *highs, *numbers(lower3), *numbers(upper3)])
    price_bottom = panel.bottom - 108
    scale = Scale(lo, hi, price_bottom, TOP)
    panel.axis = [Tick(scale(value), f"{value:.2f}") for value in ticks(lo, hi, 6)]
    _band(panel, xs, scale, numbers(lower3), numbers(upper3), VIOLET)
    _band(panel, xs, scale, numbers(lower2), numbers(upper2), RED)

    width = bar_width(len(xs), KLINE_RIGHT - LEFT, 0.72)
    colors: list[str] = []
    wicks: dict[str, list[str]] = {UP: [], DOWN: [], FLAT: []}
    candles: list[Rectangle] = []
    for x, start, high, low, end in zip(xs, opens, highs, lows, values, strict=True):
        if start is None or high is None or low is None or end is None:
            colors.append(FLAT)
            continue
        color = UP if end > start else DOWN if end < start else FLAT
        colors.append(color)
        wicks[color].append(f"M{x:.2f},{scale(high):.2f} L{x:.2f},{scale(low):.2f}")
        y0, y1 = sorted((scale(start), scale(end)))
        candles.append(Rectangle(x - width / 2, y0, width, max(y1 - y0, 0.8), color, 0.90))

    max_volume = max((value for value in volumes if value is not None), default=0.0)
    if max_volume > 0:
        vol_scale = Scale(0, max_volume * 1.08, panel.bottom, panel.bottom - 120)
        panel.right_axis = [
            Tick(vol_scale(value), compact_number(value)) for value in (0.0, max_volume / 2, max_volume)
        ]
        for x, value, color in zip(xs, volumes, colors, strict=True):
            if value is not None and value > 0:
                y = vol_scale(value)
                panel.rects.append(Rectangle(x - width / 2, y, width, panel.bottom - y, color, 0.36))
    panel.rects.extend(candles)

    for color, paths in wicks.items():
        panel.series.append(Stroke(" ".join(paths), color, 0.9, opacity=0.85))
    for period, color in ((5, BLUE), (10, TEAL), (20, PINK), (60, LILAC)):
        _indicator_line(panel, xs, numbers(ind.ma(close, period)), scale, f"MA{period}", color)
    _indicator_line(panel, xs, numbers(ind.bbi(close)), scale, "BBI", YELLOW)
    last_vol = last_value(volumes)
    vol_color = colors[-1] if colors else FLAT
    panel.legend.extend(
        [
            Legend("BOLL60 · 2σ", RED),
            Legend("BOLL60 · 3σ", VIOLET),
            Legend("成交量", vol_color, compact_number(last_vol) if last_vol is not None else "—"),
        ]
    )
    return panel


def _peak_index(values: Sequence[float | None]) -> int | None:
    valid = [(idx, value) for idx, value in enumerate(values) if value is not None]
    return max(valid, key=lambda pair: pair[1])[0] if valid else None


def _place_peak_mark(panel: Panel, x: float, y: float, label: str, color: str, *, other: Mark | None = None) -> Mark:
    panel.dots.append(Dot(x, y, color, 3.2))
    anchor = "start" if x < KLINE_RIGHT - 135 else "end"
    lx = x + 8 if anchor == "start" else x - 8
    top_y, bot_y = float(TOP + 14), float(panel.bottom - 6)
    ly = min(bot_y, max(top_y, y - 8 if y - 8 >= top_y else y + 15))
    if other is not None and abs(lx - other.x) < 130 and abs(ly - other.y) < 18:
        alt = other.y + 19 if other.y + 19 <= bot_y else other.y - 19
        ly = min(bot_y, max(top_y, alt))
    mark = Mark(lx, ly, label, color, anchor)
    panel.marks.append(mark)
    return mark


def _turnover_panel(xs: list[float], turn: list[float | None], cmf: list[float | None]) -> Panel:
    panel = Panel("换手率（左） · CMF（右）", 145, [])
    max_turnover = max((abs(value) for value in turn if value is not None), default=0)
    max_cmf = max((abs(value) for value in cmf if value is not None), default=0)
    turn_limit = max(max_turnover * 1.25, 0.01)
    cmf_limit = max(max_cmf * 1.25, 0.2)
    turn_scale = panel_scale(panel, -turn_limit, turn_limit, lambda value: f"{value:g}%", count=4)
    cmf_scale = panel_scale(panel, -cmf_limit, cmf_limit, lambda value: f"{value * 100:g}%", count=5, right=True)
    panel.rules.append(Rule(LEFT, turn_scale(0), KLINE_RIGHT, turn_scale(0)))
    _indicator_line(panel, xs, turn, turn_scale, "换手率", VIOLET, suffix="%")
    _indicator_line(panel, xs, cmf, cmf_scale, "CMF20", GREEN, multiplier=100, suffix="%")

    turn_mark: Mark | None = None
    if (idx := _peak_index(turn)) is not None and (val := turn[idx]) is not None:
        turn_mark = _place_peak_mark(panel, xs[idx], turn_scale(val), f"换手率 {val:.2f}%", VIOLET)
    if (idx := _peak_index(cmf)) is not None and (val := cmf[idx]) is not None:
        _place_peak_mark(panel, xs[idx], cmf_scale(val), f"CMF {val * 100:.2f}%", GREEN, other=turn_mark)
    return panel


def _zones(panel: Panel, scale: Scale) -> None:
    for start, end, color in ((80, 100, UP), (0, 20, DOWN)):
        panel.backdrop.append(Rectangle(LEFT, scale(end), KLINE_RIGHT - LEFT, scale(start) - scale(end), color, 0.10))
    for value, color in ((20, DOWN), (80, UP)):
        panel.rules.append(Rule(LEFT, scale(value), KLINE_RIGHT, scale(value), color, "4 5", 0.55))


def _rsi_panel(xs: list[float], close: pd.Series) -> Panel:
    panel = Panel("RSI · 20 / 80", 155, [])
    scale = panel_scale(panel, 0, 100, lambda value: f"{value:g}", count=4)
    _zones(panel, scale)
    panel.callouts.extend(
        [
            Callout(scale(90), "超买区 >80：提防追高/逢高减仓", UP),
            Callout(scale(10), "超卖区 <20：避免恐慌割肉/关注反弹", DOWN),
        ]
    )
    for period, color in ((6, BLUE), (12, AMBER), (24, PINK)):
        _indicator_line(panel, xs, numbers(ind.rsi(close, period)), scale, f"RSI{period}", color)
    return panel


def _kdj_panel(xs: list[float], high: pd.Series, low: pd.Series, close: pd.Series) -> Panel:
    panel = Panel("KDJ · 9 / 3 / 3", 145, [])
    k, d, j = [numbers(values) for values in ind.kdj(high, low, close)]
    ymin, ymax = bounds([0, 100, *k, *d, *j], minimum=4)
    scale = panel_scale(panel, ymin, ymax, lambda value: f"{value:g}", count=4)
    width = (KLINE_RIGHT - LEFT) / len(xs)
    for x, k_value, d_value in zip(xs, k, d, strict=True):
        if k_value is not None and d_value is not None:
            panel.backdrop.append(
                Rectangle(x - width / 2, TOP, width, panel.bottom - TOP, UP if k_value >= d_value else DOWN, 0.08)
            )
    _zones(panel, scale)
    panel.rules.append(Rule(LEFT, scale(50), KLINE_RIGHT, scale(50), MUTED, "4 5", 0.55))
    for label, values, color in (("K", k, AMBER), ("D", d, SKY), ("J", j, PINK)):
        _indicator_line(panel, xs, values, scale, label, color)
    return panel


def _macd_panel(xs: list[float], close: pd.Series, dates: list[datetime], *, intraday: bool) -> Panel:
    panel = Panel("MACD · 12 / 26 / 9", 175, [], date_ticks(dates, xs, intraday=intraday))
    dif, dea, histogram = [numbers(values) for values in ind.macd(close)]
    lo, hi = bounds(list(chain([0.0], dif, dea, histogram)))
    scale = panel_scale(panel, lo, hi, lambda value: f"{value:g}", count=3)
    baseline = scale(0)
    width = bar_width(len(xs), KLINE_RIGHT - LEFT, 0.76)
    panel.rules.append(Rule(LEFT, baseline, KLINE_RIGHT, baseline, FLAT))
    for x, value in zip(xs, histogram, strict=True):
        if value is not None and value != 0:
            y = scale(value)
            panel.rects.append(
                Rectangle(x - width / 2, min(y, baseline), width, abs(baseline - y), UP if value > 0 else DOWN, 0.75)
            )
    _indicator_line(panel, xs, dif, scale, "DIF", YELLOW)
    _indicator_line(panel, xs, dea, scale, "DEA", BLUE)
    panel.legend.append(Legend("MACD 柱", UP, _legend_value(histogram, 1, "")))
    return panel


def _bar_key(stamp: datetime, *, intraday: bool) -> datetime:
    stamp = stamp.replace(tzinfo=None, second=0, microsecond=0)
    return stamp if intraday else stamp.replace(hour=0, minute=0)


def prepare_kline(series: KlineSeries) -> ChartPage | str:
    data = build_kline_render_data(series)
    if isinstance(data, str):
        return data
    frame = data.chart_df
    dates = timestamps(frame_column(frame, "date"))
    if not dates:
        return ErroText["notData"]
    xs = plot_x(len(dates), bars=True, right=KLINE_RIGHT)
    close = frame_column(frame, "close")
    high = frame_column(frame, "high")
    low = frame_column(frame, "low")
    volume = frame_column(frame, "volume")
    intraday = series.period in MINUTE_PERIODS
    bars_by_time: dict[datetime, Bar] = {_bar_key(bar.ts, intraday=intraday): bar for bar in series.bars}
    actual_bars = [bars_by_time[_bar_key(stamp, intraday=intraday)] for stamp in dates]
    panels = [
        _price_panel(xs, frame, numbers(high), numbers(low), close, numbers(volume)),
        _turnover_panel(xs, [bar.turnover_rate for bar in actual_bars], numbers(ind.cmf(high, low, close, volume, 20))),
        _rsi_panel(xs, close),
        _kdj_panel(xs, high, low, close),
        _macd_panel(xs, close, dates, intraday=intraday),
    ]
    title = f"{series.symbol.display_name or series.symbol.code}  {PERIOD_NAMES[series.period]}"
    begin = dates[0].strftime("%Y-%m-%d")
    end = dates[-1].strftime("%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d")
    last = last_value(numbers(close))
    amount = actual_bars[-1].amount
    subtitle = f"{begin} — {end} · {len(dates)} 根 · 最新收盘 {number_text(last)}"
    if amount is not None:
        subtitle += f" · 成交额 {compact_number(amount)}"
    if series.adjusted:
        subtitle += " · 复权"
    return ChartPage(title, "技术走势", subtitle, panels, source_footer(series.provider), width=KLINE_WIDTH)
