"""Kronos forecast chart data, annotations and missing model labels."""

from math import sin
from datetime import datetime, timedelta

from SayuStock.render.chart_page import ChartPage, page_html
from SayuStock.stock_ai.forecast_chart import draw_forecast_chart


def _render_sample(*, tokenizer: str = "", model: str = "", device: str = "") -> ChartPage:
    start = datetime(2025, 9, 1)
    times = [start + timedelta(days=i) for i in range(151)]
    hist_y = [42 + i * 0.08 + sin(i / 12) * 2 for i in range(121)]
    backtest_mean = [hist_y[i] + 0.6 * sin(i / 4) for i in range(93, 121)]
    future_mean = [hist_y[-1] + i * 0.12 + 0.3 * sin(i / 5) for i in range(30)]
    chart = draw_forecast_chart(
        title="远山科技（含回测与预测）",
        tokenizer=tokenizer,
        model=model,
        device=device,
        source="数据来源：东方财富",
        period="周期 1D",
        hist_t=times[:121],
        hist_y=hist_y,
        backtest_t=times[93:121],
        backtest_mean=backtest_mean,
        backtest_min=[value - 0.35 - 0.02 * i for i, value in enumerate(backtest_mean)],
        backtest_max=[value + 0.35 + 0.02 * i for i, value in enumerate(backtest_mean)],
        future_t=times[121:],
        future_mean=future_mean,
        future_min=[value - 0.4 - 0.10 * i for i, value in enumerate(future_mean)],
        future_max=[value + 0.4 + 0.10 * i for i, value in enumerate(future_mean)],
        last_close=hist_y[-1],
        backtest_start=times[93],
        future_start=times[120],
    )
    assert not isinstance(chart, str), chart
    return chart


def test_forecast_chart_cross_year_and_model_note() -> None:
    page = _render_sample(tokenizer="NeoQuasar/Kronos-Tokenizer-base", model="NeoQuasar/Kronos-mini", device="cpu")
    html = page_html(page)
    assert "NeoQuasar/Kronos-mini" in html and "NeoQuasar/Kronos-Tokenizer-base" in html and "设备 cpu" in html
    assert "回测开始" in html and "预测开始" in html
    panel = page.panels[0]
    assert len(panel.series) == 3 and len(panel.fills) == 2
    assert panel.x_ticks[0].label == "2025-09-01"
    assert panel.x_ticks[-1].label.startswith("2026-")


def test_forecast_chart_without_model_note() -> None:
    html = page_html(_render_sample())
    assert "Tokenizer" not in html
    assert "设备" not in html
    assert "<svg" in html and "周期 1D" in html
