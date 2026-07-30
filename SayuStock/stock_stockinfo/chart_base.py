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
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.offsetbox import HPacker, TextArea, AnnotationBbox  # noqa: E402

from gsuid_core.utils.fonts.fonts import FONT_ORIGIN_PATH

# 统一经兼容层导入：同时支持旧版（含 Price）与新版（Price 已移除）mplchart
from ..utils.mplchart_compat import (  # noqa: E402
    SMA,
    Pane,
    Chart,
    HLine,
    Price,
    Volume,
    BarPlot,
    LinePlot,
    Indicator,
    Candlesticks,
)

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
    "_apply_detail_legend",
    "_dodge_end_label_offsets",
    "_dodge_label_point_offsets",
    "_draw_dodged_text_labels",
    "_draw_end_point_labels",
    "_draw_in_thread",
    "_fig_to_image",
    "_format_detail_legend_label",
    "_format_metric_value",
    "_format_money_axis",
    "_format_percent_axis",
    "_format_precise_percent_axis",
    "_frame_column",
    "_intraday_positions",
    "_mpl_bar_colors",
    "_numeric_series",
    "_pct_change",
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


def _format_metric_value(value: float) -> str:
    """价格 / 估值等数值的统一展示格式。"""
    if not np.isfinite(value):
        return "--"
    abs_value = abs(value)
    if abs_value >= 1000:
        return f"{value:.1f}"
    if abs_value >= 1:
        return f"{value:.2f}"
    if abs_value >= 0.01:
        return f"{value:.3f}"
    return f"{value:.4f}"


def _pct_change(start: float, end: float) -> float:
    """完整涨跌幅（%）。起点为 0 或非有限值时返回 0。"""
    if not np.isfinite(start) or not np.isfinite(end) or start == 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def _format_detail_legend_label(
    name: str,
    start: float,
    end: float,
    change_pct: float | None = None,
    *,
    start_tag: str = "起",
    end_tag: str = "末",
) -> str:
    """图例用双行标签：名称 + 起/末/完整涨跌幅。"""
    pct = _pct_change(start, end) if change_pct is None else change_pct
    return f"{name}\n{start_tag} {_format_metric_value(start)}  {end_tag} {_format_metric_value(end)}  {pct:+.2f}%"


def _apply_detail_legend(
    ax: Axes,
    labels: Sequence[str],
    *,
    text_colors: Sequence[str] | None = None,
    fontsize: float = 9.5,
    loc: str = "upper left",
) -> None:
    """把已有图例文案替换成带明细的标签，并固定到左上角。"""
    legend = ax.get_legend()
    if legend is None:
        return
    # 不同 matplotlib 版本 API 略有差异
    if hasattr(legend, "set_loc"):
        legend.set_loc(loc)
    else:
        legend._loc = loc  # type: ignore[attr-defined]
    frame = legend.get_frame()
    frame.set_facecolor(BG_COLOR)
    frame.set_edgecolor(AXIS_COLOR)
    frame.set_alpha(0.88)
    for index, text in enumerate(legend.get_texts()):
        if index >= len(labels):
            break
        text.set_text(labels[index])
        text.set_fontsize(fontsize)
        if text_colors is not None and index < len(text_colors):
            text.set_color(text_colors[index])
        else:
            text.set_color(FG_COLOR)


def _ensure_axes_renderer(ax: Axes):
    fig = ax.figure
    fig.canvas.draw()
    return fig.canvas.get_renderer()


def _dodge_end_label_offsets(
    y_values: Sequence[float],
    ax: Axes,
    *,
    min_sep_pts: float = 20.0,
    x_base_pts: float = 16.0,
    x_stagger_pts: float = 6.0,
) -> list[tuple[float, float]]:
    """为曲线末端标签计算 (x_offset, y_offset)（单位：points），使相近标签纵向避让。

    返回值可直接用于 ``AnnotationBbox(..., xybox=offset, boxcoords="offset points")``。
    当标签相对数据点纵向挪开时，配合 ``arrowprops`` 即可画出点→标签的引出线。
    """
    n = len(y_values)
    if n == 0:
        return []
    if n == 1:
        return [(x_base_pts, 0.0)]

    renderer = _ensure_axes_renderer(ax)
    dpi = float(ax.figure.dpi)
    px_per_pt = dpi / 72.0
    min_sep_px = min_sep_pts * px_per_pt

    display_ys = [float(ax.transData.transform((0.0, float(y)))[1]) for y in y_values]
    order = sorted(range(n), key=lambda i: display_ys[i])
    sorted_y = [display_ys[i] for i in order]

    # 自下而上按最小间距挤开
    packed = sorted_y[:]
    for i in range(1, n):
        packed[i] = max(packed[i], packed[i - 1] + min_sep_px)

    # 碰撞簇整体回中，尽量贴近原始 y
    cluster_start = 0
    while cluster_start < n:
        cluster_end = cluster_start
        while cluster_end + 1 < n and abs((packed[cluster_end + 1] - packed[cluster_end]) - min_sep_px) < 1e-6:
            cluster_end += 1
        if cluster_end > cluster_start:
            size = cluster_end - cluster_start + 1
            orig_mean = sum(sorted_y[cluster_start : cluster_end + 1]) / size
            pack_mean = sum(packed[cluster_start : cluster_end + 1]) / size
            shift = orig_mean - pack_mean
            for k in range(cluster_start, cluster_end + 1):
                packed[k] += shift
        cluster_start = cluster_end + 1

    # 回中后可能重新重叠，再做一次前向/后向收紧
    for i in range(1, n):
        if packed[i] < packed[i - 1] + min_sep_px:
            packed[i] = packed[i - 1] + min_sep_px
    for i in range(n - 2, -1, -1):
        if packed[i] > packed[i + 1] - min_sep_px:
            packed[i] = packed[i + 1] - min_sep_px

    # 限制在坐标轴可视范围内
    bbox = ax.get_window_extent(renderer=renderer)
    margin = min_sep_px * 0.35
    y_lo = float(bbox.y0) + margin
    y_hi = float(bbox.y1) - margin
    if packed[0] < y_lo:
        shift = y_lo - packed[0]
        packed = [p + shift for p in packed]
    if packed[-1] > y_hi:
        shift = packed[-1] - y_hi
        packed = [p - shift for p in packed]
    for i in range(1, n):
        if packed[i] < packed[i - 1] + min_sep_px:
            packed[i] = packed[i - 1] + min_sep_px
    if packed[-1] > y_hi:
        for i in range(n - 2, -1, -1):
            if packed[i + 1] - packed[i] < min_sep_px:
                packed[i] = packed[i + 1] - min_sep_px

    # 映射回 points 偏移；纵向挪得越远，横向略加错开，引出线更清晰
    results: list[tuple[float, float]] = [(x_base_pts, 0.0) for _ in range(n)]
    for rank, orig_i in enumerate(order):
        y_offset_pts = (packed[rank] - display_ys[orig_i]) / px_per_pt
        stagger = (rank - (n - 1) / 2.0) * (x_stagger_pts * 0.15)
        fan = min(abs(y_offset_pts) * 0.12, 18.0)
        x_offset_pts = x_base_pts + fan + stagger
        results[orig_i] = (x_offset_pts, y_offset_pts)
    return results


def _dodge_label_point_offsets(
    xy_data: Sequence[tuple[float, float]],
    preferred_offsets: Sequence[tuple[float, float]],
    ax: Axes,
    *,
    min_sep_pts: float = 32.0,
    max_iter: int = 60,
) -> list[tuple[float, float]]:
    """对任意位置的点标注做 2D 互斥避让，返回调整后的 offset points。

    用于极值点、分红事件、副图峰值等「点 + 引出线」标签。
    """
    n = len(xy_data)
    if n == 0:
        return []
    if n != len(preferred_offsets):
        raise ValueError("xy_data 与 preferred_offsets 长度必须一致")
    if n == 1:
        return [preferred_offsets[0]]

    _ensure_axes_renderer(ax)
    dpi = float(ax.figure.dpi)
    px_per_pt = dpi / 72.0
    min_sep_px = min_sep_pts * px_per_pt

    # 锚点 display 坐标 + 首选标签 display 位置
    anchor_disp: list[tuple[float, float]] = []
    label_disp: list[list[float]] = []
    for (x, y), (ox, oy) in zip(xy_data, preferred_offsets, strict=True):
        ax_x, ax_y = ax.transData.transform((float(x), float(y)))
        anchor_disp.append((float(ax_x), float(ax_y)))
        label_disp.append([float(ax_x) + float(ox) * px_per_pt, float(ax_y) + float(oy) * px_per_pt])

    for _ in range(max_iter):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx = label_disp[j][0] - label_disp[i][0]
                dy = label_disp[j][1] - label_disp[i][1]
                dist = float(np.hypot(dx, dy))
                if dist >= min_sep_px:
                    continue
                if dist < 1e-6:
                    # 完全重合时优先纵向推开
                    dx, dy, dist = 0.0, 1.0, 1.0
                push = (min_sep_px - dist) / 2.0
                ux, uy = dx / dist, dy / dist
                # 更偏向纵向分离，阅读更清晰
                if abs(uy) < 0.25:
                    uy = 1.0 if dy >= 0 else -1.0
                    ux = 0.15 if dx >= 0 else -0.15
                    norm = float(np.hypot(ux, uy)) or 1.0
                    ux, uy = ux / norm, uy / norm
                label_disp[i][0] -= ux * push
                label_disp[i][1] -= uy * push
                label_disp[j][0] += ux * push
                label_disp[j][1] += uy * push
                moved = True
        if not moved:
            break

    results: list[tuple[float, float]] = []
    for i in range(n):
        ox = (label_disp[i][0] - anchor_disp[i][0]) / px_per_pt
        oy = (label_disp[i][1] - anchor_disp[i][1]) / px_per_pt
        results.append((ox, oy))
    return results


def _draw_end_point_labels(
    ax: Axes,
    entries: Sequence[tuple[float, float, str, str, str]],
    *,
    min_sep_pts: float = 22.0,
    x_base_pts: float = 18.0,
    x_stagger_pts: float = 8.0,
    value_color_fn: Callable[[str], str] | None = None,
) -> None:
    """绘制曲线末端「点 + 引出线 + 名称/数值」标签，并做纵向避让。

    entries 每项为 ``(x, y, name, value_text, color)``，value_text 含前导空格，如 `` +1.23%``。
    """
    if not entries:
        return
    offsets = _dodge_end_label_offsets(
        [item[1] for item in entries],
        ax,
        min_sep_pts=min_sep_pts,
        x_base_pts=x_base_pts,
        x_stagger_pts=x_stagger_pts,
    )
    for (x, y, name, value_text, color), (x_off, y_off) in zip(entries, offsets, strict=True):
        ax.scatter([x], [y], color=color, edgecolor=BG_COLOR, s=40, zorder=5)
        value_color = value_color_fn(value_text) if value_color_fn is not None else FG_COLOR
        name_area = TextArea(
            name,
            textprops={"color": color, "fontsize": 11, "fontweight": "bold"},
        )
        value_area = TextArea(
            value_text,
            textprops={"color": value_color, "fontsize": 11, "fontweight": "bold"},
        )
        label_box = HPacker(children=[name_area, value_area], align="center", pad=0, sep=1)
        artist = AnnotationBbox(
            label_box,
            (x, y),
            xybox=(x_off, y_off),
            xycoords="data",
            boxcoords="offset points",
            box_alignment=(0, 0.5),
            frameon=True,
            pad=0.25,
            bboxprops={"facecolor": BG_COLOR, "edgecolor": color, "alpha": 0.78},
            arrowprops={
                "arrowstyle": "-",
                "color": color,
                "alpha": 0.85,
                "linewidth": 1.0,
                "connectionstyle": "arc3,rad=0",
                "shrinkA": 0,
                "shrinkB": 1,
            },
            zorder=6,
        )
        ax.add_artist(artist)


def _draw_dodged_text_labels(
    ax: Axes,
    entries: Sequence[tuple[float, float, str, str, tuple[float, float]]],
    *,
    min_sep_pts: float = 34.0,
    fontsize: float = 10.0,
    ha: str = "center",
    va_from_offset: bool = True,
    scatter: bool = True,
    scatter_size: float = 42.0,
) -> None:
    """绘制「点 + 引出线 + 文本框」标签，并对首选偏移做 2D 避让。

    entries 每项为 ``(x, y, text, color, preferred_offset_pts)``。
    """
    if not entries:
        return
    xy_data = [(float(item[0]), float(item[1])) for item in entries]
    preferred = [item[4] for item in entries]
    offsets = _dodge_label_point_offsets(xy_data, preferred, ax, min_sep_pts=min_sep_pts)
    for (x, y, text, color, _), (ox, oy) in zip(entries, offsets, strict=True):
        if scatter:
            ax.scatter([x], [y], color=color, edgecolor=BG_COLOR, s=scatter_size, zorder=5)
        if va_from_offset:
            va = "bottom" if oy >= 0 else "top"
        else:
            va = "center"
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(ox, oy),
            textcoords="offset points",
            color=color,
            fontsize=fontsize,
            fontweight="bold",
            ha=ha,
            va=va,
            bbox={"facecolor": BG_COLOR, "edgecolor": color, "alpha": 0.75, "pad": 2.5},
            arrowprops={
                "arrowstyle": "-",
                "color": color,
                "alpha": 0.8,
                "linewidth": 0.9,
                "connectionstyle": "arc3,rad=0",
                "shrinkA": 0,
                "shrinkB": 1,
            },
            zorder=6,
        )


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
