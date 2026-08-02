"""mplchart 新旧版本兼容层。

旧版（如 0.0.37）与新版（如 0.0.46+）有多处 API 差异：

- ``Price``：旧版在 ``mplchart.primitives`` 中；新版已移除，改用 ``LinePlot`` 画单列。
- ``Chart(bgcolor=...)``：新版构造器不再接受 ``bgcolor``（背景色改走 matplotlib rc / style）。
- ``chart.add_legends()`` / ``chart.main_axes()``：新版迁到 ``chart.canvas`` 上。

业务代码统一从本模块导入 ``Chart`` / ``Price`` 等符号，即可同时跑通两套版本。
"""

from __future__ import annotations

import inspect
from typing import Any

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

try:
    from mplchart.primitives import Price as _NativePrice
except ImportError:  # mplchart >= 0.0.46 移除了 Price
    _NativePrice = None


__all__ = [
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
        # color_scheme 旧版默认 ()，需要保留空映射语义；新版虽弃用但仍接受
        if "color_scheme" in _CHART_INIT_PARAMS and "color_scheme" not in filtered:
            filtered["color_scheme"] = color_scheme
        # normalize / raw_dates 是 bool，False 也要传
        for flag in ("normalize", "raw_dates"):
            if flag in _CHART_INIT_PARAMS:
                filtered[flag] = init_kwargs[flag]

        super().__init__(prices, **filtered)

    def add_legends(self) -> Any:
        if hasattr(_MplChart, "add_legends"):
            return _MplChart.add_legends(self)
        return self.canvas.add_legends()

    def main_axes(self) -> Any:
        if hasattr(_MplChart, "main_axes"):
            return _MplChart.main_axes(self)
        return self.canvas.main_axes()
