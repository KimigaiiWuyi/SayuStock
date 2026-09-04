"""mplchart 新旧版本兼容层。

旧版（如 0.0.37）与新版（如 0.0.46+）有多处 API 差异：

- ``Price``：旧版在 ``mplchart.primitives`` 中；新版已移除，改用 ``LinePlot`` 画单列。
- ``Chart(bgcolor=...)``：新版构造器不再接受 ``bgcolor``，且 ``color_scheme`` 被忽略。
  新版默认 ``style="mplchart"`` 是浅色；数据 pane 的 patch 透明，真正底色在
  ``label="root"`` 的轴上。兼容层把 ``bgcolor`` 转成暗色 ``style`` spec。
- ``chart.add_legends()`` / ``chart.main_axes()``：新版迁到 ``chart.canvas`` 上。
- ``chart.mapper``：新版改 ``chart.view``；KDJ 填色走 ``chart_series_xy``。

业务代码统一从本模块导入 ``Chart`` / ``Price`` 等符号，即可同时跑通两套版本。
"""

from __future__ import annotations

import inspect
from typing import Any, cast

from mplchart import primitives as _mpl_primitives
from mplchart.chart import Chart as _MplChart
from mplchart.indicators import SMA, Indicator
from mplchart.primitives import (
    Pane,
    HLine,
    Volume,
    BarPlot,
    LinePlot,
    Candlesticks,
)

# 新版才有 Bands；旧版靠 autoplot + color_scheme
Bands = cast("type[Any] | None", getattr(_mpl_primitives, "Bands", None))

# getattr：新版 stubs/包里可能没有 Price，避免 reportAttributeAccessIssue
_NativePrice = cast("type[LinePlot] | None", getattr(_mpl_primitives, "Price", None))


__all__ = [
    "Bands",
    "BarPlot",
    "Candlesticks",
    "Chart",
    "HLine",
    "Indicator",
    "LinePlot",
    "Pane",
    "Price",
    "SMA",
    "Volume",
    "chart_series_xy",
    "dark_style",
]


class _PriceCompat(LinePlot):
    """新版 mplchart 无 Price 时的兼容实现：等价于画指定价格列的 LinePlot。"""

    def __init__(
        self,
        item: str = "close",
        *,
        width: float = 1.0,
        alpha: float = 1.0,
        color: str | None = None,
    ) -> None:
        # 列名作为 indicator 传入：新旧 DataView/calc 都能按列解析
        super().__init__(item, width=width, alpha=alpha, color=color, label=str(item))


# 统一导出名 Price：有原生类用原生，否则用兼容实现（避免 class 重定义触发 no-redef）
Price: type[LinePlot] = _NativePrice if _NativePrice is not None else _PriceCompat


_CHART_INIT_PARAMS = inspect.signature(_MplChart.__init__).parameters


def dark_style(bgcolor: str) -> dict[str, Any]:
    """新版 ``Chart(style=...)`` 用的暗色 spec，对齐旧 ``bgcolor``。"""
    return {
        "stylesheet": "dark_background",
        "rc": {
            "figure.facecolor": bgcolor,
            "axes.facecolor": bgcolor,
            "savefig.facecolor": bgcolor,
            "savefig.edgecolor": bgcolor,
            "text.color": "#f5f5f5",
            "axes.labelcolor": "#d8d8d8",
            "xtick.color": "#d8d8d8",
            "ytick.color": "#d8d8d8",
            "axes.edgecolor": "#d8d8d8",
            "grid.color": "#777777",
            "axes.grid": True,
            "grid.alpha": 0.36,
        },
        "settings": {
            "yaxis.right": True,
        },
    }


class Chart(_MplChart):
    """兼容包装：屏蔽新旧 Chart 构造参数与辅助方法差异。"""

    def __init__(
        self,
        prices: Any = None,
        *,
        title: Any = None,
        max_bars: Any = None,
        start: Any = None,
        end: Any = None,
        figure: Any = None,
        figsize: Any = None,
        bgcolor: Any = None,
        holidays: Any = None,
        normalize: bool = False,
        raw_dates: bool = False,
        style: Any = None,
        color_scheme: Any = (),
        **extra: Any,
    ) -> None:
        # 新版没有 bgcolor / 虽有但已弃用：调用方仍传 bgcolor= 时转成 style。
        if bgcolor is not None and style is None and "style" in _CHART_INIT_PARAMS:
            style = dark_style(str(bgcolor))

        init_kwargs: dict[str, Any] = {
            "title": title,
            "max_bars": max_bars,
            "start": start,
            "end": end,
            "figure": figure,
            "figsize": figsize,
            "normalize": normalize,
            "raw_dates": raw_dates,
            "color_scheme": color_scheme,
            "style": style,
            "bgcolor": bgcolor,
            "holidays": holidays,
            **extra,
        }
        # 只透传当前已安装 mplchart 实际支持的参数，避免新版因 bgcolor 等直接 TypeError
        filtered = {key: value for key, value in init_kwargs.items() if key in _CHART_INIT_PARAMS and value is not None}
        # 新版仍接收 color_scheme / bgcolor 但会 DeprecationWarning；有 style 就不要再传
        if "style" in _CHART_INIT_PARAMS:
            filtered.pop("color_scheme", None)
            filtered.pop("bgcolor", None)
        elif "color_scheme" in _CHART_INIT_PARAMS and "color_scheme" not in filtered:
            filtered["color_scheme"] = color_scheme
        # normalize / raw_dates 是 bool，False 也要传
        for flag in ("normalize", "raw_dates"):
            if flag in _CHART_INIT_PARAMS:
                filtered[flag] = init_kwargs[flag]

        super().__init__(prices, **filtered)

    def add_legends(self) -> Any:
        method = getattr(_MplChart, "add_legends", None)
        if callable(method):
            return method(self)
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            raise AttributeError("chart has no canvas")
        return canvas.add_legends()

    def main_axes(self) -> Any:
        method = getattr(_MplChart, "main_axes", None)
        if callable(method):
            return method(self)
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            raise AttributeError("chart has no canvas")
        return canvas.main_axes()


def chart_series_xy(chart: Chart, *series: object) -> tuple[object, ...] | None:
    """KDJ 填色等：新版 ``chart.view.series_xy``，旧版 ``chart.mapper.series_xy``。"""
    src = getattr(chart, "view", None)
    if src is None:
        src = getattr(chart, "mapper", None)
    if src is None:
        return None
    fn = getattr(src, "series_xy", None)
    if not callable(fn):
        return None
    raw = fn(*series)
    if not isinstance(raw, tuple):
        return None
    return raw
