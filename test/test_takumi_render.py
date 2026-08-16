"""Kronos 预测图（matplotlib）回归，不依赖 Playwright。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.stock_ai.forecast_chart import draw_forecast_chart


def test_forecast_chart_png_header() -> None:
    times = [datetime(2026, 1, 1, 10, i) for i in range(8)]
    img = draw_forecast_chart(
        title="测试股 (含回测与预测)",
        hist_t=times[:6],
        hist_y=[10, 10.2, 10.1, 10.4, 10.3, 10.5],
        backtest_t=times[3:6],
        backtest_mean=[10.2, 10.3, 10.45],
        backtest_min=[10.0, 10.1, 10.2],
        backtest_max=[10.4, 10.5, 10.7],
        future_t=times[6:],
        future_mean=[10.6, 10.7],
        future_min=[10.4, 10.5],
        future_max=[10.8, 10.9],
        last_close=10.5,
        backtest_start=times[3],
        future_start=times[5],
    )
    assert img.size[0] > 100
    assert img.size[1] > 80
