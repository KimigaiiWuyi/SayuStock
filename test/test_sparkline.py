"""sparkline SVG：面积图 + 昨收虚线，X 轴含会话占位。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.sparkline import build_sparkline_svg, sparkline_from_series
from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import Quote, SymbolRef, IntradayPoint, IntradaySeries


def test_sparkline_svg_has_area_and_baseline() -> None:
    prices: list[float | None] = [10.0, 10.2, None, 10.4, 10.1]
    svg = build_sparkline_svg(prices, 10.0, up=True, width=100, height=40)
    assert svg.startswith("<svg")
    assert "polyline" in svg
    assert "<path " in svg
    assert "stroke-dasharray" in svg
    assert "#ff5c5c" in svg


def test_sparkline_svg_down_uses_green() -> None:
    svg = build_sparkline_svg([10.0, 9.5, 9.2], 10.0, up=False, width=80, height=30)
    assert "#3ee36a" in svg


def test_sparkline_too_short_is_empty() -> None:
    assert build_sparkline_svg([1.0], 1.0, up=True) == ""
    assert build_sparkline_svg([None, None], 1.0, up=True) == ""


def _a_share_series() -> IntradaySeries:
    points = []
    for i, px in enumerate((10.0, 10.1, 10.2, 10.15)):
        minute = 31 + i
        points.append(
            IntradayPoint(
                ts=datetime(2026, 8, 24, 9, minute),
                price=px,
                open=10.0,
                high=px,
                low=10.0,
                volume=1.0,
                amount=1.0,
                avg_price=px,
            )
        )
    symbol = SymbolRef(
        code="600519",
        name="茅台",
        asset_class=AssetClass.EQUITY,
        exchange="SSE",
        provider_symbol="1.600519",
        sec_type="沪A",
    )
    quote = Quote(
        symbol=symbol,
        price=10.15,
        open=10.0,
        high=10.2,
        low=10.0,
        prev_close=10.0,
        change_pct=1.5,
        change_amount=0.15,
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
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=quote, ndays=1)


def make_crypto_series(name: str, last_price: float, change_pct: float, *, as_of: datetime) -> IntradaySeries:
    inst = f"{name}-USDT"
    open_px = last_price / (1.0 + change_pct / 100.0)
    total_min = as_of.hour * 60 + as_of.minute
    points: list[IntradayPoint] = []
    for minute in range(0, total_min + 1):
        ts = as_of.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)
        frac = minute / total_min if total_min else 1.0
        px = open_px + (last_price - open_px) * frac
        points.append(
            IntradayPoint(
                ts=ts,
                price=px,
                open=open_px,
                high=px,
                low=px,
                volume=1.0,
                amount=1.0,
                avg_price=px,
            )
        )
    last = points[-1]
    points[-1] = IntradayPoint(
        ts=last.ts,
        price=last_price,
        open=open_px,
        high=last_price,
        low=last_price,
        volume=1.0,
        amount=1.0,
        avg_price=last_price,
    )
    symbol = SymbolRef(
        code=inst,
        name=name,
        asset_class=AssetClass.CRYPTO,
        exchange="OKX",
        provider_symbol=inst,
    )
    quote = Quote(
        symbol=symbol,
        price=last_price,
        open=open_px,
        high=last_price,
        low=open_px,
        prev_close=open_px,
        change_pct=change_pct,
        change_amount=last_price - open_px,
        volume=1.0,
        amount=1.0,
        turnover_rate=0.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry="crypto",
        limit_up=None,
        limit_down=None,
        as_of=as_of,
    )
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=quote, ndays=1)


def test_sparkline_from_series_uses_session_axis() -> None:
    svg = sparkline_from_series(_a_share_series(), width=160, height=36)
    assert "polyline" in svg
    assert "viewBox" in svg


def test_sparkline_from_crypto_uses_okx_day() -> None:
    svg = sparkline_from_series(
        make_crypto_series("BTC", 76617.0, -0.49, as_of=datetime(2026, 9, 5, 17, 2)),
        width=168,
        height=72,
    )
    assert "polyline" in svg
    assert "viewBox" in svg
