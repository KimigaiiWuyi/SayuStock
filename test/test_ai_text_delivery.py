"""「有图必有文字」回归测试（领域模型载荷）。"""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from datetime import datetime
from collections.abc import Iterator

import pytest
from kline_fixtures import make_klines

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from SayuStock.stock_stockinfo import render_mpl as rm  # noqa: E402
from SayuStock.utils.stock.utils import get_file  # noqa: E402
from SayuStock.utils.market.enums import BoardKind, AssetClass, KlinePeriod  # noqa: E402
from SayuStock.utils.market.models import (  # noqa: E402
    Bar,
    Quote,
    BoardRow,
    SymbolRef,
    KlineSeries,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
)
from SayuStock.stock_stockinfo.data import CloudMapDataResult  # noqa: E402


def _klines(n: int = 120, seed: int = 5) -> list[str]:
    return make_klines(n, seed)


def _kline_series(name: str, n: int = 120, seed: int = 5) -> KlineSeries:
    bars: list[Bar] = []
    for line in _klines(n, seed):
        parts = line.split(",")
        if len(parts) < 11:
            continue
        ts_s = parts[0]
        try:
            ts = datetime.strptime(ts_s[:10], "%Y-%m-%d")
            if len(ts_s) > 10:
                ts = datetime.strptime(ts_s[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        bars.append(
            Bar(
                ts=ts,
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]),
                amount=float(parts[6]),
                amplitude=float(parts[7]),
                change_pct=float(parts[8]),
                change_amount=float(parts[9]),
                turnover_rate=float(parts[10]),
            )
        )
    return KlineSeries(
        symbol=SymbolRef(name[:6] if name else "600000", name, AssetClass.EQUITY, "SSE", "1.600000"),
        period=KlinePeriod.D1,
        bars=tuple(bars),
        adjusted=True,
    )


def _kronos_klines() -> list[str]:
    return make_klines(80, 7)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    got: list[str] = []
    monkeypatch.setattr(rm, "ai_return", lambda t: got.append(t))
    return got


@pytest.fixture
def fake_kline_fetch(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = "AI文字投递测试股"
    series = _kline_series(name)

    async def fetch(market, sector, start_time=None, end_time=None):  # noqa: ANN001, ARG001
        return CloudMapDataResult(series, [], "single-stock-kline-101", None)

    monkeypatch.setattr(rm.CLOUDMAP_DATA_SERVICE, "fetch", fetch)

    cache = get_file(name, "png", "single-stock-kline-101", None)
    cache.unlink(missing_ok=True)
    yield name
    cache.unlink(missing_ok=True)


def test_ai_text_sent_on_cold_and_warm_cache(captured: list[str], fake_kline_fetch: str) -> None:
    name = fake_kline_fetch

    captured.clear()
    first = asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    assert not isinstance(first, str), f"第一次出图失败: {first}"
    assert captured, "冷缓存时 AI 没收到文字"
    cold_text = captured[0]

    captured.clear()
    second = asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    assert not isinstance(second, str), f"第二次出图失败: {second}"
    assert captured, "热缓存命中时 AI 没收到文字 —— 看不到图的模型将完全无输入"
    assert captured[0] == cold_text, "冷/热缓存两次发给 AI 的文字必须一致"


def test_ai_text_on_cache_hit_still_has_indicators(captured: list[str], fake_kline_fetch: str) -> None:
    name = fake_kline_fetch
    asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    captured.clear()
    asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    assert captured
    text = captured[0]
    for label in ("MA20", "BBI", "KDJ(9,3,3)", "RSI6", "MACD(12,26,9)", "BOLL(20,2)"):
        assert label in text, f"热缓存的文字缺 {label}"


def test_emit_ai_text_dispatch_matches_chart_kinds(captured: list[str]) -> None:
    kline = _kline_series("甲")
    symbol = SymbolRef("600000", "甲", AssetClass.EQUITY, "SSE", "1.600000")
    quote = Quote(
        symbol=symbol,
        price=10.0,
        open=10.0,
        high=10.5,
        low=9.5,
        prev_close=10.0,
        change_pct=0.0,
        change_amount=0.0,
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
        as_of=datetime(2026, 7, 23, 15, 0),
    )
    intraday = IntradaySeries(
        symbol=symbol,
        points=(
            IntradayPoint(
                ts=datetime(2026, 7, 23, 9, 30),
                price=10.0,
                open=10.0,
                high=10.0,
                low=10.0,
                volume=1,
                amount=10,
                avg_price=10.0,
            ),
        ),
        quote=quote,
    )
    cloud = BoardSnapshot(
        kind=BoardKind.HOTMAP,
        title="大盘云图",
        rows=(
            BoardRow(
                code="1",
                name="股A",
                price=1.0,
                change_pct=1.0,
                amount=1.0,
                market_cap=1e10,
                industry="板块",
                lead_name=None,
                lead_change_pct=None,
            ),
        ),
    )

    captured.clear()
    rm._emit_ai_text("甲", "single-stock-kline-101", kline, [])
    assert captured and "日K" in captured[0]

    captured.clear()
    rm._emit_ai_text("甲", "compare-stock", kline, [kline, kline])
    assert captured and "个股对比" in captured[0]

    captured.clear()
    rm._emit_ai_text("甲", "single-stock", intraday, [])
    assert captured and "分时" in captured[0]

    captured.clear()
    rm._emit_ai_text("大盘云图", None, cloud, [])
    assert captured and "大盘云图" in captured[0]


def test_kronos_ai_text_sent_on_cold_and_warm_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import plotly.graph_objects as go

    from SayuStock.stock_ai import draw_ai_map as dm

    series = _kline_series("预测缓存测试股", n=80, seed=7)

    class FakeMarket:
        async def kline(self, *a, **k):  # noqa: ANN001, ARG002
            return series

    async def fake_code_id(market):  # noqa: ANN001, ARG001
        return ("1.600000", "预测缓存测试股", "股票")

    async def fake_pw(fig, w, h, s):  # noqa: ANN001, ARG001
        return b"PNG"

    monkeypatch.setattr(dm, "get_market", lambda: FakeMarket())
    monkeypatch.setattr(dm, "get_code_id", fake_code_id)
    monkeypatch.setattr(dm, "get_full_security_code", lambda c: "1.600000")
    monkeypatch.setattr(dm, "gdf", lambda df, s: go.Figure(data=[go.Scatter(y=[1, 2, 3])]))
    monkeypatch.setattr(dm, "render_image_by_pw", fake_pw)

    got: list[str] = []
    monkeypatch.setattr(dm, "ai_return", lambda t: got.append(t))

    cache = get_file("1.600000", "html", "single-stock-ai", None)
    cache.unlink(missing_ok=True)

    class FakeBot:
        async def send(self, *a, **k):  # noqa: ANN001, ARG002
            return None

    try:
        got.clear()
        asyncio.run(dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot()))  # type: ignore[arg-type]
        assert got, "冷缓存时 AI 没收到文字"
        cold = got[0]

        got.clear()
        asyncio.run(dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot()))  # type: ignore[arg-type]
        assert got, "@async_file_cache 命中时 AI 没收到文字 —— 150 分钟内问第二次将完全无输入"
        assert got[0] == cold
    finally:
        cache.unlink(missing_ok=True)
