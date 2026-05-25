# pyright: reportMissingTypeStubs=false, reportMissingImports=false
"""基于 mplchart/matplotlib 的 stock_cloudmap 渲染实现。"""

import asyncio
from io import BytesIO
from typing import TypeVar, Protocol, ParamSpec, cast
from pathlib import Path
from datetime import datetime, timedelta
from collections.abc import Callable, Sequence, Awaitable

import numpy as np
import pandas as pd
import matplotlib
from PIL import Image
from numpy.typing import NDArray

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mplchart.chart import Chart  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from mplchart.indicators import EMA, SMA, MACD, BBANDS  # noqa: E402
from mplchart.primitives import Pane, HLine, Price, Volume, BarPlot, LinePlot, Candlesticks  # noqa: E402

from gsuid_core.logger import logger as _logger
from gsuid_core.utils.image.convert import convert_img as _convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return as _ai_return

from .data import CLOUDMAP_DATA_SERVICE
from .render_data import (
    MultiStockItem,
    KlineRenderData,
    CompareRenderData,
    CloudmapRenderData,
    MultiStockRenderData,
    SingleStockRenderData,
    build_kline_render_data,
    build_compare_render_data,
    build_cloudmap_render_data,
    build_multi_stock_render_data,
    build_single_stock_render_data,
)
from ..utils.constant import ErroText
from ..utils.stock.utils import get_file
from ..stock_config.stock_config import STOCK_CONFIG as _STOCK_CONFIG


class _LoggerProtocol(Protocol):
    def info(self, message: object) -> None: ...


class _ConfigItemProtocol(Protocol):
    data: int


class _StockConfigProtocol(Protocol):
    def get_config(self, key: str) -> _ConfigItemProtocol: ...


BotSendContent = str | bytes
DrawResult = str | Image.Image
JsonDict = dict[str, object]
P = ParamSpec("P")
R = TypeVar("R", bound=DrawResult)

logger = cast(_LoggerProtocol, _logger)
convert_img = cast(Callable[[bytes], Awaitable[bytes]], _convert_img)
ai_return = cast(Callable[[str], None], _ai_return)
STOCK_CONFIG = cast(_StockConfigProtocol, _STOCK_CONFIG)

UP_COLOR = "#e74c3c"
DOWN_COLOR = "#00b050"
FLAT_COLOR = "#7f8c8d"
BG_COLOR = "#050505"
FG_COLOR = "#f5f5f5"
AXIS_COLOR = "#d8d8d8"
GRID_COLOR = "#777777"
FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
MPL_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#17becf", "#e377c2"]


def _setup_mpl() -> None:
    plt.rcParams["font.sans-serif"] = FONT_CANDIDATES
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = BG_COLOR
    plt.rcParams["axes.facecolor"] = BG_COLOR
    plt.rcParams["savefig.facecolor"] = BG_COLOR


def _fig_to_image(fig: Figure, *, dpi: int = 180) -> Image.Image:
    output = BytesIO()
    fig.savefig(output, format="png", dpi=dpi, facecolor=fig.get_facecolor(), pad_inches=0.06)
    plt.close(fig)
    _ = output.seek(0)
    return Image.open(output).convert("RGB")


async def _draw_in_thread(func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    return await asyncio.to_thread(func, *args, **kwargs)


def _dict_value(data: JsonDict, key: str, default: object) -> object:
    if key in data:
        return data[key]
    return default


def _as_dict(value: object) -> JsonDict:
    if isinstance(value, dict):
        return cast(JsonDict, value)
    return {}


def _as_dict_list(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [_as_dict(item) for item in value if isinstance(item, dict)]


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _series_from_value(value: object) -> pd.Series:
    if isinstance(value, pd.Series):
        return value
    return pd.Series(value)


def _numeric_series(value: object, *, fill_value: float | None = None) -> pd.Series:
    series = cast(pd.Series, pd.to_numeric(_series_from_value(value), errors="coerce"))
    if fill_value is None:
        return series
    return cast(pd.Series, series.fillna(fill_value))


def _datetime_series(value: object) -> pd.Series:
    return cast(pd.Series, pd.to_datetime(_series_from_value(value), errors="coerce"))


def _timestamp_from_value(value: object) -> pd.Timestamp | None:
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, (str, int, float, datetime, np.datetime64)):
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError):
            return None
        if not isinstance(timestamp, pd.Timestamp):
            return None
        return timestamp
    return None


def _intraday_positions(df: pd.DataFrame, datetime_column: str = "dt") -> tuple[NDArray[np.float64], pd.Series]:
    datetimes = _datetime_series(df[datetime_column])
    return np.arange(len(datetimes), dtype=float), datetimes


def _apply_intraday_axis(ax: Axes, x_values: NDArray[np.float64], datetimes: pd.Series) -> None:
    if len(x_values) == 0:
        return
    tick_count = min(8, max(2, len(x_values) // 45 + 2))
    tick_indexes = np.linspace(0, len(x_values) - 1, tick_count, dtype=int)
    unique_tick_indexes = np.unique(tick_indexes)
    tick_labels: list[str] = []
    for index in unique_tick_indexes:
        timestamp = _timestamp_from_value(datetimes.iloc[int(index)])
        tick_labels.append(timestamp.strftime("%H:%M") if timestamp is not None else "")
    ax.set_xticks(x_values[unique_tick_indexes])
    ax.set_xticklabels(tick_labels)
    ax.set_xlim(float(x_values[0]) - 1.0, float(x_values[-1]) + 1.0)
    ax.margins(x=0.0)


def _format_money_axis(value: float, _pos: object = None) -> str:
    abs_value = abs(value)
    if abs_value >= 1e8:
        return f"{value / 1e8:.1f}亿"
    if abs_value >= 1e4:
        return f"{value / 1e4:.1f}万"
    return f"{value:.0f}"


def _format_percent_axis(value: float, _pos: object = None) -> str:
    return f"{value:.0f}%"


def _style_axis(ax: Axes, *, grid: bool = True) -> None:
    ax.set_facecolor(BG_COLOR)
    ax.tick_params(colors=AXIS_COLOR, labelsize=12)
    ax.xaxis.label.set_color(AXIS_COLOR)
    ax.yaxis.label.set_color(AXIS_COLOR)
    ax.title.set_color(FG_COLOR)
    for spine in ax.spines.values():
        spine.set_color(AXIS_COLOR)
        spine.set_linewidth(1.1)
    if grid:
        ax.grid(True, color=GRID_COLOR, alpha=0.36, linewidth=0.8)


def _mpl_bar_colors(colors: Sequence[str]) -> list[str]:
    return [UP_COLOR if item == "red" else DOWN_COLOR if item == "green" else FLAT_COLOR for item in colors]


def _date_index_positions(index: pd.Index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(index, errors="coerce"))


def _apply_intraday_10min_ticks(ax: Axes, index: pd.Index) -> None:
    dates = _date_index_positions(index)
    if dates.empty:
        return
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    for position, timestamp in enumerate(dates):
        if pd.isna(timestamp):
            continue
        if timestamp.minute % 10 == 0:
            tick_positions.append(position)
            tick_labels.append(timestamp.strftime("%H:%M"))
    if not tick_positions:
        tick_positions = [int(position) for position in np.linspace(0, len(dates) - 1, min(8, len(dates)), dtype=int)]
        tick_labels = []
        for position in tick_positions:
            timestamp = _timestamp_from_value(dates[position])
            tick_labels.append(timestamp.strftime("%H:%M") if timestamp is not None else "")
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)


def _apply_month_ticks(ax: Axes, index: pd.Index) -> None:
    dates = _date_index_positions(index)
    if dates.empty:
        return
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    previous_month: tuple[int, int] | None = None
    for position, timestamp in enumerate(dates):
        if pd.isna(timestamp):
            continue
        current_month = (timestamp.year, timestamp.month)
        if current_month != previous_month:
            tick_positions.append(position)
            tick_labels.append(timestamp.strftime("%Y-%m"))
            previous_month = current_month
    max_ticks = 10
    if len(tick_positions) > max_ticks:
        selected = np.linspace(0, len(tick_positions) - 1, max_ticks, dtype=int)
        tick_positions = [tick_positions[index] for index in selected]
        tick_labels = [tick_labels[index] for index in selected]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)


def _apply_intraday_kline_ticks(ax: Axes, index: pd.Index) -> None:
    dates = _date_index_positions(index)
    if dates.empty:
        return
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    previous_day: pd.Timestamp | None = None
    for position, timestamp in enumerate(dates):
        if pd.isna(timestamp):
            continue
        current_day = timestamp.normalize()
        if previous_day is None or current_day != previous_day:
            tick_positions.append(position)
            tick_labels.append(timestamp.strftime("%m-%d %H:%M"))
            previous_day = current_day
    max_ticks = 10
    if len(tick_positions) > max_ticks:
        selected = np.linspace(0, len(tick_positions) - 1, max_ticks, dtype=int)
        tick_positions = [tick_positions[i] for i in selected]
        tick_labels = [tick_labels[i] for i in selected]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)


def _axes_top_to_bottom(fig: Figure) -> list[Axes]:
    axes = [ax for ax in fig.axes if isinstance(ax, Axes)]
    return sorted(axes, key=lambda item: item.get_position().y0, reverse=True)


async def to_single_fig_kline(raw_data: JsonDict, sp: str | None = None) -> DrawResult:
    return await _draw_in_thread(draw_single_kline_chart, raw_data, sp)


def draw_single_kline_chart(raw_data: JsonDict, sp: str | None = None) -> DrawResult:
    _ = sp
    _setup_mpl()
    data = build_kline_render_data(raw_data)
    if isinstance(data, str):
        return data
    kline = cast(KlineRenderData, data)
    chart_df = cast(pd.DataFrame, kline.chart_df.copy())
    chart_df = cast(pd.DataFrame, chart_df.dropna(subset=["date", "open", "high", "low", "close"]))
    if chart_df.empty:
        return ErroText["notData"]

    dates = _datetime_series(chart_df["date"])
    valid_mask = dates.notna()
    turnover_source = chart_df["turnover"] if "turnover" in chart_df else pd.Series(0.0, index=chart_df.index)
    prices = pd.DataFrame(
        {
            "open": np.asarray(_numeric_series(chart_df["open"])[valid_mask]),
            "high": np.asarray(_numeric_series(chart_df["high"])[valid_mask]),
            "low": np.asarray(_numeric_series(chart_df["low"])[valid_mask]),
            "close": np.asarray(_numeric_series(chart_df["close"])[valid_mask]),
            "volume": np.asarray(_numeric_series(chart_df["volume"], fill_value=0)[valid_mask]),
            "turnover": np.asarray(_numeric_series(turnover_source, fill_value=0)[valid_mask]),
        },
        index=pd.DatetimeIndex(np.asarray(dates[valid_mask]), name="date"),
    )
    prices = cast(pd.DataFrame, prices.dropna(subset=["open", "high", "low", "close"]))
    if prices.empty:
        return ErroText["notData"]
    prices = cast(pd.DataFrame, prices.sort_index())

    chart = Chart(
        prices,
        title=kline.title,
        figsize=(25.5, 15.5),
        bgcolor=BG_COLOR,
        raw_dates=False,
        color_scheme={
            "colorup": UP_COLOR,
            "colordn": DOWN_COLOR,
            "bgcolor": BG_COLOR,
            "text": FG_COLOR,
            "grid": GRID_COLOR,
        },
    )
    chart.plot(
        Candlesticks(width=0.78, alpha=0.95, colorup=UP_COLOR, colordn=DOWN_COLOR),
        Volume(width=0.76, alpha=0.42, colorup=UP_COLOR, colordn=DOWN_COLOR),
        SMA(60),
        EMA(12),
        EMA(26),
        BBANDS(20, 2.0),
        BBANDS(60, 2.0),
        Pane("below", height_ratio=0.24),
        LinePlot(lambda frame: frame["turnover"], label="换手率", color="#d77cff", width=1.5),
        Pane("below", height_ratio=0.18),
        MACD(12, 26, 9) @ BarPlot(item="macdhist", color=AXIS_COLOR, alpha=0.68, width=0.76, label="MACD柱"),
        MACD(12, 26, 9) @ LinePlot(item="macd", label="DIF", color="#f1c40f", width=1.6),
        MACD(12, 26, 9) @ LinePlot(item="macdsignal", label="DEA", color="#4aa3ff", width=1.6),
    )
    chart.add_legends()

    fig = cast(Figure, chart.figure)
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for index, ax in enumerate(axes):
        _style_axis(ax)
        ax.tick_params(axis="x", rotation=16)
        ax.yaxis.label.set_color(AXIS_COLOR)
        if index < len(axes) - 1:
            ax.tick_params(labelbottom=False)
        else:
            if "min" in kline.freq_label or "H" in kline.freq_label:
                _apply_intraday_kline_ticks(ax, prices.index)
            else:
                _apply_month_ticks(ax, prices.index)
            ax.tick_params(labelbottom=True)
        if index == len(axes) - 2:
            ax.yaxis.set_major_formatter(FuncFormatter(_format_percent_axis))
        if index == len(axes) - 1:
            for patch in ax.patches:
                if not isinstance(patch, Rectangle):
                    continue
                height = patch.get_height()
                patch.set_facecolor(UP_COLOR if height >= 0 else DOWN_COLOR)
                patch.set_alpha(0.72)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(BG_COLOR)
            legend.get_frame().set_edgecolor(GRID_COLOR)
            for text in legend.get_texts():
                text.set_color(FG_COLOR)

    if axes:
        axes[0].set_title(kline.title, color=FG_COLOR, fontsize=24, fontweight="bold", pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.988, top=0.875, bottom=0.10, hspace=0.045)
    return _fig_to_image(fig)


async def to_single_fig(raw_data: JsonDict) -> DrawResult:
    return await _draw_in_thread(draw_single_stock_chart, raw_data)


def draw_single_stock_chart(raw_data: JsonDict) -> DrawResult:
    _setup_mpl()
    logger.info("[SayuStock] 开始获取图形...")
    data = build_single_stock_render_data(raw_data)
    if isinstance(data, str):
        return data
    stock = cast(SingleStockRenderData, data)
    df = stock.df

    datetimes = _datetime_series(df["dt"])
    percent_series = _numeric_series(df["percentage_change"])
    valid_time_mask = datetimes.notna()
    valid_data_mask = valid_time_mask & percent_series.notna()
    if not bool(valid_data_mask.any()):
        return ErroText["notData"]

    valid_percents = np.asarray(percent_series[valid_time_mask])
    valid_bar_colors = np.asarray(_mpl_bar_colors(stock.bar_colors), dtype=object)[np.asarray(valid_time_mask)]
    prices = pd.DataFrame(
        {
            "open": valid_percents,
            "high": valid_percents,
            "low": valid_percents,
            "close": valid_percents,
            "volume": np.asarray(_numeric_series(df["money"], fill_value=0)[valid_time_mask]),
            "bar_color": valid_bar_colors,
        },
        index=pd.DatetimeIndex(np.asarray(datetimes[valid_time_mask]), name="date"),
    )
    prices = cast(pd.DataFrame, prices.sort_index())

    title_color = UP_COLOR if stock.gained >= 0 else DOWN_COLOR
    chart = Chart(
        prices,
        title=stock.title_text,
        figsize=(22.2, 16.7),
        bgcolor=BG_COLOR,
        raw_dates=False,
        color_scheme={
            "colorup": UP_COLOR,
            "colordn": DOWN_COLOR,
            "bgcolor": BG_COLOR,
            "text": FG_COLOR,
            "grid": GRID_COLOR,
        },
    )
    chart.plot(
        Price("close", width=2.2, color="white"),
        HLine(0, color="#f1c40f", linestyle="-."),
        Pane("below", height_ratio=0.28),
        BarPlot(lambda frame: frame["volume"], color="#4aa3ff", alpha=0.72, width=0.82, label="量能"),
    )
    chart.add_legends()

    fig = cast(Figure, chart.figure)
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for index, ax in enumerate(axes):
        _style_axis(ax)
        ax.tick_params(axis="x", rotation=20)
        if index == 0:
            percent_limit = max(stock.max_fluctuation * 100 + 1.0, 1.0)
            ax.set_ylabel("涨跌幅")
            ax.set_ylim(-percent_limit, percent_limit)
            ax.patch.set_alpha(0.0)
            ax.axhspan(0, percent_limit, facecolor=UP_COLOR, alpha=0.16, zorder=0.2)
            ax.axhspan(-percent_limit, 0, facecolor=DOWN_COLOR, alpha=0.16, zorder=0.2)
            ax.set_axisbelow(False)
            tick_step = 2 if percent_limit > 8 else 1
            tick_start = int(np.floor(-percent_limit))
            tick_end = int(np.ceil(percent_limit))
            tick_values = [value for value in range(tick_start, tick_end + 1) if value % tick_step == 0]
            ax.set_yticks(tick_values)
            ax.set_yticklabels([f"{value}%" for value in tick_values])
            ax.tick_params(labelbottom=False)
        else:
            ax.set_ylabel("量能")
            ax.tick_params(labelbottom=True)
            ax.yaxis.set_major_formatter(FuncFormatter(_format_money_axis))
            _apply_intraday_10min_ticks(ax, prices.index)
            bar_colors = [str(value) for value in prices["bar_color"]]
            for patch in list(ax.patches):
                patch.remove()
            volume_values = np.asarray(prices["volume"], dtype=float)
            max_height = float(np.nanmax(volume_values)) if len(volume_values) > 0 else 0.0
            if max_height > 0:
                ax.set_ylim(0, max_height * 1.08)
                ax.margins(y=0.0)
                bars = ax.bar(
                    np.arange(len(volume_values)),
                    volume_values,
                    color=bar_colors,
                    edgecolor=bar_colors,
                    alpha=0.72,
                    width=0.82,
                    label="量能",
                    clip_on=True,
                    zorder=2,
                )
                for bar in bars:
                    bar.set_clip_on(True)
                    bar.set_clip_path(ax.patch)
                ax.set_autoscale_on(False)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(BG_COLOR)
            legend.get_frame().set_edgecolor(GRID_COLOR)
            for text in legend.get_texts():
                text.set_color(FG_COLOR)

    if axes:
        axes[0].set_title(stock.title_text, color=title_color, fontsize=24, fontweight="bold", pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.988, top=0.88, bottom=0.10, hspace=0.04)
    return _fig_to_image(fig)


async def to_multi_fig(raw_data_list: list[JsonDict]) -> DrawResult:
    return await _draw_in_thread(draw_multi_stock_chart, raw_data_list)


def draw_multi_stock_chart(raw_data_list: list[JsonDict]) -> DrawResult:
    _setup_mpl()
    logger.info("[SayuStock] Starting to generate multi-stock figure with multi-line title...")
    data = build_multi_stock_render_data(raw_data_list)
    if isinstance(data, str):
        return data
    multi = cast(MultiStockRenderData, data)

    base_df = multi.stocks[0].df
    datetimes = _datetime_series(base_df["dt"])
    valid_mask = datetimes.notna()
    price_columns: dict[str, NDArray[np.float64]] = {}
    volume_columns: dict[str, NDArray[np.float64]] = {}
    stock_labels: list[str] = []
    stock_colors: list[str] = []
    volume_total = pd.Series(0.0, index=base_df.index)
    for stock_index, stock in enumerate(multi.stocks):
        item = cast(MultiStockItem, stock)
        col_name = f"stock_{stock_index}"
        vol_name = f"vol_{stock_index}"
        item_change = cast(pd.Series, _numeric_series(item.df["percentage_change"])[valid_mask])
        item_volume = np.asarray(_numeric_series(item.df["money"], fill_value=0)[valid_mask])
        price_columns[col_name] = np.asarray(item_change, dtype=float)
        volume_columns[vol_name] = item_volume
        stock_labels.append(item.name)
        stock_colors.append(MPL_COLORS[stock_index % len(MPL_COLORS)])
        volume_total = cast(pd.Series, volume_total.add(_numeric_series(item.df["money"], fill_value=0), fill_value=0))

    first_col = next(iter(price_columns))
    base_close = price_columns[first_col]
    prices = pd.DataFrame(
        {
            "open": base_close,
            "high": base_close,
            "low": base_close,
            "close": base_close,
            "volume": np.asarray(volume_total[valid_mask]),
            **price_columns,
            **volume_columns,
        },
        index=pd.DatetimeIndex(np.asarray(datetimes[valid_mask]), name="date"),
    )
    prices = cast(pd.DataFrame, prices.dropna(subset=["open", "high", "low", "close"]))
    if prices.empty:
        return ErroText["notData"]
    prices = cast(pd.DataFrame, prices.sort_index())
    stock_volumes: list[NDArray[np.float64]] = []
    for stock_index in range(len(multi.stocks)):
        vol_name = f"vol_{stock_index}"
        stock_volumes.append(np.asarray(prices[vol_name], dtype=float))

    chart = Chart(
        prices,
        title="分时涨跌幅对比",
        figsize=(22.2, 16.7),
        bgcolor=BG_COLOR,
        raw_dates=False,
        color_scheme={
            "colorup": UP_COLOR,
            "colordn": DOWN_COLOR,
            "bgcolor": BG_COLOR,
            "text": FG_COLOR,
            "grid": GRID_COLOR,
        },
    )
    plot_items: list[object] = [HLine(0, color="#f1c40f", linestyle="--")]
    for stock_index, col_name in enumerate(price_columns):
        plot_items.append(Price(col_name, width=2.0, color=stock_colors[stock_index]))
    chart.plot(
        *plot_items,
        Pane("below", height_ratio=0.35),
        BarPlot(lambda frame: frame["volume"], color=AXIS_COLOR, alpha=0.18, width=0.82, label="成交额"),
    )
    chart.add_legends()

    fig = cast(Figure, chart.figure)
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for ax_index, ax in enumerate(axes):
        _style_axis(ax)
        ax.tick_params(axis="x", rotation=20)
        if ax_index == 0:
            percent_limit = max(abs(multi.y_axis_min), abs(multi.y_axis_max), 1.0)
            tick_step = 2 if percent_limit > 8 else 1
            tick_start = int(np.floor(-percent_limit))
            tick_end = int(np.ceil(percent_limit))
            tick_values = [value for value in range(tick_start, tick_end + 1) if value % tick_step == 0]
            ax.set_ylim(-percent_limit, percent_limit)
            ax.patch.set_alpha(0.0)
            ax.axhspan(0, percent_limit, facecolor=UP_COLOR, alpha=0.16, zorder=0.2)
            ax.axhspan(-percent_limit, 0, facecolor=DOWN_COLOR, alpha=0.16, zorder=0.2)
            ax.set_axisbelow(False)
            ax.set_yticks(tick_values)
            ax.set_yticklabels([f"{value}%" for value in tick_values])
            ax.set_ylabel("涨跌幅")
            ax.tick_params(labelbottom=False)
            ax.grid(True, axis="y", color=GRID_COLOR, alpha=0.42, linewidth=0.8)
            legend = ax.get_legend()
            if legend is not None:
                for text, label in zip(legend.get_texts(), stock_labels, strict=False):
                    text.set_text(label)
                    text.set_color(FG_COLOR)
                legend.get_frame().set_facecolor(BG_COLOR)
                legend.get_frame().set_edgecolor(GRID_COLOR)
        else:
            ax.set_ylabel("成交额")
            ax.yaxis.set_major_formatter(FuncFormatter(_format_money_axis))
            _apply_intraday_10min_ticks(ax, prices.index)
            ax.tick_params(labelbottom=True)
            for patch in list(ax.patches):
                patch.remove()
            sorted_indices = sorted(range(len(multi.stocks)), key=lambda i: multi.stocks[i].total_volume)
            num_bars = len(prices)
            x_positions = np.arange(num_bars)
            cumulative_bottom = np.zeros(num_bars)
            for vol_idx in sorted_indices:
                ax.bar(
                    x_positions,
                    stock_volumes[vol_idx],
                    bottom=cumulative_bottom,
                    color=stock_colors[vol_idx],
                    alpha=0.72,
                    width=0.82,
                    label=stock_labels[vol_idx],
                )
                cumulative_bottom = cumulative_bottom + stock_volumes[vol_idx]
            max_cumulative = float(cumulative_bottom.max()) if len(cumulative_bottom) > 0 else 1.0
            ax.set_ylim(0, max_cumulative * 1.08)
            volume_legend = ax.get_legend()
            if volume_legend is not None:
                volume_legend.get_frame().set_facecolor(BG_COLOR)
                volume_legend.get_frame().set_edgecolor(GRID_COLOR)
                for text in volume_legend.get_texts():
                    text.set_color(FG_COLOR)

    if axes:
        axes[0].set_title(
            "分时涨跌幅对比",
            color=FG_COLOR,
            fontsize=22,
            fontweight="bold",
            pad=24,
        )
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.988, top=0.855, bottom=0.10, hspace=0.04)
    return _fig_to_image(fig)


async def to_compare_fig(raw_datas: list[JsonDict]) -> DrawResult:
    return await _draw_in_thread(draw_compare_chart, raw_datas)


def draw_compare_chart(raw_datas: list[JsonDict]) -> DrawResult:
    _setup_mpl()
    data = build_compare_render_data(raw_datas)
    if isinstance(data, str):
        return data
    compare = cast(CompareRenderData, data)

    price_frames: list[pd.DataFrame] = []
    compare_columns: list[str] = []
    compare_labels: list[str] = []
    for index, item in enumerate(compare.items):
        column_name = f"compare_{index}"
        compare_columns.append(column_name)
        compare_labels.append(item.name)
        dates = _datetime_series(item.df["日期"])
        values = _numeric_series(item.df["归一化"]) * 100
        valid_mask = dates.notna() & values.notna()
        price_frames.append(
            pd.DataFrame(
                {column_name: np.asarray(values[valid_mask])},
                index=pd.DatetimeIndex(np.asarray(dates[valid_mask]), name="date"),
            )
        )
    merged = pd.concat(price_frames, axis=1).sort_index()
    merged = cast(pd.DataFrame, merged.dropna(how="all"))
    if merged.empty:
        return ErroText["notData"]
    first_series = cast(pd.Series, merged.iloc[:, 0].ffill().bfill())
    prices = merged.copy()
    prices["open"] = first_series
    prices["high"] = first_series
    prices["low"] = first_series
    prices["close"] = first_series
    prices["volume"] = 0.0

    chart = Chart(
        prices,
        title="对比图",
        figsize=(25.5, 16.5),
        bgcolor=BG_COLOR,
        raw_dates=False,
        color_scheme={
            "colorup": UP_COLOR,
            "colordn": DOWN_COLOR,
            "bgcolor": BG_COLOR,
            "text": FG_COLOR,
            "grid": GRID_COLOR,
        },
    )
    chart.plot(
        HLine(0, color="#f1c40f", linestyle="--"),
        *(
            Price(column_name, width=2.2, color=MPL_COLORS[index % len(MPL_COLORS)])
            for index, column_name in enumerate(compare_columns)
        ),
    )
    chart.add_legends()

    fig = cast(Figure, chart.figure)
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for ax_index, ax in enumerate(axes):
        _style_axis(ax)
        ax.yaxis.set_major_formatter(FuncFormatter(_format_percent_axis))
        ax.tick_params(axis="x", rotation=20)
        if ax_index == 0:
            compare_values = merged[compare_columns]
            data_min = float(cast(float, compare_values.min(skipna=True).min(skipna=True)))
            data_max = float(cast(float, compare_values.max(skipna=True).max(skipna=True)))
            span = max(data_max - data_min, 1.0)
            padding = span * 0.08
            y_min = min(data_min - padding, 0.0)
            y_max = max(data_max + padding, 0.0)
            ax.set_ylim(y_min, y_max)
            ax.patch.set_alpha(0.0)
            ax.axhspan(0, y_max, facecolor=UP_COLOR, alpha=0.16, zorder=0.2)
            ax.axhspan(y_min, 0, facecolor=DOWN_COLOR, alpha=0.16, zorder=0.2)
            ax.set_axisbelow(False)
            for line in ax.lines:
                line.set_zorder(3)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(BG_COLOR)
            legend.get_frame().set_edgecolor(AXIS_COLOR)
            for text, label in zip(legend.get_texts(), compare_labels, strict=False):
                text.set_text(label)
                text.set_color(FG_COLOR)
    if axes:
        axes[0].set_title("对比图", fontsize=24, fontweight="bold", color=FG_COLOR, pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.988, top=0.875, bottom=0.10)
    return _fig_to_image(fig)


async def to_fig(raw_data: JsonDict, market: str, sector: str | None = None, layer: int = 2) -> DrawResult:
    return await _draw_in_thread(draw_cloudmap_chart, raw_data, market, sector, layer)


def _color_for_diff(diff: float) -> tuple[float, float, float]:
    clipped = max(-10.0, min(10.0, diff))
    base = np.array([61, 61, 59], dtype=float)
    target = np.array([255, 0, 0], dtype=float) if clipped >= 0 else np.array([0, 210, 80], dtype=float)
    ratio = abs(clipped) / 10.0
    rgb = (base * (1 - ratio) + target * ratio) / 255.0
    return float(rgb[0]), float(rgb[1]), float(rgb[2])


def _split_rect(
    items: Sequence[JsonDict], x: float, y: float, w: float, h: float
) -> list[tuple[JsonDict, float, float, float, float]]:
    if not items:
        return []
    if len(items) == 1:
        return [(items[0], x, y, w, h)]

    total = sum(max(_as_float(item["value"]), 0.0) for item in items)
    if total <= 0:
        total = float(len(items))
        items = [dict(item, value=1.0) for item in items]

    half = total / 2.0
    acc = 0.0
    split_index = 0
    for index, item in enumerate(items):
        value = max(_as_float(item["value"]), 0.0)
        if index > 0 and acc + value > half:
            break
        acc += value
        split_index = index + 1
    split_index = max(1, min(split_index, len(items) - 1))
    first = items[:split_index]
    second = items[split_index:]
    first_sum = sum(max(_as_float(item["value"]), 0.0) for item in first)
    ratio = first_sum / total if total else 0.5
    if w >= h:
        first_w = w * ratio
        return _split_rect(first, x, y, first_w, h) + _split_rect(second, x + first_w, y, w - first_w, h)
    first_h = h * ratio
    return _split_rect(first, x, y, w, first_h) + _split_rect(second, x, y + first_h, w, h - first_h)


def draw_cloudmap_chart(raw_data: JsonDict, market: str, sector: str | None = None, layer: int = 2) -> DrawResult:
    _setup_mpl()
    data = build_cloudmap_render_data(raw_data, market, sector, layer)
    if isinstance(data, str):
        return data
    cloudmap = cast(CloudmapRenderData, data)

    fig = plt.figure(figsize=(18, 18))
    ax = cast(Axes, fig.add_subplot(1, 1, 1))
    fig.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    items = cast(list[JsonDict], cloudmap.df.to_dict("records"))
    for item, x, y, w, h in _split_rect(items, 0.0, 0.0, 1.0, 1.0):
        pad = 0.0025
        rx = x + pad
        ry = y + pad
        rw = max(w - pad * 2, 0)
        rh = max(h - pad * 2, 0)
        diff_val = _as_float(item["diff_val"])
        ax.add_patch(
            Rectangle((rx, ry), rw, rh, facecolor=_color_for_diff(diff_val), edgecolor=BG_COLOR, linewidth=1.0)
        )
        area = rw * rh
        if area <= 0.002:
            continue
        fontsize = max(7, min(24, int(7 + area * 120)))
        custom_info = f"+{diff_val}%" if diff_val >= 0 else f"{diff_val}%"
        label = f"{item['name']}\n{custom_info}"
        if layer != 1 and area > 0.012:
            label = f"{item['category']}\n{label}"
        ax.text(
            rx + rw / 2,
            ry + rh / 2,
            label,
            ha="center",
            va="center",
            color="white",
            fontsize=fontsize,
            fontweight="bold",
            clip_on=True,
        )

    ax.set_title(cloudmap.title, color=FG_COLOR, fontsize=28, fontweight="bold", pad=18)
    fig.text(0.01, 0.01, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    return _fig_to_image(fig, dpi=220)


async def render_html(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    """兼容旧入口名：mpl 版本不生成 HTML，实际缓存 PNG 图片。"""
    return await render_image_file(market, sector, start_time, end_time)


async def render_image_file(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    logger.info(f"[SayuStock] market: {market} sector: {sector}")
    if sector == "single-stock" and not market:
        return ErroText["notMarket"]
    if not market:
        market = "沪深A"

    logger.info("[SayuStock] 开始获取数据...")
    data_result = await CLOUDMAP_DATA_SERVICE.fetch(market, sector, start_time, end_time)
    raw_data = data_result.raw_data
    raw_datas = data_result.raw_datas
    sector = data_result.sector

    if isinstance(raw_data, str):
        return raw_data

    file = get_file(market, "png", sector, data_result.special_cache_key)
    if file.exists():
        minutes = STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(f"[SayuStock] png文件在{minutes}分钟内，直接返回文件数据。")
            return file

    if sector == "single-stock":
        if raw_datas:
            fig = await to_multi_fig(raw_datas)
            _ai_return_single_stock(raw_datas, is_multi=True)
        else:
            fig = await to_single_fig(raw_data)
            _ai_return_single_stock(raw_data)
    elif sector == "compare-stock":
        fig = await to_compare_fig(raw_datas)
        _ai_return_compare_stock(raw_datas)
    elif sector and sector.startswith("single-stock-kline"):
        fig = await to_single_fig_kline(raw_data)
        _ai_return_kline(raw_data, sector)
    else:
        fig = await to_fig(raw_data, market, sector, 2 if market == "大盘云图" else 1)
        _ai_return_cloudmap(raw_data, market, sector)

    if isinstance(fig, str):
        return fig

    await asyncio.to_thread(fig.save, file, format="PNG")
    return file


def _ai_return_single_stock(raw_data: JsonDict | list[JsonDict], is_multi: bool = False) -> None:
    """从个股数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    if is_multi:
        parts: list[str] = []
        raw_data_list = raw_data if isinstance(raw_data, list) else []
        for rd in raw_data_list:
            if isinstance(rd, str):
                continue
            data = _as_dict(rd["data"])
            parts.append(
                f"【{_dict_value(data, 'f58', 'N/A')}】最新价: {_dict_value(data, 'f43', 'N/A')}  "
                f"涨跌幅: {_dict_value(data, 'f170', 'N/A')}%  "
                f"开盘: {_dict_value(data, 'f60', 'N/A')}  "
                f"最高: {_dict_value(data, 'f44', 'N/A')}  "
                f"最低: {_dict_value(data, 'f45', 'N/A')}  "
                f"换手率: {_dict_value(data, 'f168', 'N/A')}%  "
                f"成交额: {_dict_value(data, 'f48', 'N/A')}"
            )
        if parts:
            ai_return("【多股分时行情对比】\n" + "\n".join(parts))
        return

    data = _as_dict(cast(JsonDict, raw_data)["data"])
    result = (
        f"【{_dict_value(data, 'f58', 'N/A')} 分时行情】\n"
        f"最新价: {_dict_value(data, 'f43', 'N/A')}  涨跌幅: {_dict_value(data, 'f170', 'N/A')}%\n"
        f"开盘价: {_dict_value(data, 'f60', 'N/A')}  "
        f"最高价: {_dict_value(data, 'f44', 'N/A')}  最低价: {_dict_value(data, 'f45', 'N/A')}\n"
        f"换手率: {_dict_value(data, 'f168', 'N/A')}%  成交额: {_dict_value(data, 'f48', 'N/A')}"
    )
    ai_return(result)


def _ai_return_kline(raw_data: JsonDict, sector: str) -> None:
    """从K线数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    data = _as_dict(raw_data["data"])
    klines = _as_str_list(data["klines"])
    if not klines:
        return

    period_map = {
        "5": "5分钟",
        "15": "15分钟",
        "30": "30分钟",
        "60": "60分钟",
        "100": "K线",
        "101": "日K",
        "102": "周K",
        "103": "月K",
        "104": "季K",
        "105": "半年K",
        "106": "年K",
    }
    code = sector.replace("single-stock-kline-", "")
    period_name = period_map[code] if code in period_map else "K线"

    result = f"【{_dict_value(data, 'name', 'N/A')} {period_name}数据】\n"
    result += "日期        开盘    收盘    最高    最低    涨跌幅\n"
    for line in klines[-10:]:
        values = line.split(",")
        if len(values) >= 11:
            result += f"{values[0]} {values[1]:>8} {values[2]:>8} {values[3]:>8} {values[4]:>8} {values[8]:>8}%\n"
    ai_return(result)


def _ai_return_compare_stock(raw_datas: list[JsonDict]) -> None:
    """从对比个股数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    parts: list[str] = []
    for rd in raw_datas:
        data = _as_dict(rd["data"])
        name = data["name"] if "name" in data else _dict_value(data, "f58", "N/A")
        klines = _as_str_list(data["klines"])
        if klines:
            last = klines[-1].split(",")
            if len(last) >= 11:
                parts.append(f"{name}: 收盘 {last[2]}  涨跌幅 {last[8]}%")
    if parts:
        ai_return("【个股对比数据】\n" + "\n".join(parts))


def _ai_return_cloudmap(raw_data: JsonDict, market: str, sector: str | None = None) -> None:
    """从大盘云图/板块云图/概念云图数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    diff = _as_dict_list(_as_dict(raw_data["data"])["diff"])
    if not diff:
        return

    valid_items = [item for item in diff if item["f3"] != "-" and item["f14"]]
    valid_items.sort(key=lambda x: _as_float(x["f3"]), reverse=True)

    title = market if market else "板块云图"
    if sector and market not in ("大盘云图",):
        title = f"{market} - {sector}"

    result = f"【{title}】\n"
    result += "领涨:\n"
    for item in valid_items[:5]:
        result += f"  {item['f14']}({item['f100']}): {item['f3']}%\n"

    result += "领跌:\n"
    for item in valid_items[-5:]:
        result += f"  {item['f14']}({item['f100']}): {item['f3']}%\n"

    up_count = sum(1 for item in valid_items if _as_float(item["f3"]) > 0)
    down_count = sum(1 for item in valid_items if _as_float(item["f3"]) < 0)
    flat_count = len(valid_items) - up_count - down_count
    result += f"统计: 上涨 {up_count} 家, 下跌 {down_count} 家, 平盘 {flat_count} 家"
    ai_return(result)


async def render_image(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> BotSendContent:
    image_path = await render_image_file(market, sector, start_time, end_time)
    if isinstance(image_path, str):
        return image_path

    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    return await convert_img(image_bytes)
