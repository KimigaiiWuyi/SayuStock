"""Typed offline chart fixtures and native-render gallery."""

import os
import struct
import asyncio
from io import BytesIO
from math import cos, sin
from base64 import b64decode
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import replace

import pytest
from PIL import Image
from test_intraday_ndays import _quote
from test_ai_text_delivery import _kline_series

from SayuStock.utils import indicators as ind
from SayuStock.render import RenderError
from SayuStock.utils.stock import render_cache
from SayuStock.render.kline import prepare_kline
from SayuStock.render.compare import prepare_compare
from SayuStock.render.treemap import render_treemap
from SayuStock.stock_cloudmap import render as cloudmap
from SayuStock.render.intraday import prepare_multi_intraday, prepare_single_intraday
from SayuStock.utils.time_range import get_trading_datetimes_bjt
from SayuStock.render.chart_page import ChartPage, page_html, render_chart
from SayuStock.utils.render_data import build_kline_render_data
from SayuStock.utils.market.enums import BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.models import BoardRow, SymbolRef, BoardSnapshot, IntradayPoint, IntradaySeries
from SayuStock.stock_stockinfo.data import CloudMapDataResult


def _series(code: str, name: str, ndays: int, amplitude: float, *, phase: float = 0) -> IntradaySeries:
    symbol = SymbolRef(code, name, AssetClass.EQUITY, "SSE", f"1.{code}")
    first = datetime(2026, 8, 24, 12)
    points: list[IntradayPoint] = []
    dates = [first + timedelta(days=i) for i in range(5)][-ndays:]
    for day_index, stamp in enumerate(dates):
        times = get_trading_datetimes_bjt(symbol.provider_symbol, now_bjt=stamp)
        count = 164 if day_index == len(dates) - 1 else len(times)
        for i, now in enumerate(times[:count]):
            if 72 <= i <= 77:
                continue
            drift = i / 90 + day_index * 0.7
            bump = sin(i / 28 + phase) + 0.19 * sin(i / 3.2)
            price = 25.0 + amplitude * (drift + bump - 1)
            amount = 3.0e6 + 4.0e6 * (1 + cos(i / 14)) * (1 + 0.12 * sin(i * 1.7))
            points.append(IntradayPoint(now, price, price, price, price, amount / price, amount, price - 0.2))
    before_last_day = [point.price for point in points if point.ts.date() < points[-1].ts.date()]
    previous_close = before_last_day[-1] if before_last_day else 25.0
    quote = replace(
        _quote(symbol, points[-1].price),
        prev_close=previous_close,
        change_pct=(points[-1].price / previous_close - 1) * 100,
        amount=sum(point.amount for point in points if point.ts.date() == points[-1].ts.date()),
        turnover_rate=2.28,
        provider="eastmoney",
    )
    return IntradaySeries(symbol, tuple(points), quote, ndays=ndays, ref_close=25.0, provider="eastmoney")


def _board(kind: BoardKind = BoardKind.HOTMAP) -> BoardSnapshot:
    industries = ("半导体", "软件服务", "电池", "汽车零部件", "证券", "食品饮料", "医药生物", "电力", "有色金属")
    names = (
        "远山科技",
        "星海微电子",
        "三江新能",
        "启辰精工",
        "山海资本",
        "四季食品",
        "北辰生物",
        "蓝湾电力",
        "云岫资源",
    )
    rows = []
    for i, industry in enumerate(industries):
        for j in range(8):
            cap = 2e11 / (1 + i * 0.23) / (1 + j * 0.57)
            move = 9.8 * sin(i + j * 2.2)
            name = names[i] if j == 0 else f"{names[i][:2]}{'甲乙丙丁戊己庚辛'[j]}"
            rows.append(BoardRow(f"{600000 + i * 10 + j}", name, 20, move, 1e8, cap, industry, None, None))
    return BoardSnapshot(kind, "离线样例", tuple(rows), "eastmoney")


def _kline(period: KlinePeriod = KlinePeriod.D1) -> ChartPage:
    series = replace(_kline_series("星海微电子", n=134), period=period, provider="eastmoney")
    chart = prepare_kline(series)
    assert not isinstance(chart, str), chart
    return chart


def test_kline_indicators_and_period_match_upstream() -> None:
    series = replace(_kline_series("星海微电子", n=134), period=KlinePeriod.Q1)
    data = build_kline_render_data(series)
    assert not isinstance(data, str)
    chart = prepare_kline(series)
    assert not isinstance(chart, str)
    assert "季K线" in chart.title
    close = data.chart_df["close"]
    assert len(close) == 134
    main, volume, turnover, rsi, kdj, macd = chart.panels
    main_legend = {entry.label: entry.value for entry in main.legend}
    for period in (5, 10, 20, 60):
        assert main_legend[f"MA{period}"] == f"{ind.ma(close, period).iloc[-1]:.2f}"
    assert main_legend["BBI"] == f"{ind.bbi(close).iloc[-1]:.2f}"
    assert set(main_legend) >= {"BOLL60 · 2σ", "BOLL60 · 3σ"}
    assert len(main.fills) == 2 and volume.rects
    assert turnover.right_axis and {item.label for item in turnover.legend} == {"换手率", "CMF20"}
    assert [entry.label for entry in rsi.legend] == ["RSI6", "RSI12", "RSI24"]
    assert [entry.label for entry in kdj.legend] == ["K", "D", "J"]
    dif, dea, hist = ind.macd(close)
    assert [entry.value for entry in macd.legend] == [f"{item.iloc[-1]:.2f}" for item in (dif, dea, hist)]


def test_five_day_multi_chart_shows_trading_days_today_and_cumulative() -> None:
    a, b = _series("600101", "远山科技", 5, 0.55), _series("600102", "星海微电子", 5, -0.65)
    chart = prepare_multi_intraday([a, b])
    assert not isinstance(chart, str)
    price, money = chart.panels
    assert len(price.series) == 2
    assert len(money.x_ticks) == 5
    assert money.x_ticks[0].label == "08-24"
    assert money.x_ticks[-1].label == "08-28"
    assert all("今日" in legend.detail and "基准" in legend.detail for legend in chart.legend)
    assert len(price.rules) >= 5 and len(money.rules) == 4
    assert all(stroke.path.count("M") >= 5 for stroke in price.series)


def _fixture_png() -> bytes:
    image = BytesIO()
    Image.new("RGB", (12, 8), "#0b0e13").save(image, format="PNG")
    return image.getvalue()


@pytest.mark.asyncio
async def test_render_cache_single_writer_recovery_and_legacy_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_cache, "DATA_PATH", tmp_path)
    path = render_cache.get_render_file("例股", "png", "compare-stock", "20260101-", namespace="takumi-stock-v1")
    assert path != render_cache.get_render_file(
        "例股", "png", "compare-stock", "20260102-", namespace="takumi-stock-v1"
    )
    legacy = tmp_path / "例股_compare-stock_20260101-_data.png"
    legacy.write_bytes(b"old backend")
    path.write_bytes(b"truncated")
    count = 0

    async def fail() -> str:
        return "绘图失败"

    assert await render_cache.cached_render(path, 3, fail) == "绘图失败"
    assert path.read_bytes() == b"truncated"

    async def draw() -> bytes:
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return _fixture_png()

    assert await asyncio.gather(*(render_cache.cached_render(path, 3, draw) for _ in range(3))) == [path] * 3
    assert count == 1
    assert path.read_bytes() == _fixture_png()
    assert legacy.read_bytes() == b"old backend"
    assert not list(tmp_path.glob(".render-*"))


@pytest.mark.asyncio
async def test_render_cache_bounds_same_key_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "pending.png"
    started = asyncio.Event()
    release = asyncio.Event()

    async def draw() -> bytes:
        started.set()
        await release.wait()
        return _fixture_png()

    owner = asyncio.create_task(render_cache.cached_render(path, 3, draw, includes_inference=True))
    await started.wait()
    monkeypatch.setattr(render_cache, "_CACHE_TIMEOUT", 0.03)
    try:
        assert "超时" in await render_cache.cached_render(path, 3, draw)
    finally:
        release.set()
        assert await owner == path


@pytest.mark.asyncio
async def test_main_map_cold_warm_ai_and_command_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    board = _board()
    captured: list[str] = []
    renders: list[int] = []

    async def fetch(*_: object) -> CloudMapDataResult:
        return CloudMapDataResult(board, [], None, None)

    async def render(*_: object, **__: object) -> bytes:
        renders.append(1)
        return _fixture_png()

    async def broken(*_: object, **__: object) -> bytes:
        raise RenderError("synthetic failure")

    monkeypatch.setattr(render_cache, "DATA_PATH", tmp_path)
    monkeypatch.setattr(cloudmap.CLOUDMAP_DATA_SERVICE, "fetch", fetch)
    monkeypatch.setattr(cloudmap, "render_treemap", broken)
    monkeypatch.setattr(cloudmap, "ai_return", captured.append)
    assert "云图生成失败" in await cloudmap.render_image("大盘云图")
    assert not list(tmp_path.glob("*.png"))
    monkeypatch.setattr(cloudmap, "render_treemap", render)
    for _ in range(2):
        content = await cloudmap.render_image("大盘云图")
        assert isinstance(content, str) and content.startswith("base64://")
        assert b64decode(content.removeprefix("base64://")) == _fixture_png()
    assert renders == [1]
    assert len(captured) == 3 and all(text == captured[0] for text in captured)
    html = await cloudmap.render_html("大盘云图")
    assert isinstance(html, Path) and "<svg" in html.read_text(encoding="utf-8")


def _required() -> None:
    if "SAYUSTOCK_RENDER_RUNTIME" not in os.environ and os.environ.get("SAYUSTOCK_REQUIRE_RENDER_RUNTIME") != "1":
        pytest.skip("Install Node/Takumi and set SAYUSTOCK_RENDER_RUNTIME to render the gallery")


def _save_png(name: str, result: str | bytes) -> None:
    assert isinstance(result, bytes), result
    assert result.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", result[16:24])
    assert width >= 1200 and height >= 500
    if "SAYUSTOCK_RENDER_EVIDENCE_DIR" in os.environ:
        target = Path(os.environ["SAYUSTOCK_RENDER_EVIDENCE_DIR"]) / "charts"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.png").write_bytes(result)


@pytest.mark.asyncio
async def test_chart_gallery_uses_native_worker() -> None:
    _required()
    single = _series("600101", "远山科技", 5, 0.62)
    multi = _series("600102", "星海微电子", 5, -0.55, phase=1.3)
    compared = prepare_compare(
        [
            replace(_kline_series("沪深300ETF", n=134, seed=4), provider="eastmoney"),
            replace(_kline_series("BTC-USDT", n=134, seed=11), provider="okx"),
            replace(_kline_series("成长混合基金", n=121, seed=14), provider="tiantian"),
        ]
    )
    charts = {
        "01-intraday": prepare_single_intraday(replace(single, points=single.points[-158:], ndays=1)),
        "02-intraday-five-day": prepare_single_intraday(single),
        "03-multi-intraday": prepare_multi_intraday(
            [replace(s, points=s.points[-158:], ndays=1) for s in (single, multi)]
        ),
        "04-multi-five-day": prepare_multi_intraday([single, multi]),
        "05-kline": _kline(),
        "06-compare": compared,
    }
    for name, page in charts.items():
        assert not isinstance(page, str), page
        assert "数据来源" in page_html(page)
        _save_png(name, await render_chart(page))
    for name, kind, market, sector, layer in (
        ("07-market-cloudmap", BoardKind.HOTMAP, "大盘云图", None, 2),
        ("08-industry-cloudmap", BoardKind.INDUSTRY, "行业云图", "半导体", 1),
        ("09-concept-cloudmap", BoardKind.CONCEPT, "概念云图", "先进制造", 1),
    ):
        snap = _board(kind)
        if kind == BoardKind.INDUSTRY:
            snap = replace(snap, rows=snap.rows[:8])
        elif kind == BoardKind.CONCEPT:
            snap = replace(snap, rows=snap.rows[:20])
        width, scale = (2500, 2.0) if kind == BoardKind.HOTMAP else (1300, 1.25)
        _save_png(name, await render_treemap(snap, market, sector, layer, width=width, scale=scale))
