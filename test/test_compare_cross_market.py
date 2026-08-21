"""跨市场个股对比：OKX 日线与 A 股日线必须按日历日对齐。"""

from __future__ import annotations

from datetime import datetime, timedelta

from PIL import Image

from SayuStock.utils.constant import ErroText
from SayuStock.utils.render_data import build_compare_render_data
from SayuStock.utils.market.enums import AssetClass, KlinePeriod
from SayuStock.utils.market.models import Bar, SymbolRef, KlineSeries
from SayuStock.stock_stockinfo.chart_compare import draw_compare_chart
from SayuStock.utils.market.convert.dataframe import kline_to_df


def _bars(*, start: datetime, closes: list[float], step_days: int = 1) -> tuple[Bar, ...]:
    out: list[Bar] = []
    for index, close in enumerate(closes):
        ts = start + timedelta(days=index * step_days)
        out.append(
            Bar(
                ts=ts,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1.0,
                amount=1.0,
                amplitude=0.0,
                change_pct=0.0,
                change_amount=0.0,
                turnover_rate=0.0,
            )
        )
    return tuple(out)


def _series(
    *,
    name: str,
    code: str,
    exchange: str,
    sec_type: str,
    closes: list[float],
    hour: int,
    period: KlinePeriod = KlinePeriod.D1_YEAR,
) -> KlineSeries:
    return KlineSeries(
        symbol=SymbolRef(
            code=code,
            name=name,
            asset_class=AssetClass.CRYPTO if exchange == "OKX" else AssetClass.ETF,
            exchange=exchange,
            provider_symbol=code,
            sec_type=sec_type,
        ),
        period=period,
        bars=_bars(start=datetime(2026, 1, 5, hour, 0, 0), closes=closes),
        adjusted=False,
    )


def test_daily_okx_bars_use_calendar_date_not_clock() -> None:
    series = _series(
        name="BTC-USDT",
        code="BTC-USDT",
        exchange="OKX",
        sec_type="加密货币",
        closes=[100.0, 110.0],
        hour=8,
        period=KlinePeriod.D1_YEAR,
    )
    df = kline_to_df(series)
    assert list(df["date"]) == ["2026-01-05", "2026-01-06"]


def test_intraday_kline_keeps_clock() -> None:
    series = _series(
        name="BTC-USDT",
        code="BTC-USDT",
        exchange="OKX",
        sec_type="加密货币",
        closes=[100.0, 110.0],
        hour=8,
        period=KlinePeriod.M5,
    )
    df = kline_to_df(series)
    assert list(df["date"]) == ["2026-01-05 08:00", "2026-01-06 08:00"]


def test_compare_aligns_okx_and_a_share_on_calendar_day() -> None:
    crypto = _series(
        name="BTC-USDT",
        code="BTC-USDT",
        exchange="OKX",
        sec_type="加密货币",
        closes=[100.0, 102.0, 108.0],
        hour=8,
    )
    etf = _series(
        name="沪深300ETF",
        code="510300",
        exchange="SSE",
        sec_type="ETF",
        closes=[4.0, 4.1, 4.2],
        hour=0,
    )
    data = build_compare_render_data([crypto, etf])
    assert not isinstance(data, str)
    crypto_days = {pd_ts.date() for pd_ts in data.items[0].df["日期"]}
    etf_days = {pd_ts.date() for pd_ts in data.items[1].df["日期"]}
    assert crypto_days == etf_days
    assert datetime(2026, 1, 5).date() in crypto_days


def test_compare_chart_renders_mixed_okx_and_etf() -> None:
    crypto = _series(
        name="BTC-USDT",
        code="BTC-USDT",
        exchange="OKX",
        sec_type="加密货币",
        closes=[100.0 + i for i in range(12)],
        hour=8,
    )
    etf = _series(
        name="沪深300ETF",
        code="510300",
        exchange="SSE",
        sec_type="ETF",
        closes=[4.0 + i * 0.01 for i in range(12)],
        hour=0,
    )
    fig = draw_compare_chart([crypto, etf])
    assert fig != ErroText["notData"]
    assert isinstance(fig, Image.Image)
    assert fig.size[0] > 100
