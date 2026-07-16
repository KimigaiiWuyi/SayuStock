"""matplotlib 绘图的公共底座：绘图栈、样式常量、类型别名、坐标轴与数据强转 helper。

``render_mpl.py`` 曾是个 1400+ 行的巨型文件，什么都往里塞。现在按图表种类拆开：

- ``chart_base``（本文件）：颜色/字体/坐标轴/工具函数
- ``chart_kline``：日K/周K/月K
- ``chart_intraday``：分时、多股分时
- ``chart_compare``：个股对比
- ``chart_cloudmap``：云图
- ``render_mpl``：对外入口（render_image / ai_return），并 re-export 上述绘图函数

本模块同时是各 chart_* 的**绘图栈入口**：``matplotlib.use("Agg")`` 在这里执行，
各 chart_* 一律 ``from .chart_base import ...`` 取 plt / Chart / 各 primitive，
这样后端设定顺序有保证，也不用每个文件重复一遍 ``# noqa: E402`` 的 import 块。

指标数学一律走 ``utils/indicators.py``，文字输出走 ``utils/render_text.py``。
"""

import asyncio
from io import BytesIO
from typing import TypeVar, ParamSpec
from datetime import datetime
from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd
import matplotlib
from PIL import Image
from numpy.typing import NDArray

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from mplchart.chart import Chart  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from mplchart.indicators import SMA, Indicator  # noqa: E402
from mplchart.primitives import Pane, HLine, Price, Volume, BarPlot, LinePlot, Candlesticks  # noqa: E402
from matplotlib.offsetbox import HPacker, TextArea, AnnotationBbox  # noqa: E402

from gsuid_core.utils.fonts.fonts import FONT_ORIGIN_PATH

__all__ = [
    # 绘图栈（各 chart_* 从这里取，保证 Agg 后端先设好）
    "AnnotationBbox",
    "Axes",
    "BarPlot",
    "Candlesticks",
    "Chart",
    "Figure",
    "FuncFormatter",
    "Indicator",
    "HLine",
    "HPacker",
    "Image",
    "LinePlot",
    "NDArray",
    "Pane",
    "Price",
    "Rectangle",
    "SMA",
    "Sequence",
    "TextArea",
    "TypeVar",
    "Volume",
    "np",
    "pd",
    "plt",
    # 类型别名
    "BotSendContent",
    "DrawResult",
    "JsonDict",
    # 样式
    "AXIS_COLOR",
    "BG_COLOR",
    "DOWN_COLOR",
    "FG_COLOR",
    "FLAT_COLOR",
    "GRID_COLOR",
    "MPL_COLORS",
    "UP_COLOR",
    # helper
    "_add_cross_midnight_marker",
    "_apply_intraday_10min_ticks",
    "_apply_intraday_axis",
    "_apply_intraday_kline_ticks",
    "_apply_month_ticks",
    "_as_dict",
    "_as_dict_list",
    "_as_float",
    "_as_str_list",
    "_axes_top_to_bottom",
    "_datetime_series",
    "_date_index_positions",
    "_dict_value",
    "_draw_in_thread",
    "_fig_to_image",
    "_format_money_axis",
    "_format_percent_axis",
    "_format_precise_percent_axis",
    "_frame_column",
    "_intraday_positions",
    "_mpl_bar_colors",
    "_numeric_series",
    "_series_from_value",
    "_setup_mpl",
    "_style_axis",
    "_timestamp_from_value",
]


BotSendContent = str | bytes
DrawResult = str | Image.Image
JsonDict = dict[str, object]
P = ParamSpec("P")
R = TypeVar("R", bound=DrawResult)

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
    font_candidates = FONT_CANDIDATES
    if FONT_ORIGIN_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_ORIGIN_PATH))
        core_font_name = font_manager.FontProperties(fname=str(FONT_ORIGIN_PATH)).get_name()
        font_candidates = [core_font_name, *FONT_CANDIDATES]
    plt.rcParams["font.sans-serif"] = font_candidates
    plt.rcParams["font.family"] = "sans-serif"
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
        return {str(key): item for key, item in value.items()}
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
    series = pd.to_numeric(_series_from_value(value), errors="coerce")
    assert isinstance(series, pd.Series), "to_numeric(Series) 恒返回 Series"
    if fill_value is None:
        return series
    return series.fillna(fill_value)


def _datetime_series(value: object) -> pd.Series:
    return pd.to_datetime(_series_from_value(value), errors="coerce")


def _frame_column(df: pd.DataFrame, key: str) -> pd.Series:
    column = df[key]
    assert isinstance(column, pd.Series), f"列 {key} 存在重复标签"
    return column


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


def _format_precise_percent_axis(value: float, _pos: object = None) -> str:
    # 换手率数据本身已是百分比数值（如 5.23 表示 5.23%），无需再乘以 100
    return f"{value:.2f}%"


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


def _format_intraday_tick_label(timestamp: pd.Timestamp, base_day: pd.Timestamp | None) -> str:
    if base_day is not None and timestamp.normalize() > base_day:
        return f"次日\n{timestamp.strftime('%H:%M')}"
    return timestamp.strftime("%H:%M")


def _add_cross_midnight_marker(ax: Axes, index: pd.Index) -> None:
    dates = _date_index_positions(index)
    if dates.empty:
        return
    valid_dates = [timestamp for timestamp in dates if not pd.isna(timestamp)]
    if not valid_dates:
        return
    base_day = valid_dates[0].normalize()
    for position, timestamp in enumerate(dates):
        if pd.isna(timestamp):
            continue
        if timestamp.normalize() > base_day:
            ax.axvline(position, color="#f1c40f", linestyle=":", alpha=0.55, linewidth=1.1, zorder=2)
            ax.text(
                position,
                0.98,
                "次日",
                transform=ax.get_xaxis_transform(),
                color="#f1c40f",
                fontsize=10,
                ha="left",
                va="top",
                alpha=0.82,
                bbox={"facecolor": BG_COLOR, "edgecolor": "none", "alpha": 0.55, "pad": 1},
            )
            return


def _intraday_tick_step_minutes(dates: pd.DatetimeIndex) -> int:
    valid_dates = [timestamp for timestamp in dates if not pd.isna(timestamp)]
    if len(valid_dates) < 2:
        return 10
    trading_minutes = (valid_dates[-1] - valid_dates[0]).total_seconds() / 60
    return 30 if trading_minutes >= 12 * 60 else 10


def _apply_intraday_10min_ticks(ax: Axes, index: pd.Index) -> None:
    dates = _date_index_positions(index)
    if dates.empty:
        return
    valid_dates = [timestamp for timestamp in dates if not pd.isna(timestamp)]
    base_day = valid_dates[0].normalize() if valid_dates else None
    tick_step_minutes = _intraday_tick_step_minutes(dates)
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    for position, timestamp in enumerate(dates):
        if pd.isna(timestamp):
            continue
        if timestamp.minute % tick_step_minutes == 0:
            tick_positions.append(position)
            tick_labels.append(_format_intraday_tick_label(timestamp, base_day))
    if not tick_positions:
        tick_positions = [int(position) for position in np.linspace(0, len(dates) - 1, min(8, len(dates)), dtype=int)]
        tick_labels = []
        for position in tick_positions:
            timestamp = _timestamp_from_value(dates[position])
            tick_labels.append(_format_intraday_tick_label(timestamp, base_day) if timestamp is not None else "")
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)
    _add_cross_midnight_marker(ax, index)


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
    axes = [ax for ax in fig.axes if isinstance(ax, Axes) and ax.get_label() not in {"root", "twinx"}]
    return sorted(axes, key=lambda item: item.get_position().y0, reverse=True)
