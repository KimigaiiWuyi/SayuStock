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
from typing import TypeVar, Protocol, ParamSpec, cast, runtime_checkable
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
from matplotlib.backend_bases import RendererBase  # noqa: E402

from gsuid_core.utils.fonts.fonts import FONT_ORIGIN_PATH

# 统一经兼容层导入：同时支持旧版（含 Price）与新版（Price 已移除）mplchart
from ..utils.mplchart_compat import (  # noqa: E402
    SMA,
    Pane,
    Bands,
    Chart,
    HLine,
    Price,
    Volume,
    BarPlot,
    LinePlot,
    Indicator,
    Candlesticks,
    chart_series_xy,
)

__all__ = [
    # 绘图栈（各 chart_* 从这里取，保证 Agg 后端先设好）
    "AnnotationBbox",
    "Axes",
    "Bands",
    "BarPlot",
    "Candlesticks",
    "Chart",
    "chart_series_xy",
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
    "FONT_W_BOLD",
    "FONT_W_DEMIBOLD",
    "FONT_W_HEAVY",
    "FONT_W_LIGHT",
    "FONT_W_MED",
    "FONT_W_REG",
    "FONT_W_SEMIBOLD",
    "FONT_W_THIN",
    "GRID_COLOR",
    "MPL_COLORS",
    "UP_COLOR",
    # helper
    "_add_cross_midnight_marker",
    "_apply_intraday_10min_ticks",
    "_apply_intraday_axis",
    "_apply_intraday_day_separators",
    "_apply_intraday_day_ticks",
    "_apply_intraday_kline_ticks",
    "_apply_month_ticks",
    "_hide_root_x_tick_labels",
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
    "_estimate_text_box_pts",
    "_leader_arrowprops",
    "_draw_in_thread",
    "_fig_to_image",
    "_paint_chart_background",
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
DAY_SEP_COLOR = "#7f8c9a"
FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
# 对齐系统 MiSans 静态字族的 wght 档（VF 在 matplotlib 里只有 Regular 一档）
FONT_W_THIN = 150
FONT_W_LIGHT = 250
FONT_W_REG = 330
FONT_W_MED = 380
FONT_W_DEMIBOLD = 450
FONT_W_SEMIBOLD = 520
FONT_W_BOLD = 630
FONT_W_HEAVY = 700
MPL_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#17becf", "#e377c2"]


def _setup_mpl() -> None:
    """注册字体：优先静态 MiSans（Thin…Heavy），勿把 VF 放第一否则字重失效。"""
    if FONT_ORIGIN_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_ORIGIN_PATH))
    families: list[str] = []
    if any(entry.name == "MiSans" for entry in font_manager.fontManager.ttflist):
        families.append("MiSans")
    vf_name = None
    if FONT_ORIGIN_PATH.exists():
        vf_name = font_manager.FontProperties(fname=str(FONT_ORIGIN_PATH)).get_name()
        if vf_name and vf_name not in families:
            families.append(vf_name)
    families.extend(FONT_CANDIDATES)
    plt.rcParams["font.sans-serif"] = families
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.weight"] = FONT_W_REG
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = BG_COLOR
    plt.rcParams["axes.facecolor"] = BG_COLOR
    plt.rcParams["savefig.facecolor"] = BG_COLOR


def _paint_chart_background(fig: Figure) -> None:
    """把 mplchart 图的可见底色刷成暗色。

    新版 Canvas 里数据 pane 的 patch 是透明的，真正露出来的是 ``label=root``
    那根轴。只 ``fig.set_facecolor`` / 只刷 pane 都会仍看到默认浅色 root。
    """
    fig.set_facecolor(BG_COLOR)
    fig.patch.set_alpha(1.0)
    for ax in fig.axes:
        if ax.get_label() == "root":
            ax.set_facecolor(BG_COLOR)
            ax.patch.set_visible(True)
            ax.patch.set_alpha(1.0)


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
    if isinstance(value, (list, tuple, pd.Index)):
        return pd.Series(list(value))
    return pd.Series([value])


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
    ax.xaxis.label.set_fontweight(FONT_W_DEMIBOLD)
    ax.yaxis.label.set_color(AXIS_COLOR)
    ax.yaxis.label.set_fontweight(FONT_W_DEMIBOLD)
    ax.title.set_color(FG_COLOR)
    ax.title.set_fontweight(FONT_W_BOLD)
    for lab in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        lab.set_fontweight(FONT_W_REG)
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


@runtime_checkable
class _LegendSetLoc(Protocol):
    def set_loc(self, loc: str | int) -> None: ...


@runtime_checkable
class _LegendPrivLoc(Protocol):
    _loc: str | int


def _set_legend_loc(legend: object, loc: str) -> None:
    """跨 matplotlib 版本设置图例位置（公开 set_loc 或私有 _loc）。"""
    if isinstance(legend, _LegendSetLoc):
        legend.set_loc(loc)
        return
    if isinstance(legend, _LegendPrivLoc):
        legend._loc = loc


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
    _set_legend_loc(legend, loc)
    frame = legend.get_frame()
    frame.set_facecolor(BG_COLOR)
    frame.set_edgecolor(AXIS_COLOR)
    frame.set_alpha(0.88)
    for index, text in enumerate(legend.get_texts()):
        if index >= len(labels):
            break
        text.set_text(labels[index])
        text.set_fontsize(fontsize)
        text.set_fontweight(FONT_W_MED)
        if text_colors is not None and index < len(text_colors):
            text.set_color(text_colors[index])
        else:
            text.set_color(FG_COLOR)


def _ensure_axes_renderer(ax: Axes) -> RendererBase:
    """拿到可用于 ``get_window_extent(renderer=...)`` 的 renderer。

    stubs 里 ``Figure.canvas`` 可能是 ``None``，且 ``get_renderer`` 只在 Agg canvas 上；
    运行时 Agg 后端一定有这两个属性。
    """
    fig = ax.figure
    if fig is None:
        raise RuntimeError("Axes 未绑定 Figure，无法获取 renderer")
    canvas = fig.canvas
    if canvas is None:
        raise RuntimeError("Figure 未绑定 canvas，无法获取 renderer")
    canvas.draw()
    get_renderer = getattr(canvas, "get_renderer", None)
    if not callable(get_renderer):
        raise RuntimeError("当前 canvas 不支持 get_renderer（需要 Agg 后端）")
    return cast(RendererBase, get_renderer())


def _estimate_text_box_pts(text: str, fontsize: float, *, pad_pts: float = 4.0) -> tuple[float, float]:
    """按文本行数/最长行估算标签框 (宽, 高)，单位 points（含边距）。"""
    lines = [line for line in str(text).split("\n") if line is not None]
    if not lines:
        lines = [""]
    # 中文约 0.95em，数字/英文约 0.55em；取偏大估计避免框比真实窄
    max_w = 0.0
    for line in lines:
        w = 0.0
        for ch in line:
            w += fontsize * (0.92 if ord(ch) > 127 else 0.58)
        max_w = max(max_w, w)
    width = max(max_w + pad_pts * 2.0, fontsize * 2.0)
    height = max(len(lines) * fontsize * 1.38 + pad_pts * 2.0, fontsize * 1.6)
    return width, height


def _leader_arrowprops(color: str, *, linewidth: float = 1.0, alpha: float = 0.88) -> dict[str, object]:
    """锚点圆点 → 标签的虚线引出。"""
    return {
        "arrowstyle": "-",
        "color": color,
        "alpha": alpha,
        "linewidth": linewidth,
        "linestyle": (0, (3.2, 2.2)),
        "connectionstyle": "arc3,rad=0",
        "shrinkA": 0,
        "shrinkB": 2,
    }


def _dodge_end_label_offsets(
    y_values: Sequence[float],
    ax: Axes,
    *,
    min_sep_pts: float = 20.0,
    x_base_pts: float = 16.0,
    x_stagger_pts: float = 6.0,
    heights_pts: Sequence[float] | None = None,
) -> list[tuple[float, float]]:
    """为曲线末端标签计算 (x_offset, y_offset)（单位：points），纵向 AABB 避让。

    返回值可直接用于 ``AnnotationBbox(..., xybox=offset, boxcoords="offset points")``。
    标签框高度可用 ``heights_pts`` 传入；缺省时用 ``min_sep_pts`` 作为统一间距。
    """
    n = len(y_values)
    if n == 0:
        return []
    if n == 1:
        return [(x_base_pts, 0.0)]

    renderer = _ensure_axes_renderer(ax)
    fig = ax.figure
    if fig is None:
        return [(x_base_pts, 0.0) for _ in range(n)]
    dpi = float(fig.dpi)
    px_per_pt = dpi / 72.0

    if heights_pts is None:
        half_h = [min_sep_pts * 0.5 * px_per_pt for _ in range(n)]
    else:
        if len(heights_pts) != n:
            raise ValueError("heights_pts 与 y_values 长度必须一致")
        half_h = [max(float(h), min_sep_pts) * 0.5 * px_per_pt for h in heights_pts]
    gap_px = 4.0 * px_per_pt  # 框与框之间最小空隙

    display_ys = [float(ax.transData.transform((0.0, float(y)))[1]) for y in y_values]
    order = sorted(range(n), key=lambda i: display_ys[i])
    packed = [display_ys[i] for i in order]
    half_sorted = [half_h[i] for i in order]

    for i in range(1, n):
        min_center = packed[i - 1] + half_sorted[i - 1] + half_sorted[i] + gap_px
        if packed[i] < min_center:
            packed[i] = min_center

    cluster_start = 0
    while cluster_start < n:
        cluster_end = cluster_start
        while cluster_end + 1 < n:
            need = packed[cluster_end] + half_sorted[cluster_end] + half_sorted[cluster_end + 1] + gap_px
            if abs(packed[cluster_end + 1] - need) < 1.0:
                cluster_end += 1
            else:
                break
        if cluster_end > cluster_start:
            size = cluster_end - cluster_start + 1
            orig_mean = sum(display_ys[order[k]] for k in range(cluster_start, cluster_end + 1)) / size
            pack_mean = sum(packed[cluster_start : cluster_end + 1]) / size
            shift = orig_mean - pack_mean
            for k in range(cluster_start, cluster_end + 1):
                packed[k] += shift
        cluster_start = cluster_end + 1

    for i in range(1, n):
        min_center = packed[i - 1] + half_sorted[i - 1] + half_sorted[i] + gap_px
        if packed[i] < min_center:
            packed[i] = min_center
    for i in range(n - 2, -1, -1):
        max_center = packed[i + 1] - half_sorted[i + 1] - half_sorted[i] - gap_px
        if packed[i] > max_center:
            packed[i] = max_center

    bbox = ax.get_window_extent(renderer=renderer)
    y_lo = float(bbox.y0) + half_sorted[0] + gap_px
    y_hi = float(bbox.y1) - half_sorted[-1] - gap_px
    if packed[0] < y_lo:
        shift = y_lo - packed[0]
        packed = [p + shift for p in packed]
    if packed[-1] > y_hi:
        shift = packed[-1] - y_hi
        packed = [p - shift for p in packed]
    for i in range(1, n):
        min_center = packed[i - 1] + half_sorted[i - 1] + half_sorted[i] + gap_px
        if packed[i] < min_center:
            packed[i] = min_center
    if packed[-1] > y_hi:
        for i in range(n - 2, -1, -1):
            max_center = packed[i + 1] - half_sorted[i + 1] - half_sorted[i] - gap_px
            if packed[i] > max_center:
                packed[i] = max_center

    span_need = packed[-1] - packed[0]
    span_have = max(y_hi - y_lo, 1.0)
    if span_need > span_have and n > 1:
        scale = span_have / span_need
        mid = (packed[0] + packed[-1]) / 2.0
        packed = [mid + (p - mid) * scale for p in packed]
        for i in range(1, n):
            min_center = packed[i - 1] + half_sorted[i - 1] + half_sorted[i] + gap_px * 0.5
            if packed[i] < min_center:
                packed[i] = min_center

    results: list[tuple[float, float]] = [(x_base_pts, 0.0) for _ in range(n)]
    for rank, orig_i in enumerate(order):
        y_offset_pts = (packed[rank] - display_ys[orig_i]) / px_per_pt
        stagger = (rank - (n - 1) / 2.0) * x_stagger_pts
        fan = min(abs(y_offset_pts) * 0.18, 28.0)
        density_fan = min(n * 1.2, 14.0)
        x_offset_pts = x_base_pts + fan + abs(stagger) * 0.35 + density_fan * (0.5 + 0.5 * (rank % 2))
        results[orig_i] = (x_offset_pts, y_offset_pts)
    return results


def _dodge_label_point_offsets(
    xy_data: Sequence[tuple[float, float]],
    preferred_offsets: Sequence[tuple[float, float]],
    ax: Axes,
    *,
    min_sep_pts: float = 8.0,
    max_iter: int = 120,
    sizes_pts: Sequence[tuple[float, float]] | None = None,
    ha: str = "center",
    va_from_offset: bool = True,
) -> list[tuple[float, float]]:
    """对任意位置的点标注做 2D AABB 互斥避让，返回调整后的 offset points。

    ``sizes_pts`` 为每个标签 (宽, 高) points；缺省时用 ``min_sep_pts`` 当方形框。
    ``ha`` / ``va_from_offset`` 与绘制时对齐一致，用于从 offset 推算框中心。
    """
    n = len(xy_data)
    if n == 0:
        return []
    if n != len(preferred_offsets):
        raise ValueError("xy_data 与 preferred_offsets 长度必须一致")
    if n == 1:
        return [list(preferred_offsets)[0]]

    renderer = _ensure_axes_renderer(ax)
    fig = ax.figure
    if fig is None:
        return [list(preferred_offsets)[0] for _ in range(n)]
    dpi = float(fig.dpi)
    px_per_pt = dpi / 72.0
    margin_px = max(min_sep_pts, 4.0) * px_per_pt

    if sizes_pts is None:
        box_wh = [(min_sep_pts * 2.5 * px_per_pt, min_sep_pts * 1.2 * px_per_pt) for _ in range(n)]
    else:
        if len(sizes_pts) != n:
            raise ValueError("sizes_pts 与 xy_data 长度必须一致")
        box_wh = [(max(float(w), 8.0) * px_per_pt, max(float(h), 8.0) * px_per_pt) for w, h in sizes_pts]

    anchor_disp: list[tuple[float, float]] = []
    label_center: list[list[float]] = []
    half_w = [w * 0.5 for w, _ in box_wh]
    half_h = [h * 0.5 for _, h in box_wh]

    def _offset_to_center(ox_pts: float, oy_pts: float, hw: float, hh: float) -> tuple[float, float]:
        ox_px = float(ox_pts) * px_per_pt
        oy_px = float(oy_pts) * px_per_pt
        if ha == "left":
            cx = ox_px + hw
        elif ha == "right":
            cx = ox_px - hw
        else:
            cx = ox_px
        if va_from_offset:
            if oy_pts >= 0:
                cy = oy_px + hh
            else:
                cy = oy_px - hh
        else:
            cy = oy_px
        return cx, cy

    def _center_to_offset(cx_px: float, cy_px: float, hw: float, hh: float) -> tuple[float, float]:
        if ha == "left":
            ox_px = cx_px - hw
        elif ha == "right":
            ox_px = cx_px + hw
        else:
            ox_px = cx_px
        if va_from_offset:
            if cy_px >= 0:
                oy_px = cy_px - hh
            else:
                oy_px = cy_px + hh
        else:
            oy_px = cy_px
        return ox_px / px_per_pt, oy_px / px_per_pt

    for (x, y), (ox, oy) in zip(xy_data, preferred_offsets, strict=True):
        ax_x, ax_y = ax.transData.transform((float(x), float(y)))
        anchor_disp.append((float(ax_x), float(ax_y)))
        i = len(label_center)
        dcx, dcy = _offset_to_center(float(ox), float(oy), half_w[i], half_h[i])
        label_center.append([float(ax_x) + dcx, float(ax_y) + dcy])

    ax_bbox = ax.get_window_extent(renderer=renderer)
    pad = 3.0 * px_per_pt

    def _clamp_center(i: int) -> None:
        lo_x = float(ax_bbox.x0) + half_w[i] + pad
        hi_x = float(ax_bbox.x1) - half_w[i] - pad
        lo_y = float(ax_bbox.y0) + half_h[i] + pad
        hi_y = float(ax_bbox.y1) - half_h[i] - pad
        if lo_x <= hi_x:
            label_center[i][0] = min(max(label_center[i][0], lo_x), hi_x)
        if lo_y <= hi_y:
            label_center[i][1] = min(max(label_center[i][1], lo_y), hi_y)

    for i in range(n):
        _clamp_center(i)

    for _ in range(max_iter):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx = label_center[j][0] - label_center[i][0]
                dy = label_center[j][1] - label_center[i][1]
                need_x = half_w[i] + half_w[j] + margin_px
                need_y = half_h[i] + half_h[j] + margin_px
                overlap_x = need_x - abs(dx)
                overlap_y = need_y - abs(dy)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue
                if overlap_y <= overlap_x:
                    push = overlap_y / 2.0 + 0.5
                    sign = 1.0 if dy >= 0 else -1.0
                    if abs(dy) < 1e-6:
                        sign = 1.0 if (i + j) % 2 == 0 else -1.0
                    label_center[i][1] -= sign * push
                    label_center[j][1] += sign * push
                else:
                    push = overlap_x / 2.0 + 0.5
                    sign = 1.0 if dx >= 0 else -1.0
                    if abs(dx) < 1e-6:
                        sign = 1.0 if (i + j) % 2 == 0 else -1.0
                    label_center[i][0] -= sign * push
                    label_center[j][0] += sign * push
                moved = True
        for i in range(n):
            before = (label_center[i][0], label_center[i][1])
            _clamp_center(i)
            if abs(label_center[i][0] - before[0]) > 0.01 or abs(label_center[i][1] - before[1]) > 0.01:
                moved = True
        if not moved:
            break

    for i in range(n):
        ax_x, ax_y = anchor_disp[i]
        pref_ox, pref_oy = preferred_offsets[i]
        tcx, tcy = _offset_to_center(float(pref_ox), float(pref_oy), half_w[i], half_h[i])
        target_x, target_y = ax_x + tcx, ax_y + tcy
        label_center[i][0] += (target_x - label_center[i][0]) * 0.08
        label_center[i][1] += (target_y - label_center[i][1]) * 0.08
        _clamp_center(i)

    for _ in range(40):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx = label_center[j][0] - label_center[i][0]
                dy = label_center[j][1] - label_center[i][1]
                need_x = half_w[i] + half_w[j] + margin_px
                need_y = half_h[i] + half_h[j] + margin_px
                if need_x - abs(dx) <= 0 or need_y - abs(dy) <= 0:
                    continue
                if need_y - abs(dy) <= need_x - abs(dx):
                    push = (need_y - abs(dy)) / 2.0 + 0.5
                    sign = 1.0 if dy >= 0 else -1.0
                    if abs(dy) < 1e-6:
                        sign = 1.0 if i < j else -1.0
                    label_center[i][1] -= sign * push
                    label_center[j][1] += sign * push
                else:
                    push = (need_x - abs(dx)) / 2.0 + 0.5
                    sign = 1.0 if dx >= 0 else -1.0
                    if abs(dx) < 1e-6:
                        sign = 1.0 if i < j else -1.0
                    label_center[i][0] -= sign * push
                    label_center[j][0] += sign * push
                moved = True
        if not moved:
            break
        for i in range(n):
            _clamp_center(i)

    results: list[tuple[float, float]] = []
    for i in range(n):
        ax_x, ax_y = anchor_disp[i]
        cx = label_center[i][0] - ax_x
        cy = label_center[i][1] - ax_y
        ox, oy = _center_to_offset(cx, cy, half_w[i], half_h[i])
        if abs(ox) < 6.0 and abs(oy) < 6.0:
            oy = 14.0 if cy >= 0 else -14.0
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
    """绘制曲线末端「圆点 + 虚线引出 + 名称/数值」标签，并做纵向 AABB 避让。

    entries 每项为 ``(x, y, name, value_text, color)``，value_text 含前导空格，如 `` +1.23%``。
    """
    if not entries:
        return
    fontsize = 11.0
    heights: list[float] = []
    for item in entries:
        name, value_text = item[2], item[3]
        _, h = _estimate_text_box_pts(f"{name}{value_text}", fontsize, pad_pts=5.0)
        heights.append(h)

    offsets = _dodge_end_label_offsets(
        [item[1] for item in entries],
        ax,
        min_sep_pts=min_sep_pts,
        x_base_pts=x_base_pts,
        x_stagger_pts=x_stagger_pts,
        heights_pts=heights,
    )
    for (x, y, name, value_text, color), (x_off, y_off) in zip(entries, offsets, strict=True):
        ax.scatter(
            [x],
            [y],
            color=color,
            edgecolor=BG_COLOR,
            s=48,
            zorder=5,
            linewidths=1.1,
        )
        value_color = value_color_fn(value_text) if value_color_fn is not None else FG_COLOR
        name_area = TextArea(
            name,
            textprops={"color": color, "fontsize": fontsize, "fontweight": FONT_W_SEMIBOLD},
        )
        value_area = TextArea(
            value_text,
            textprops={"color": value_color, "fontsize": fontsize, "fontweight": FONT_W_MED},
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
            pad=0.28,
            bboxprops={
                "facecolor": BG_COLOR,
                "edgecolor": color,
                "alpha": 0.82,
                "boxstyle": "round,pad=0.25",
            },
            arrowprops=_leader_arrowprops(color, linewidth=1.05),
            zorder=6,
        )
        ax.add_artist(artist)


def _draw_dodged_text_labels(
    ax: Axes,
    entries: Sequence[tuple[float, float, str, str, tuple[float, float]]],
    *,
    min_sep_pts: float = 6.0,
    fontsize: float = 10.0,
    ha: str = "center",
    va_from_offset: bool = True,
    scatter: bool = True,
    scatter_size: float = 48.0,
) -> None:
    """绘制「圆点 + 虚线引出 + 文本框」标签，按文本框 AABB 做 2D 避让。

    entries 每项为 ``(x, y, text, color, preferred_offset_pts)``。
    """
    if not entries:
        return
    xy_data = [(float(item[0]), float(item[1])) for item in entries]
    preferred = [item[4] for item in entries]
    sizes = [_estimate_text_box_pts(item[2], fontsize, pad_pts=5.0) for item in entries]
    offsets = _dodge_label_point_offsets(
        xy_data,
        preferred,
        ax,
        min_sep_pts=min_sep_pts,
        sizes_pts=sizes,
        ha=ha,
        va_from_offset=va_from_offset,
    )
    for (x, y, text, color, _), (ox, oy) in zip(entries, offsets, strict=True):
        if scatter:
            ax.scatter(
                [x],
                [y],
                color=color,
                edgecolor=BG_COLOR,
                s=scatter_size,
                zorder=5,
                linewidths=1.1,
            )
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
            fontweight=FONT_W_SEMIBOLD,
            ha=ha,
            va=va,
            bbox={
                "facecolor": BG_COLOR,
                "edgecolor": color,
                "alpha": 0.82,
                "pad": 2.8,
                "boxstyle": "round,pad=0.28",
            },
            arrowprops=_leader_arrowprops(color, linewidth=0.95),
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


def _hide_root_x_tick_labels(fig: Figure) -> None:
    """关掉 mplchart root 轴底部日期字。

    root 铺满整张图，自带一套日期刻度；最下 pane 再画 HH:MM / 年月后，
    底边会出现一行横字叠一行斜字。时间只留 pane 上那一套。
    """
    for ax in fig.axes:
        if ax.get_label() == "root":
            ax.tick_params(axis="x", which="both", labelbottom=False, labeltop=False)


def _apply_pane_x_tick_labels(ax: Axes, positions: list[int], labels: list[str]) -> None:
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    fig = ax.figure
    if isinstance(fig, Figure):
        _hide_root_x_tick_labels(fig)


def _apply_intraday_day_separators(
    ax: Axes,
    *,
    starts: list[int],
    n_points: int,
    shade: bool = True,
) -> None:
    """五日分时：隔日竖线 + 隔日浅底，和当日分时的 10 分钟刻度互斥。"""
    if n_points <= 0 or len(starts) < 2:
        return
    bounds = starts + [n_points]
    if shade:
        for index in range(len(starts)):
            if index % 2 == 1:
                ax.axvspan(
                    float(bounds[index]) - 0.5,
                    float(bounds[index + 1]) - 0.5,
                    facecolor="#ffffff",
                    alpha=0.045,
                    zorder=0,
                )
    for pos in starts[1:]:
        ax.axvline(float(pos) - 0.5, color=DAY_SEP_COLOR, linestyle="--", alpha=0.72, linewidth=1.2, zorder=2)


def _apply_intraday_day_ticks(ax: Axes, positions: list[int], labels: list[str]) -> None:
    if not positions or not labels:
        return
    _apply_pane_x_tick_labels(ax, positions, labels)
    ax.tick_params(axis="x", rotation=0)
    for lab in ax.get_xticklabels():
        text = lab.get_text()
        lines = text.split("\n")
        if len(lines) < 2:
            continue
        chg = lines[-1].strip()
        if chg.startswith("+"):
            lab.set_color(UP_COLOR)
        elif chg.startswith("-"):
            lab.set_color(DOWN_COLOR)
        lab.set_fontsize(11)
        lab.set_linespacing(1.2)


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
    _apply_pane_x_tick_labels(ax, tick_positions, tick_labels)
    # 分时 HH:MM 很短，保持水平，避免和已关掉的 root 斜标签「看起来还在」
    ax.tick_params(axis="x", rotation=0)
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
    _apply_pane_x_tick_labels(ax, tick_positions, tick_labels)


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
    _apply_pane_x_tick_labels(ax, tick_positions, tick_labels)


def _axes_top_to_bottom(fig: Figure) -> list[Axes]:
    axes = [ax for ax in fig.axes if isinstance(ax, Axes) and ax.get_label() not in {"root", "twinx"}]
    return sorted(axes, key=lambda item: item.get_position().y0, reverse=True)
