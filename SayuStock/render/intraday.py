"""Single/multi-symbol session charts from the shared intraday projection."""

from datetime import datetime, timedelta

import pandas as pd

from .charts import Scale, bar_width, split_area_paths
from .palette import UP, BLUE, DOWN, FLAT, GRID, TEXT, AMBER, SERIES
from .chart_page import (
    TOP,
    LEFT,
    RIGHT,
    Dot,
    Fill,
    Mark,
    Rule,
    Tick,
    Panel,
    Legend,
    ChartPage,
    Rectangle,
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
    compact_number,
)
from ..utils.constant import ErroText
from ..utils.render_data import _intraday_day_axis, build_multi_stock_render_data, build_single_stock_render_data
from ..utils.market.models import IntradaySeries
from ..utils.market.display import source_footer

LINE_HEIGHT = 400
MONEY_HEIGHT = 168


def _timeline(xs: list[float], dates: list[datetime], positions: list[int], labels: list[str]) -> list[Tick]:
    if not positions:
        return date_ticks(dates, xs, intraday=True)
    return [Tick(xs[i], *label.split("\n", 1)) for i, label in zip(positions, labels, strict=True)]


def _day_rules(panel: Panel, xs: list[float], starts: list[int]) -> None:
    for day, start in enumerate(starts):
        x0 = (xs[start - 1] + xs[start]) / 2 if start > 0 else LEFT
        x1 = (xs[starts[day + 1] - 1] + xs[starts[day + 1]]) / 2 if day + 1 < len(starts) else RIGHT
        if day % 2:
            panel.backdrop.append(Rectangle(x0, TOP, x1 - x0, panel.bottom - TOP, GRID, 0.28))
        if start:
            panel.rules.append(Rule(x0, TOP, x0, panel.bottom, FLAT, "3 4", 0.4))


def _split_gaps(
    dates: list[datetime], xs: list[float], values: list[float | None]
) -> tuple[list[float], list[float | None]]:
    line_x: list[float] = []
    line_y: list[float | None] = []
    for i, (stamp, x, value) in enumerate(zip(dates, xs, values, strict=True)):
        if i and stamp - dates[i - 1] > timedelta(minutes=1):
            line_x.append((xs[i - 1] + x) / 2)
            line_y.append(None)
        line_x.append(x)
        line_y.append(value)
    return line_x, line_y


def _intraday_curve(
    panel: Panel,
    dates: list[datetime],
    xs: list[float],
    values: list[float | None],
    scale: Scale,
    color: str,
    *,
    area: bool = False,
) -> None:
    line_x, line_y = _split_gaps(dates, xs, values)
    add_series(panel, line_x, line_y, scale, color, width=2.1, end_dot=True)
    for i, value in enumerate(line_y):
        if value is not None and (i == 0 or line_y[i - 1] is None) and (i == len(line_y) - 1 or line_y[i + 1] is None):
            panel.dots.append(Dot(line_x[i], scale(value), color, 2.2))
    if area:
        pixel_y = [scale(value) if value is not None else None for value in line_y]
        up, down = split_area_paths(line_x, pixel_y, scale(0))
        panel.fills.extend([Fill(up, UP, 0.18), Fill(down, DOWN, 0.18)])


def _money_bars(
    panel: Panel,
    xs: list[float],
    amount_by_series: list[list[float | None]],
    colors: list[str] | list[list[str]],
) -> None:
    totals = [sum(value or 0 for value in bucket) for bucket in zip(*amount_by_series, strict=True)]
    hi = max(totals, default=0)
    if hi <= 0:
        panel.empty = "暂无可显示的分钟成交额"
        return
    scale = panel_scale(panel, 0, hi * 1.12, compact_number, count=3)
    width = bar_width(len(xs), RIGHT - LEFT, 0.78)
    previous = [0.0] * len(xs)
    for amounts, colors_for_series in zip(amount_by_series, colors, strict=True):
        for i, (x, amount) in enumerate(zip(xs, amounts, strict=True)):
            if amount is None or amount <= 0:
                continue
            base = previous[i]
            top = scale(base + amount)
            color = colors_for_series if isinstance(colors_for_series, str) else colors_for_series[i]
            panel.rects.append(Rectangle(x - width / 2, top, width, scale(base) - top, color, 0.72))
            previous[i] += amount


def _last_stamp(series: IntradaySeries) -> str:
    stamp = max((point.ts for point in series.points), default=None)
    return stamp.strftime("%Y-%m-%d %H:%M") if stamp is not None else ""


def prepare_single_intraday(series: IntradaySeries) -> ChartPage | str:
    data = build_single_stock_render_data(series)
    if isinstance(data, str):
        return data
    dates = timestamps(frame_column(data.df, "dt"))
    values = numbers(frame_column(data.df, "percentage_change"))
    if not dates or not any(value is not None for value in values):
        return ErroText["notData"]
    xs = plot_x(len(dates), bars=True)
    timeline = _timeline(xs, dates, data.day_tick_positions, data.day_tick_labels)
    price = Panel("价格走势 · 相对基准", LINE_HEIGHT, [])
    money = Panel("成交额", MONEY_HEIGHT, [], timeline)
    limit = max(data.max_fluctuation * 100 + 1, 1.0)
    scale = panel_scale(price, -limit, limit, lambda value: f"{value:g}%")
    quote_price = data.new_price
    digits = 3 if quote_price is not None and abs(quote_price - round(quote_price, 2)) > 1e-8 else 2
    price.right_axis = [
        Tick(tick.pos, number_text(data.open_price * (1 + float(tick.label[:-1]) / 100), digits)) for tick in price.axis
    ]
    price.rules.append(Rule(LEFT, scale(0), RIGHT, scale(0)))
    for panel in (price, money):
        _day_rules(panel, xs, data.day_starts)
    _intraday_curve(price, dates, xs, values, scale, TEXT, area=True)
    bar_colors = [UP if color == "red" else DOWN if color == "green" else FLAT for color in data.bar_colors]
    amounts = [
        amount if value is not None else None
        for amount, value in zip(numbers(frame_column(data.df, "money")), values, strict=True)
    ]
    _money_bars(money, xs, [amounts], [bar_colors])
    quote = series.quote
    footer = source_footer(series.provider, quote.provider if quote is not None else None)
    title = f"{data.stock_name}  {data.stock_code}"
    legend = [
        Legend("最新价", TEXT, number_text(quote_price, digits)),
        Legend("今日涨跌", _change_color(data.today_change), percent_text(data.today_change)),
    ]
    if data.ndays > 1:
        legend.append(Legend(f"{data.ndays}日累计", _change_color(data.ndays_change), percent_text(data.ndays_change)))
    if data.ndays > 1:
        reference = "窗口前收" if series.ref_close else "首日收盘"
    elif quote is not None and quote.prev_close:
        reference = "昨收"
    else:
        reference = "开盘参考"
    legend.extend(
        [
            Legend(reference, AMBER, number_text(data.open_price, digits)),
            Legend("成交额", BLUE, data.total_amount, f"换手率 {percent_text(data.turnover_rate).lstrip('+')}"),
        ]
    )
    return ChartPage(title, f"{data.ndays}日分时", f"分时截至 {_last_stamp(series)}", [price, money], footer, legend)


def _change_color(value: float) -> str:
    return UP if value > 0 else DOWN if value < 0 else FLAT


def _day_boundaries(
    dates: list[datetime], first: list[float | None], first_source: IntradaySeries | None
) -> tuple[list[int], list[int], list[str]]:
    code = first_source.symbol.provider_symbol or first_source.symbol.code if first_source is not None else ""
    prev = first_source.ref_close if first_source is not None else None
    return _intraday_day_axis(pd.Series(dates), pd.Series(first, dtype=float), code_id=code, prev_close=prev)


def prepare_multi_intraday(series_list: list[IntradaySeries]) -> ChartPage | str:
    data = build_multi_stock_render_data(series_list)
    if isinstance(data, str):
        return data
    dates_by_stock = [timestamps(frame_column(item.df, "dt")) for item in data.stocks]
    dates = sorted({stamp for series_dates in dates_by_stock for stamp in series_dates})
    if not dates:
        return ErroText["notData"]
    positions = {stamp: i for i, stamp in enumerate(dates)}
    xs = plot_x(len(dates), bars=True)
    colors = [SERIES[i % len(SERIES)] for i in range(len(data.stocks))]
    percentages: list[list[float | None]] = []
    amounts: list[list[float | None]] = []
    prices: list[list[float | None]] = []
    for item, series_dates in zip(data.stocks, dates_by_stock, strict=True):
        to_align = [
            numbers(frame_column(item.df, "percentage_change")),
            numbers(frame_column(item.df, "money")),
            numbers(frame_column(item.df, "price")),
        ]
        for values, destination in zip(to_align, (percentages, amounts, prices), strict=True):
            aligned: list[float | None] = [None] * len(dates)
            for stamp, value in zip(series_dates, values, strict=True):
                aligned[positions[stamp]] = value
            destination.append(aligned)
    ndays = max((series.ndays for series in series_list), default=1)
    starts: list[int] = []
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    if ndays > 1:
        starts, tick_positions, tick_labels = _day_boundaries(dates, prices[0], data.stocks[0].source)
        tick_labels = [label.split("\n", 1)[0] for label in tick_labels]
    price = Panel("涨跌幅", LINE_HEIGHT, [])
    money = Panel("成交额 · 分标的堆叠", MONEY_HEIGHT, [], _timeline(xs, dates, tick_positions, tick_labels))
    scale = panel_scale(price, data.y_axis_min, data.y_axis_max, lambda value: f"{value:g}%")
    price.rules.append(Rule(LEFT, scale(0), RIGHT, scale(0)))
    for panel in (price, money):
        _day_rules(panel, xs, starts)
    legend: list[Legend] = []
    entries: list[Mark] = []
    providers: list[str | None] = []
    for item, color, pct, px in zip(data.stocks, colors, percentages, prices, strict=True):
        _intraday_curve(price, dates, xs, pct, scale, color)
        last = next(((i, value) for i, value in reversed(list(enumerate(pct))) if value is not None), None)
        if last is None:
            continue
        pos, change = last
        entries.append(Mark(xs[pos], scale(change), percent_text(change), color))
        detail = f"基准 {number_text(item.baseline, 3)} → {number_text(px[pos], 3)}"
        if ndays > 1:
            detail += f" · 今日 {percent_text(item.today_change)}"
        legend.append(Legend(item.name, color, percent_text(change), detail))
        if item.source is not None:
            providers.append(item.source.provider)
            if item.source.quote is not None:
                providers.append(item.source.quote.provider)
    if not legend:
        return ErroText["notData"]
    add_end_labels(price, entries)
    _money_bars(money, xs, amounts[::-1], colors[::-1])
    suffix = f" · {ndays}日累计 / 今日" if ndays > 1 else ""
    date_range = f"{dates[0]:%Y-%m-%d} — {dates[-1]:%Y-%m-%d}"
    return ChartPage(
        "分时涨跌幅对比",
        f"{ndays}日分时",
        f"{date_range} · {len(legend)} 只标的{suffix}",
        [price, money],
        source_footer(*providers),
        legend,
    )
