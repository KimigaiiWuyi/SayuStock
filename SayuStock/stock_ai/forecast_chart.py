"""Kronos 回测/预测折线图（matplotlib）。

旧路径是 plotly Figure → ``write_html`` → Playwright 等 ``.plot-container``。
预测图是带置信带的时间序列，不适合改成静态 HTML/CSS；matplotlib 与个股 K 线
同一套 Agg 栈，中文走 GsCore MiSans。
"""

from __future__ import annotations

from datetime import datetime
from collections.abc import Sequence

import numpy as np
import pandas as pd
from PIL import Image
from numpy.typing import NDArray
from matplotlib.dates import date2num

from ..stock_stockinfo.chart_base import plt, _setup_mpl, _fig_to_image

_HIST = "#1f77b4"
_BACKTEST = "#2ca02c"
_FUTURE = "#ff7f0e"
_GRID = "#d0d0d0"


def _as_py_dt(value: object) -> datetime:
    parsed = pd.to_datetime(pd.Series([value], dtype="object"), errors="raise")
    raw = parsed.iloc[0]
    if not isinstance(raw, pd.Timestamp):
        raise TypeError("预测图时间轴无法解析为 Timestamp")
    py = raw.to_pydatetime()
    if py.tzinfo is not None:
        return py.replace(tzinfo=None)
    return py


def _as_mpl_x(values: Sequence[object]) -> NDArray[np.float64]:
    """matplotlib 轴坐标：date2num 浮点，避免 list[datetime] 对不上 ArrayLike。"""
    nums = [float(np.asarray(date2num(_as_py_dt(v)), dtype=np.float64).reshape(-1)[0]) for v in values]
    return np.asarray(nums, dtype=np.float64)


def _as_mpl_x1(value: object) -> float:
    return float(np.asarray(date2num(_as_py_dt(value)), dtype=np.float64).reshape(-1)[0])


def _as_floats(values: Sequence[object] | NDArray[np.floating]) -> NDArray[np.float64]:
    return np.asarray(values, dtype=np.float64)


def draw_forecast_chart(
    *,
    title: str,
    hist_t: Sequence[object],
    hist_y: Sequence[object] | NDArray[np.floating],
    backtest_t: Sequence[object],
    backtest_mean: Sequence[object] | NDArray[np.floating],
    backtest_min: Sequence[object] | NDArray[np.floating],
    backtest_max: Sequence[object] | NDArray[np.floating],
    future_t: Sequence[object],
    future_mean: Sequence[object] | NDArray[np.floating],
    future_min: Sequence[object] | NDArray[np.floating],
    future_max: Sequence[object] | NDArray[np.floating],
    last_close: float,
    backtest_start: object,
    future_start: object,
) -> Image.Image:
    _setup_mpl()
    hist_x = _as_mpl_x(hist_t)
    hist_close = _as_floats(hist_y)
    bt_x = _as_mpl_x(backtest_t)
    bt_mean = _as_floats(backtest_mean)
    bt_min = _as_floats(backtest_min)
    bt_max = _as_floats(backtest_max)
    fu_x = _as_mpl_x(future_t)
    fu_mean = _as_floats(future_mean)
    fu_min = _as_floats(future_min)
    fu_max = _as_floats(future_max)

    fig, ax = plt.subplots(figsize=(20, 10), dpi=160)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    if len(hist_x) and len(hist_close):
        ax.plot(hist_x, hist_close, color=_HIST, linewidth=2.0, label="历史实际走势")

    if len(bt_x) and len(bt_mean):
        if len(bt_min) == len(bt_x) and len(bt_max) == len(bt_x):
            ax.fill_between(bt_x, bt_min, bt_max, color=_BACKTEST, alpha=0.2, label="回测范围 (Min–Max)")
        ax.plot(bt_x, bt_mean, color=_BACKTEST, linewidth=2.0, linestyle=":", label="回测-预测均值")

    if len(fu_x) and len(fu_mean):
        if len(fu_min) == len(fu_x) and len(fu_max) == len(fu_x):
            ax.fill_between(fu_x, fu_min, fu_max, color=_FUTURE, alpha=0.28, label="未来范围 (Min–Max)")
        if len(hist_x):
            conn_x = np.concatenate((hist_x[-1:], fu_x))
            conn_y = np.concatenate((np.asarray([last_close], dtype=np.float64), fu_mean))
        else:
            conn_x = fu_x
            conn_y = fu_mean
        ax.plot(conn_x, conn_y, color=_FUTURE, linewidth=2.0, label="未来-预测均值")

    if backtest_start is not None:
        bt_x0 = _as_mpl_x1(backtest_start)
        ax.axvline(bt_x0, color="#888888", linestyle="--", linewidth=1.6)
        ax.text(bt_x0, 1.01, "回测开始", transform=ax.get_xaxis_transform(), color="#888888", ha="right", va="bottom")
    if future_start is not None:
        fu_x0 = _as_mpl_x1(future_start)
        ax.axvline(fu_x0, color="#d62728", linestyle="--", linewidth=1.6)
        ax.text(fu_x0, 1.01, "预测开始", transform=ax.get_xaxis_transform(), color="#d62728", ha="left", va="bottom")

    ax.set_title(title, fontsize=22, color="#222222", pad=16)
    ax.set_xlabel("时间", fontsize=14, color="#333333")
    ax.set_ylabel("价格", fontsize=14, color="#333333")
    ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.85)
    ax.tick_params(colors="#333333")
    for spine in ax.spines.values():
        spine.set_color("#bbbbbb")
    legend = ax.legend(loc="upper left", fontsize=11, framealpha=0.95, facecolor="#ffffff", edgecolor="#cccccc")
    for text in legend.get_texts():
        text.set_color("#222222")
    ax.xaxis_date()
    fig.autofmt_xdate()
    return _fig_to_image(fig, dpi=160)
