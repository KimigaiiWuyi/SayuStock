"""Core HTML 渲染委托、缓存单写者保护、实图画廊与失败警告页面核心测试。"""

from __future__ import annotations

import os
import sys
import struct
import asyncio
import importlib
from io import BytesIO
from math import cos, sin
from types import ModuleType, SimpleNamespace
from base64 import b64decode
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from SayuStock import render
from gsuid_core.utils import html_render as core_render
from SayuStock.render import RenderError, render_html
from SayuStock.stock_ai import draw_ai_map as dm
from SayuStock.utils.stock import render_cache
from SayuStock.render.kline import prepare_kline
from SayuStock.render.compare import prepare_compare
from SayuStock.render.treemap import treemap_html, render_treemap, prepare_treemap
from SayuStock.stock_cloudmap import render as cloudmap
from SayuStock.utils.warn_box import Failure
from SayuStock.render.intraday import prepare_multi_intraday, prepare_single_intraday
from SayuStock.utils.sparkline import build_sparkline_svg
from SayuStock.render.templates import _ENV
from SayuStock.render.valuation import prepare_valuation
from SayuStock.utils.time_range import get_trading_datetimes_bjt
from SayuStock.render.chart_page import ChartPage, page_html, render_chart
from SayuStock.utils.market.enums import BoardKind, ValueKind, AssetClass, KlinePeriod
from SayuStock.utils.market.errors import MarketError, not_found
from SayuStock.utils.market.models import (
    BREADTH_BANDS,
    Bar,
    Quote,
    BoardRow,
    SymbolRef,
    KlineSeries,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
    breadth_up_down,
)
from SayuStock.utils.my_stock_html import WATCH_INDEXES, watchlist_width, build_my_stock_html
from SayuStock.stock_info.draw_info import _flow_image_uri
from SayuStock.stock_stockinfo.data import CloudMapDataResult
from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.all_weather_html import CSS_WIDTH as WEATHER_WIDTH, build_all_weather_html
from SayuStock.stock_ai.forecast_chart import draw_forecast_chart
from SayuStock.utils.fund_holdings_html import PAGE_W as FUND_WIDTH, FundHoldingRow, build_fund_holdings_html
from SayuStock.utils.paper_holdings_html import (
    PAPER_HOLDINGS_WIDTH,
    HoldingBarRow,
    build_paper_holdings_html,
)
from SayuStock.utils.market_overview_html import CSS_WIDTH as OVERVIEW_WIDTH, build_market_overview_html
from SayuStock.utils.market.adapters.eastmoney.parse_value import parse_value_series_payload


def _quote(code: str, name: str, price: float, change: float, move: float | None, market: str = "沪A") -> Quote:
    return Quote(
        symbol=SymbolRef(code, name, AssetClass.EQUITY, "SSE", f"1.{code}", market),
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=1650.0 if price > 50 else 25.0,
        change_pct=change,
        change_amount=move,
        volume=10_000.0,
        amount=3.2e8,
        turnover_rate=1.23,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=datetime(2026, 8, 24, 10, 42),
        provider="tencent",
    )


def _intraday_series(code: str, name: str, ndays: int, amplitude: float, *, phase: float = 0.0) -> IntradaySeries:
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
            price = 25.0 + amplitude * (i / 90 + day_index * 0.7 + sin(i / 28 + phase) + 0.19 * sin(i / 3.2) - 1)
            amount = 3.0e6 + 4.0e6 * (1 + cos(i / 14)) * (1 + 0.12 * sin(i * 1.7))
            points.append(IntradayPoint(now, price, price, price, price, amount / price, amount, price - 0.2))
    before_last = [p.price for p in points if p.ts.date() < points[-1].ts.date()]
    prev_close = before_last[-1] if before_last else 25.0
    q = Quote(
        symbol=symbol,
        price=points[-1].price,
        open=prev_close,
        high=points[-1].price,
        low=points[-1].price,
        prev_close=prev_close,
        change_pct=(points[-1].price / prev_close - 1) * 100,
        change_amount=None,
        volume=1.0,
        amount=sum(p.amount for p in points if p.ts.date() == points[-1].ts.date()),
        turnover_rate=2.28,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=None,
        provider="eastmoney",
    )
    return IntradaySeries(symbol, tuple(points), q, ndays=ndays, ref_close=25.0, provider="eastmoney")


def _kline_series(name: str, *, n: int = 120, seed: int = 1) -> KlineSeries:
    sym = SymbolRef(f"60{seed:04d}", name, AssetClass.EQUITY, "SSE", f"1.60{seed:04d}")
    start = datetime(2025, 9, 1)
    bars = tuple(
        Bar(
            ts=start + timedelta(days=i),
            open=20.0 + i * 0.1 + seed,
            high=21.0 + i * 0.1 + seed,
            low=19.0 + i * 0.1 + seed,
            close=20.5 + i * 0.1 + seed + sin(i / 4 + seed),
            volume=1e6,
            amount=2e7,
            amplitude=2.0,
            change_pct=0.5,
            change_amount=0.1,
            turnover_rate=1.0,
        )
        for i in range(n)
    )
    return KlineSeries(symbol=sym, period=KlinePeriod.D1, bars=bars, adjusted=True, provider="eastmoney")


def _board(kind: BoardKind = BoardKind.HOTMAP) -> BoardSnapshot:
    industries = ("半导体", "软件服务", "电池", "汽车零部件", "证券", "食品饮料", "医药生物", "电力", "有色金属")
    rows = [
        BoardRow(
            f"{600000 + i * 10 + j}",
            f"{ind_name}{j}",
            20.0,
            9.8 * sin(i + j * 2.2),
            1e8,
            2e11 / (1 + i * 0.23) / (1 + j * 0.57),
            ind_name,
            None,
            None,
        )
        for i, ind_name in enumerate(industries)
        for j in range(8)
    ]
    return BoardSnapshot(kind, "离线样例", tuple(rows), "eastmoney")


def _fixture_png() -> bytes:
    image = BytesIO()
    Image.new("RGB", (12, 8), "#0b0e13").save(image, format="PNG")
    return image.getvalue()


def _forecast_sample(*, tokenizer: str = "", model: str = "", device: str = "") -> ChartPage:
    start = datetime(2025, 9, 1)
    times = [start + timedelta(days=i) for i in range(151)]
    hist_y = [42.0 + i * 0.08 + sin(i / 12) * 2 for i in range(121)]
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
        backtest_min=[v - 0.35 - 0.02 * i for i, v in enumerate(backtest_mean)],
        backtest_max=[v + 0.35 + 0.02 * i for i, v in enumerate(backtest_mean)],
        future_t=times[121:],
        future_mean=future_mean,
        future_min=[v - 0.4 - 0.10 * i for i, v in enumerate(future_mean)],
        future_max=[v + 0.4 + 0.10 * i for i, v in enumerate(future_mean)],
        last_close=hist_y[-1],
        backtest_start=times[93],
        future_start=times[120],
    )
    assert not isinstance(chart, str), chart
    return chart


def _save_evidence(subdir: str | None, name: str, payload: str | bytes, *, min_w: int = 1200, min_h: int = 500) -> None:
    assert isinstance(payload, bytes), payload
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", payload[16:24])
    assert width >= min_w and height >= min_h
    if "SAYUSTOCK_RENDER_EVIDENCE_DIR" in os.environ:
        base = Path(os.environ["SAYUSTOCK_RENDER_EVIDENCE_DIR"])
        target = base / subdir if subdir else base
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.png").write_bytes(payload)


def test_templates_and_render_html_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jinja 自动转义/缺参校验，以及 render_html 参数委托与错误/取消透传。"""
    from jinja2 import UndefinedError

    tpl = _ENV.from_string("<p>{{ title }}</p>")
    assert tpl.render(title="<script>x</script>") == "<p>&lt;script&gt;x&lt;/script&gt;</p>"
    with pytest.raises(UndefinedError):
        tpl.render()

    async def _run() -> None:
        async def core_ok(
            html: str,
            *,
            max_width: float,
            dpi: float,
            root_max_width: float,
            allow_refit: bool,
            default_font_size: float,
            image_format: str,
            jpeg_quality: int,
            lang: str,
        ) -> bytes:
            assert html == "<div>早柚</div>"
            assert (max_width, dpi, root_max_width) == (478.5, 144, 319)
            assert allow_refit and default_font_size == 0
            assert (image_format, jpeg_quality, lang) == ("jpeg", 82, "zh-CN")
            return b"core-rendered-bytes"

        monkeypatch.setattr(core_render, "render_html_to_bytes", core_ok)
        assert await render.render_html("<div>早柚</div>", width=319, image_format="jpeg", jpeg_quality=82) == (
            b"core-rendered-bytes"
        )

        failure = RuntimeError("Core renderer unavailable")

        async def core_fail(_html: str, **_opts: object) -> bytes:
            raise failure

        monkeypatch.setattr(core_render, "render_html_to_bytes", core_fail)
        with pytest.raises(RenderError, match="自选生成失败：Core renderer unavailable") as caught:
            await render.render_html("<div>test</div>", width=320, label="自选")
        assert caught.value.__cause__ is failure

        entered = asyncio.Event()
        exited = asyncio.Event()

        async def core_hang(_html: str, **_opts: object) -> bytes:
            entered.set()
            try:
                await asyncio.Future[None]()
            finally:
                exited.set()
            return b""

        monkeypatch.setattr(core_render, "render_html_to_bytes", core_hang)
        task = asyncio.create_task(render.render_html("<div>test</div>", width=320))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()

    asyncio.run(_run())


@pytest.mark.usefixtures("require_render_runtime")
def test_core_shared_renderer_and_png_jpeg_cjk_svg(monkeypatch: pytest.MonkeyPatch) -> None:
    """复用 Core 共享渲染器实例，真实输出 PNG/JPEG 且 CJK 与 SVG 着色正确。"""

    async def _run() -> None:
        html = """<!doctype html><html><head><style>
        html,body{margin:0}.card{display:flex;flex-direction:column;width:320px;background:#10151d;
        color:#e6edf3;font-family:MiSans;font-size:24px;padding:20px;box-sizing:border-box}
        </style></head><body><div class="card">
        <div>早柚股票 +0.002</div><svg width="280" height="60"><path d="M0,50 L100,30 L280,10"
        fill="none" stroke="#f6465d" stroke-width="3"/></svg></div></body></html>"""
        for image_format, pil_fmt in (("png", "PNG"), ("jpeg", "JPEG")):
            payload = await render.render_html(html, width=320, image_format=image_format, jpeg_quality=88)
            with Image.open(BytesIO(payload)) as image:
                assert image.format == pil_fmt and image.width == 480 and image.height >= 150
                rgb = image.convert("RGB")
            px = rgb.getpixel((5, 5))
            assert isinstance(px, tuple) and all(c < 40 for c in px)
            if image_format == "png":
                colors = rgb.getcolors(rgb.width * rgb.height)
                assert colors is not None and any(color == (246, 70, 93) for _, color in colors)

        seen: list[object] = []

        def fake_pic(_html: str, *, renderer: object, **_opts: object) -> bytes:
            seen.append(renderer)
            return b"shared"

        monkeypatch.setattr(core_render, "html_to_pic", fake_pic)
        await core_render.render_html_to_bytes("<p>Core</p>", max_width=320)
        await render.render_html("<p>A</p>", width=320)
        await render.render_html("<p>B</p>", width=320)
        assert seen[0] is not None and seen[0] is seen[1] is seen[2]

    asyncio.run(_run())


def test_render_cache_single_writer_and_cloudmap_ai_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存单写者互斥、残片恢复、旧版隔离以及云图冷热缓存先发 ai_return。"""
    monkeypatch.setattr(render_cache, "DATA_PATH", tmp_path)

    async def _run() -> None:
        path = render_cache.get_render_file("例股", "png", "compare-stock", "20260101-", namespace="takumi-stock-v1")
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
        assert count == 1 and path.read_bytes() == _fixture_png() and legacy.read_bytes() == b"old backend"

        board = _board()
        captured: list[str] = []
        renders: list[int] = []

        async def fetch(*_args: object) -> CloudMapDataResult:
            return CloudMapDataResult(board, [], None, None)

        async def fake_treemap(*_args: object, **_kwargs: object) -> bytes:
            renders.append(1)
            return _fixture_png()

        monkeypatch.setattr(cloudmap.CLOUDMAP_DATA_SERVICE, "fetch", fetch)
        monkeypatch.setattr(cloudmap, "render_treemap", fake_treemap)
        monkeypatch.setattr(cloudmap, "ai_return", captured.append)
        for _ in range(2):
            res = await cloudmap.render_image("大盘云图")
            assert isinstance(res, str) and res.startswith("base64://")
            assert b64decode(res.removeprefix("base64://")) == _fixture_png()
        assert renders == [1] and len(captured) == 2 and captured[0] == captured[1]

    asyncio.run(_run())


@pytest.mark.usefixtures("require_render_runtime")
def test_chart_and_card_gallery_render_with_evidence() -> None:
    """分时/五日/K线/对比/云图/估值/Kronos/四类卡片 HTML 与真实 PNG 出图并写证据目录。"""

    async def _run() -> None:
        single = _intraday_series("600101", "远山科技", 5, 0.62)
        multi = _intraday_series("600102", "星海微电子", 5, -0.55, phase=1.3)
        compared = prepare_compare(
            [
                replace(_kline_series("沪深300ETF", n=134, seed=4), provider="eastmoney"),
                replace(_kline_series("BTC-USDT", n=134, seed=11), provider="okx"),
            ]
        )
        charts = {
            "01-intraday": prepare_single_intraday(replace(single, points=single.points[-158:], ndays=1)),
            "02-intraday-five-day": prepare_single_intraday(single),
            "04-multi-five-day": prepare_multi_intraday([single, multi]),
            "05-kline": prepare_kline(_kline_series("星海微电子", n=134)),
            "06-compare": compared,
        }
        for name, page in charts.items():
            assert not isinstance(page, str), page
            assert "数据来源" in page_html(page)
            payload = await render_chart(page)
            _save_evidence("charts", name, payload)
            if name == "05-kline":
                assert isinstance(payload, bytes)
                _, img_h = struct.unpack(">II", payload[16:24])
                assert page.panels[0].height > sum(p.height for p in page.panels[1:])
                assert page.panels[0].height * 1.5 / img_h >= 0.5

        snap = _board(BoardKind.INDUSTRY)
        dense_snap = BoardSnapshot(
            BoardKind.INDUSTRY,
            "半导体",
            tuple(
                BoardRow(
                    f"688{i:03d}",
                    f"测试芯片{i}",
                    20.0,
                    (i % 19) - 9.0,
                    1e8,
                    2e11 / (1 + i * 0.25),
                    "半导体",
                    None,
                    None,
                )
                for i in range(150)
            ),
            "eastmoney",
        )
        dense_page = prepare_treemap(dense_snap, "行业云图", "半导体", 1, width=2500)
        assert not isinstance(dense_page, str)
        assert sum(tile.show_text for tile in dense_page.tiles) >= 140
        html_dense = treemap_html(dense_page)
        assert "font-weight: 550" in html_dense and "font-weight: 380" in html_dense
        _save_evidence(
            "charts",
            "08-industry-cloudmap",
            await render_treemap(replace(snap, rows=snap.rows[:8]), "行业云图", "半导体", 1, width=1300, scale=1.25),
        )

        event = {
            "ex_date": "2026-01-05",
            "report_date": "2025-06-30",
            "bonus_per_share": 0.32,
            "contribution_pct": 1.26,
        }
        val = parse_value_series_payload(
            {
                "code": "600101",
                "name": "远山科技",
                "rows": [
                    {"date": "2026-01-05", "value": 2.8, "events": [event]},
                    {"date": "2026-01-07", "value": None},
                    {"date": "2026-01-08", "value": 2.6},
                ],
            },
            kind=ValueKind.DY,
        )
        assert not isinstance(val, MarketError)
        val_page = prepare_valuation([val], ValueKind.DY, "股息率", failures=["测试缺源股"])
        assert not isinstance(val_page, str) and "每股 0.32元 · 贡献 +1.26%" in page_html(val_page)
        _save_evidence("valuation", "dy", await render_chart(val_page))

        kronos_page = _forecast_sample(
            tokenizer="NeoQuasar/Kronos-Tokenizer-base", model="NeoQuasar/Kronos-mini", device="cpu"
        )
        assert "回测开始" in page_html(kronos_page) and "预测开始" in page_html(kronos_page)
        _save_evidence("valuation", "kronos", await render_chart(kronos_page), min_h=500)

        q1 = _quote("588170", "科创半导体ETF华夏", 0.652, 0.31, 0.002)
        generic_spark = build_sparkline_svg([100 + sin(i / 3) + i * 0.04 for i in range(45)], 100.0, up=True)
        idx_quotes = [_quote(code, name, 3930.0, 0.5, None, "指数") for name, code in WATCH_INDEXES]
        watch_html = build_my_stock_html(
            quotes=[(q1, "588170"), (replace(q1, change_amount=None, prev_close=0.654), "512880")],
            index_quotes=idx_quotes,
            saved_count=3,
            sparklines={"588170": generic_spark},
            failures=[Failure("000858", "东方财富网络异常")],
        )
        assert ">+0.002<" in watch_html and ">-0.002<" in watch_html
        assert watchlist_width(18) < watchlist_width(26)
        fund_html = build_fund_holdings_html(
            fund_name="沪深优选成长混合",
            fund_code="012345",
            index_items=[DisplayItem(n, 3930.0, 0.5) for n, _ in WATCH_INDEXES],
            rows=[
                FundHoldingRow("588170", "科创半导体ETF华夏", "9.82", 0.652, 0.31, provider="tencent"),
                FundHoldingRow("000858", "五粮液", "6.20", failed=True, reason="东方财富网络异常"),
            ],
            meta="持仓 2 只",
            average=0.31,
            report_period="报告期 2026-09-30",
            sparklines={"588170": generic_spark},
        )
        flow_img = Image.new("RGB", (500, 274), "white")
        ImageDraw.Draw(flow_img).line((16, 120, 488, 90), fill=(37, 155, 179), width=2)
        diff_bar = dict(zip(BREADTH_BANDS, (42, 170, 230, 610, 923, 704, 95, 645, 280, 175, 100, 45, 11), strict=True))
        up, down = breadth_up_down(diff_bar)
        idx_items = [DisplayItem(n, 3930.0, 0.5) for n, _ in WATCH_INDEXES]
        rise = [DisplayItem("半导体", 0.0, 3.2, lead_name="样例", lead_change_pct=8.5)] * 5
        fall = [DisplayItem("煤炭", 0.0, -2.1, fall_name="样例", fall_change_pct=-5.0)] * 5
        overview_html = build_market_overview_html(
            data_zs_items=idx_items,
            sparks={item.name: generic_spark for item in idx_items},
            diff_bar=diff_bar,
            up_value=up,
            down_value=down,
            breadth_available=True,
            em_flow_uri=_flow_image_uri(flow_img),
            now=datetime(2026, 8, 24, 10, 42),
            trade_date=None,
            turnover="1.42万亿",
            turnover_delta="放量 840亿",
            turnover_tone="up",
            data_hy_fall=fall,
            data_hy_rise=rise,
            data_gn_fall=fall,
            data_gn_rise=rise,
            source_line="数据来源：东方财富 | SayuStock",
            title_num="5",
        )
        weather_html = build_all_weather_html(
            [("国际市场", idx_items), ("加密货币", [DisplayItem("BTC", 85013.0, 1.87)])],
            now=datetime(2026, 8, 24, 10, 42),
            sparklines={"上证指数": generic_spark, "BTC": generic_spark},
        )
        paper_index = [
            DisplayItem("上证指数", 3930.12, -0.30, provider="tencent"),
            DisplayItem("深证成指", 11291.05, 0.41, provider="tencent"),
            DisplayItem("中证A500", 4857.27, 0.00, provider="tencent"),
            DisplayItem("中证2000", 2846.32, 0.35, provider="tencent"),
        ]
        paper_rows = [
            HoldingBarRow(
                "600001",
                "示例科技",
                2000,
                82.25,
                84.36,
                1.25,
                4220.0,
                12.5653,
                168720.0,
                "tencent",
                spark_provider="eastmoney",
            ),
            HoldingBarRow(
                "000002",
                "示例电气",
                1800,
                61.83,
                58.04,
                None,
                -6822.0,
                -6.129,
                104472.0,
                quote_error="东方财富网络异常",
                price_basis="上次报价",
            ),
            HoldingBarRow(
                "512880",
                "示例证券ETF",
                85000,
                0.987,
                0.987,
                None,
                0.0,
                0.0,
                83895.0,
                quote_error="数据源未返回今日涨跌幅",
                price_basis="成本估值",
            ),
        ]
        paper_failures = [
            Failure("示例电气(000002)", "东方财富网络异常"),
            Failure("示例证券ETF(512880)", "数据源未返回今日涨跌幅"),
        ]
        paper_html = build_paper_holdings_html(
            account_name="示例多因子盘",
            strategy_id="multi_factor",
            enabled=True,
            cash=638_100.0,
            initial_cash=1_000_000.0,
            holdings=paper_rows,
            index_items=paper_index,
            sparklines={"600001": generic_spark},
            failures=paper_failures,
            warn_title="2/3 只持仓今日行情获取失败",
        )
        paper_empty_html = build_paper_holdings_html(
            account_name="示例空仓盘",
            strategy_id="trend",
            enabled=False,
            cash=1_000_000.0,
            initial_cash=1_000_000.0,
            holdings=[],
            index_items=paper_index,
        )
        assert 'class="mood"' in paper_html and 'class="mood"' in paper_empty_html
        for subdir, label, width, html in (
            (None, "watchlist-3", watchlist_width(3), watch_html),
            (None, "fund", FUND_WIDTH, fund_html),
            (None, "overview", OVERVIEW_WIDTH, overview_html),
            (None, "all-weather", WEATHER_WIDTH, weather_html),
            ("extras", "paper-holdings", PAPER_HOLDINGS_WIDTH, paper_html),
            ("extras", "paper-holdings-empty", PAPER_HOLDINGS_WIDTH, paper_empty_html),
        ):
            png = await render_html(html, width=width, label=label)
            _save_evidence(subdir, label, png, min_w=round(width * 1.5), min_h=300)

    asyncio.run(_run())


def test_watchlist_and_fund_partial_or_full_failure_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """自选与基金持仓部分/全失败不得静默少画，必须出警告 HTML 与 AI 文字。"""
    ai_texts: list[str] = []
    htmls: list[str] = []

    class _FakeMarket:
        def __init__(self, ok: set[str]) -> None:
            self._ok = ok

        async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
            _ = ndays
            if query not in self._ok:
                return not_found("标的不存在", provider="eastmoney")
            q = _quote(query, f"名称{query}", 1680.0, 1.2, 20.0)
            return IntradaySeries(
                q.symbol,
                (IntradayPoint(datetime(2026, 10, 20, 9, 30), 1680.0, 1680.0, 1680.0, 1680.0, 1.0, 1.0, 1680.0),),
                q,
                provider="eastmoney",
            )

        async def quote(self, query: str) -> Quote | MarketError:
            if query not in self._ok:
                return not_found("标的不存在", provider="eastmoney")
            return _quote(query, f"名称{query}", 1680.0, 1.2, 20.0)

    async def fake_render(html: str, **_kwargs: object) -> bytes:
        htmls.append(html)
        return b"\x89PNG"

    df: ModuleType = importlib.import_module("SayuStock.stock_info.draw_fund_info")
    dmi: ModuleType = importlib.import_module("SayuStock.stock_info.draw_my_info")
    for mod in (df, dmi):
        monkeypatch.setattr(mod, "ai_return", ai_texts.append)
        monkeypatch.setattr(mod, "render_html", fake_render)

    async def _no_index(_names: list[str]) -> list[object]:
        return []

    async def _no_watch(_m: object) -> list[Quote | None]:
        return [None] * 4

    async def _fake_code(_c: str) -> tuple[str, str, str]:
        return ("1.000005", "易方达蓝筹精选", "基金")

    async def _fake_pos(_c: str) -> dict[str, object]:
        return {
            "Expansion": "2026-09-30",
            "Datas": [
                {"ShareCode": "600519", "ShareName": "贵州茅台", "ShareProportion": "8.50"},
                {"ShareCode": "300750", "ShareName": "宁德时代", "ShareProportion": "6.20"},
            ],
        }

    async def _fake_uids(_u: str, _b: str, game_name: str | None = None) -> list[str]:
        _ = game_name
        return ["1.600519", "0.300750"]

    monkeypatch.setattr(df, "quote_index_items", _no_index)
    monkeypatch.setattr(df, "get_code_id", _fake_code)
    monkeypatch.setattr(df, "get_fund_pos_list", _fake_pos)
    monkeypatch.setattr(df, "get_market", lambda: _FakeMarket({"600519"}))
    assert asyncio.run(df.draw_fund_info("000005")) == b"\x89PNG"
    assert "⚠ 东方财富未找到标的" in htmls[-1] and ai_texts and "宁德时代" in ai_texts[-1]

    monkeypatch.setattr(dmi, "_watch_index_quotes", _no_watch)
    monkeypatch.setattr(dmi.SsBind, "get_uid_list_by_game", staticmethod(_fake_uids))
    monkeypatch.setattr(dmi, "get_market", lambda: _FakeMarket(set()))
    ev = SimpleNamespace(at=None, user_id="u1", bot_id="b1")
    assert asyncio.run(dmi.draw_my_stock_img(ev)) == b"\x89PNG"
    assert 'class="column warn"' in htmls[-1] and "600519" in htmls[-1]


def test_kronos_lazy_torch_and_forecast_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Kronos torch 懒加载提示、模型/设备配置切换隔离缓存及冷热缓存保底 ai_return。"""
    monkeypatch.setitem(sys.modules, "torch", None)
    assert dm._resolve_kronos_device("cuda:0") == "cpu"
    frame = dm.fill_kline_by_kronos(_kline_series("预测测试", n=80))
    assert frame is not None
    res_no_torch = dm.gdf(frame, _kline_series("预测测试", n=80))
    assert isinstance(res_no_torch, str) and "torch" in res_no_torch

    series = _kline_series("预测缓存", n=80, seed=7)

    class _FakeMarket:
        async def kline(self, *_args: object) -> KlineSeries:
            return series

    class _FakeBot:
        async def send(self, *_args: object) -> None:
            pass

    async def _code_id(market: str) -> tuple[str, str, str]:
        return ("1.600000", market, "股票")

    draws: list[dm.KronosRunConfig] = []
    got: list[str] = []
    config = dm.KronosRunConfig("tokenizer/a", "model/a", "cpu")

    def fake_gdf(_df: object, _bars: object, conf: dm.KronosRunConfig) -> ChartPage:
        draws.append(conf)
        return _forecast_sample()

    async def fake_chart(_page: object) -> bytes:
        return _fixture_png()

    async def _run() -> None:
        nonlocal config
        monkeypatch.setattr(render_cache, "DATA_PATH", tmp_path)
        monkeypatch.setattr(dm, "get_market", _FakeMarket)
        monkeypatch.setattr(dm, "get_code_id", _code_id)
        monkeypatch.setattr(dm, "get_full_security_code", lambda code: code)
        monkeypatch.setattr(dm, "kronos_run_config", lambda: config)
        monkeypatch.setattr(dm, "gdf", fake_gdf)
        monkeypatch.setattr(dm, "render_chart", fake_chart)
        monkeypatch.setattr(dm, "ai_return", got.append)
        for _ in range(2):
            out = await dm.draw_ai_kline_with_forecast("预测缓存", _FakeBot())
            assert isinstance(out, str) and b64decode(out.removeprefix("base64://")) == _fixture_png()
        config = dm.KronosRunConfig("tokenizer/a", "model/b", "cuda:0")
        await dm.draw_ai_kline_with_forecast("预测缓存", _FakeBot())
        assert len(got) == 3 and got[0] == got[1] and len(draws) == 2 and len(list(tmp_path.glob("*.png"))) == 2

    asyncio.run(_run())
