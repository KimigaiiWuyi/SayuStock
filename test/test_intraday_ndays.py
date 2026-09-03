"""五日分时：命令前缀、解析、会话补齐、日分隔、AI 文字。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from dataclasses import replace

import pytest

from SayuStock.utils.render_data import build_single_stock_render_data
from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.stock_period import (
    is_intraday_sector,
    parse_stock_img_request,
    intraday_ndays_from_sector,
)
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import Quote, SymbolRef, IntradayPoint, IntradaySeries
from SayuStock.utils.market.adapters.eastmoney.parse_intraday import (
    parse_trend_line,
    parse_intraday_from_trends_list,
)


def _symbol() -> SymbolRef:
    return SymbolRef(
        code="000001",
        name="上证指数",
        asset_class=AssetClass.INDEX,
        exchange="SSE",
        provider_symbol="1.000001",
        sec_type="指数",
    )


def _quote(symbol: SymbolRef, price: float) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=3900.0,
        change_pct=0.5,
        change_amount=None,
        volume=1.0,
        amount=1.0,
        turnover_rate=1.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=None,
    )


def _five_day_series() -> IntradaySeries:
    days = ("2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01")
    points: list[IntradayPoint] = []
    price = 3880.0
    for day in days:
        for hm, bump in (("09:31", 1.0), ("10:00", 2.0), ("14:00", -1.0)):
            price += bump
            ts = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
            points.append(
                IntradayPoint(
                    ts=ts,
                    price=price,
                    open=price,
                    high=price,
                    low=price,
                    volume=100.0,
                    amount=1000.0,
                    avg_price=price,
                )
            )
    symbol = _symbol()
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=_quote(symbol, price), ndays=5)


def test_parse_stock_img_request_five_day() -> None:
    assert parse_stock_img_request("五日 贵州茅台") == ("贵州茅台", "single-stock-ndays-5")
    assert parse_stock_img_request("5日 上证指数") == ("上证指数", "single-stock-ndays-5")
    assert parse_stock_img_request("五日分时 600519") == ("600519", "single-stock-ndays-5")
    assert parse_stock_img_request("5天茅台") == ("茅台", "single-stock-ndays-5")
    assert parse_stock_img_request("日k 贵州茅台") == ("贵州茅台", "single-stock-kline-101")
    assert parse_stock_img_request("贵州茅台") == ("贵州茅台", "single-stock")
    assert parse_stock_img_request("分时 证券ETF") == ("证券ETF", "single-stock")
    assert is_intraday_sector("single-stock-ndays-5")
    assert is_intraday_sector("single-stock")
    assert not is_intraday_sector("single-stock-kline-101")
    assert intraday_ndays_from_sector("single-stock-ndays-5") == 5
    assert intraday_ndays_from_sector("single-stock") == 1


def test_parse_trend_line_zero_price_uses_open() -> None:
    point = parse_trend_line("2026-08-26 09:30,0.00,3881.74,3881.74,3881.74,100,200,3888.25")
    assert point is not None
    assert point.price == 3881.74
    assert point.open == 3881.74
    assert point.avg_price == 3888.25


def test_parse_five_day_trends_sets_ndays() -> None:
    lines = [
        "2026-08-26 09:30,0.00,3881.74,3881.74,3881.74,1,1,3888.25",
        "2026-08-26 15:00,3890.00,3890.10,3890.10,3889.90,1,1,3885.00",
        "2026-08-27 09:31,3891.00,3891.00,3891.00,3891.00,1,1,3891.00",
        "2026-09-01 15:00,3979.65,3979.89,3979.89,3979.65,1,1,3992.38",
    ]
    series = parse_intraday_from_trends_list(lines, _symbol(), ndays=5)
    assert not is_market_error(series)
    assert series.ndays == 5
    assert series.points[0].price == 3881.74
    dates = {p.ts.date() for p in series.points}
    assert len(dates) == 3


def test_five_day_render_keeps_day_panels() -> None:
    series = _five_day_series()
    result = build_single_stock_render_data(series)
    assert not isinstance(result, str), result
    assert result.ndays == 5
    assert "5日分时" in result.title_text
    assert len(result.day_starts) == 5
    assert len(result.day_tick_labels) == 5
    dates = sorted({ts.date() for ts in result.df["dt"] if ts is not None})
    assert len(dates) == 5
    assert result.day_tick_labels[0].startswith("08-26")
    assert "\n" in result.day_tick_labels[0]
    # 每个交易日应铺满 A 股会话，不能被压成一根当日轴
    first_day = result.df[result.df["dt"].dt.date == dates[0]]
    last_ts = first_day["dt"].max()
    assert last_ts.hour == 15 and last_ts.minute == 0
    first_ts = first_day["dt"].min()
    assert first_ts.hour == 9 and first_ts.minute == 30
    last_day = result.df[result.df["dt"].dt.date == dates[-1]]
    last_day_ts = last_day["dt"].max()
    assert last_day_ts.hour == 15 and last_day_ts.minute == 0
    assert len(first_day) == len(last_day)
    quote = series.quote
    assert quote is not None
    assert result.open_price != quote.open
    assert result.open_price != quote.prev_close
    first_last = [p.price for p in series.points if p.ts.date() == dates[0]][-1]
    assert result.open_price == first_last
    assert "五日累计" in result.title_text
    assert "今日" in result.title_text
    assert "开盘价" not in result.title_text
    assert "五日" in result.custom_info and "今日" in result.custom_info
    assert abs(result.today_change - 0.5) < 1e-9


def test_one_day_render_has_no_five_day_title() -> None:
    symbol = _symbol()
    series = IntradaySeries(
        symbol=symbol,
        points=(
            IntradayPoint(
                ts=datetime(2026, 9, 1, 9, 31),
                price=3970.0,
                open=3970.0,
                high=3970.0,
                low=3970.0,
                volume=1.0,
                amount=1.0,
                avg_price=3970.0,
            ),
        ),
        quote=_quote(symbol, 3970.0),
        ndays=1,
    )
    result = build_single_stock_render_data(series)
    assert not isinstance(result, str), result
    assert result.ndays == 1
    assert "日分时" not in result.title_text
    assert result.day_starts == []


def test_five_day_text_lists_daily_closes() -> None:
    from SayuStock.utils import render_text as rt

    text = rt.single_stock_text(_five_day_series(), ndays=5)
    assert "5日分时" in text
    assert "分日收盘" in text
    assert "08-26" in text
    assert "09-01" in text
    assert "五日累计" in text
    assert "今日涨跌幅" in text


def test_five_day_zero_axis_uses_ref_close() -> None:
    series = replace(_five_day_series(), ref_close=3800.0)
    result = build_single_stock_render_data(series)
    assert not isinstance(result, str), result
    assert result.open_price == 3800.0
    last = float(result.df["price"].dropna().iloc[-1])
    expected = (last / 3800.0 - 1.0) * 100.0
    assert abs(result.ndays_change - expected) < 1e-6
    last_pct = float(result.df["percentage_change"].dropna().iloc[-1])
    assert abs(last_pct - expected) < 1e-6
    assert "+2.16%" in result.day_tick_labels[0]


def test_five_day_chart_xlim_covers_padded_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    from SayuStock.stock_stockinfo import chart_intraday as ci

    captured: list[tuple[float, float]] = []

    def _capture(fig: object) -> Image.Image:
        axes = getattr(fig, "axes", [])
        for ax in axes:
            label = ax.get_label() if hasattr(ax, "get_label") else ""
            if label in {"root", "twinx"}:
                continue
            captured.append(ax.get_xlim())
        return Image.new("RGB", (10, 10))

    monkeypatch.setattr(ci, "_fig_to_image", _capture)
    series = _five_day_series()
    result = ci.draw_single_stock_chart(series)
    assert not isinstance(result, str), result
    data = build_single_stock_render_data(series)
    assert not isinstance(data, str)
    n_points = len(data.df)
    assert captured
    for left, right in captured:
        assert left <= 0.0
        assert right >= float(n_points) - 1.0 - 1e-6


def test_emit_ai_text_five_day_sector(monkeypatch: pytest.MonkeyPatch) -> None:
    from SayuStock.stock_stockinfo import render_mpl as rm

    captured: list[str] = []
    monkeypatch.setattr(rm, "ai_return", captured.append)
    rm._emit_ai_text("上证指数", "single-stock-ndays-5", _five_day_series(), [])
    assert captured
    assert "5日分时" in captured[0]
    assert "分日收盘" in captured[0]
    assert "五日累计" in captured[0]
    assert "今日涨跌幅" in captured[0]


def test_data_fetch_forwards_ndays() -> None:
    from SayuStock.utils.market import set_market
    from SayuStock.stock_stockinfo.data import CloudMapDataService
    from SayuStock.utils.market.adapters._base import PartialMarketData

    series = _five_day_series()
    calls: list[tuple[str, int]] = []

    class _Port(PartialMarketData):
        async def resolve(self, query: str) -> SymbolRef | None:
            return series.symbol

        async def quote(self, query: str) -> Quote:
            q = series.quote
            assert q is not None
            return q

        async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries:
            calls.append((query, ndays))
            return series

    async def _run() -> None:
        set_market(_Port())
        try:
            result = await CloudMapDataService().fetch("上证指数", "single-stock-ndays-5", None, None)
            assert result.sector == "single-stock-ndays-5"
            assert isinstance(result.raw_data, IntradaySeries)
            assert result.raw_data.ndays == 5
            assert calls == [("上证指数", 5)]
        finally:
            set_market(None)

    asyncio.run(_run())


def test_draw_five_day_chart_is_line_not_error() -> None:
    from SayuStock.stock_stockinfo.chart_intraday import draw_single_stock_chart

    result = draw_single_stock_chart(_five_day_series())
    assert not isinstance(result, str), result
    assert result.size[0] > 100 and result.size[1] > 100


def test_five_day_tick_labels_show_dates() -> None:
    import matplotlib.pyplot as plt

    from SayuStock.stock_stockinfo.chart_base import _apply_intraday_day_ticks

    fig, ax = plt.subplots()
    _apply_intraday_day_ticks(ax, [10, 30, 50], ["08-26\n+1.20%", "08-27\n-0.30%", "08-28\n+0.50%"])
    texts = [lab.get_text() for lab in ax.get_xticklabels()]
    assert any("08-26" in text for text in texts)
    assert any("+" in text or "-" in text for text in texts)
    plt.close(fig)
