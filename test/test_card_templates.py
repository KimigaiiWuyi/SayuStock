"""Offline cards cover the layouts and quote states used by the four commands."""

from __future__ import annotations

import os
import re
import asyncio
from io import BytesIO
from math import sin
from typing import NamedTuple
from pathlib import Path
from datetime import datetime
from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from SayuStock.render import render_html
from SayuStock.utils.warn_box import Failure
from SayuStock.utils.sparkline import build_sparkline_svg
from SayuStock.utils.market.enums import AssetClass
from SayuStock.render.card_widgets import watch_sparkline
from SayuStock.utils.market.models import (
    BREADTH_BANDS,
    Quote,
    SymbolRef,
    IntradayPoint,
    IntradaySeries,
    breadth_up_down,
)
from SayuStock.utils.my_stock_html import WATCH_INDEXES, watchlist_width, build_my_stock_html
from SayuStock.stock_info.draw_info import _flow_image_uri
from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.all_weather_html import CSS_WIDTH as WEATHER_WIDTH, build_all_weather_html
from SayuStock.utils.fund_holdings_html import PAGE_W as FUND_WIDTH, FundHoldingRow, build_fund_holdings_html
from SayuStock.utils.market_overview_html import CSS_WIDTH as OVERVIEW_WIDTH, build_market_overview_html


class CardSample(NamedTuple):
    label: str
    width: int
    html: str


def _quote(code: str, name: str, price: float, change: float, move: float | None, market: str = "沪A") -> Quote:
    symbol = SymbolRef(code, name, AssetClass.EQUITY, "SSE", f"1.{code}", market)
    return Quote(
        symbol=symbol,
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=None,
        change_pct=change,
        change_amount=move,
        volume=10_000.0,
        amount=320_000_000.0,
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


def _sample_spark(quote: Quote, *, gap: bool = False) -> str:
    prices = [quote.price + 0.004 * sin(i / 3) + 0.0001 * i for i in range(43)]
    points = tuple(
        IntradayPoint(datetime(2026, 8, 24, 10, i), p, p, p, p, 1, p, p)
        for i, p in enumerate(prices)
        if not gap or i < 14 or i > 19
    )
    series = IntradaySeries(quote.symbol, points, quote, provider="tencent")
    return watch_sparkline(series, quote, width=130, height=40)


def _indexes() -> list[Quote | None]:
    values = [(3930.12, 0.57), (12853.78, -0.62), (5931.59, 0.0), (2785.20, 0.36)]
    return [
        _quote(code, name, price, change, None, "指数")
        for (name, code), (price, change) in zip(WATCH_INDEXES, values, strict=True)
    ]


def _watchlist(count: int) -> CardSample:
    indexes = _indexes()
    indexes[-1] = None
    stocks = [
        _quote("588170", "科创半导体ETF华夏", 0.652, 0.31, 0.002),
        _quote("159819", "人工智能ETF易方达", 0.988, -0.2, -0.002, "深A"),
        _quote("600519", "贵州茅台", 1497.00, 0.0, 0.0),
    ]
    failure = [Failure("000858", "东方财富网络异常")] if count > 3 else []
    names = ("中远海控", "招商银行", "长江电力", "金山办公", "中芯国际", "工业富联", "宁德时代", "京沪高铁")
    for index in range(len(stocks), count - len(failure)):
        name = names[(index - 3) % len(names)]
        change = (index % 7 - 3) * 0.37
        stocks.append(_quote(str(600700 + index), name, 9.53 + index * 5.13, change, round(change * 0.46, 2)))
    sparks = {q.symbol.code: _sample_spark(q, gap=i == 0) for i, q in enumerate(stocks) if i != 1}
    return CardSample(
        f"watchlist-{count}",
        watchlist_width(count),
        build_my_stock_html(
            quotes=[(q, q.symbol.code) for q in stocks],
            index_quotes=indexes,
            saved_count=count,
            sparklines=sparks,
            failures=failure,
        ),
    )


def card_samples() -> tuple[CardSample, ...]:
    indexes = [
        DisplayItem(name, q.price, q.change_pct or 0.0, provider=q.provider)
        for (name, _), q in zip(WATCH_INDEXES, _indexes(), strict=True)
        if q is not None
    ]
    generic_spark = build_sparkline_svg([100 + sin(i / 3) + i * 0.04 for i in range(45)], 100, up=True)
    fund = build_fund_holdings_html(
        fund_name="沪深优选成长混合",
        fund_code="012345",
        index_items=indexes,
        rows=[
            FundHoldingRow("588170", "科创半导体ETF华夏", "9.82", 0.652, 0.31, provider="tencent"),
            FundHoldingRow("300750", "宁德时代（创业板）", "8.31", 425.67, -0.73, provider="tencent"),
            FundHoldingRow("000858", "五粮液", "6.20", failed=True, reason="东方财富网络异常"),
        ],
        meta="持仓 3 只 · 前十占比 24.33%",
        average=-0.21,
        report_period="报告期 2026-09-30",
        sparklines={"588170": generic_spark},
    )
    other_indexes = ("创业板指", "科创综指", "沪深300", "中证500", "中证1000", "北证50", "黄金9999", "三十债主连")
    overview_indexes = indexes + [
        DisplayItem(name, 1305 + i * 80, (i - 4) * 0.31) for i, name in enumerate(other_indexes)
    ]
    ranking_labels = ("能源金属", "油气开采", "消费电子", "智能驾驶", "人形机器人")
    rise = [
        DisplayItem(ranking_labels[i % 5], 0, 6 - i * 0.19, lead_name="样例成分", lead_change_pct=8.52)
        for i in range(20)
    ]
    fall = [replace(item, change_pct=-item.change_pct, fall_name="样例成分", fall_change_pct=-8.52) for item in rise]
    diff_bar = dict(zip(BREADTH_BANDS, (42, 170, 230, 610, 923, 704, 95, 645, 280, 175, 100, 45, 11), strict=True))
    up, down = breadth_up_down(diff_bar)

    def overview(flow: str) -> str:
        return build_market_overview_html(
            data_zs_items=overview_indexes,
            sparks={item.name: generic_spark for item in overview_indexes},
            diff_bar=diff_bar,
            up_value=up,
            down_value=down,
            breadth_available=True,
            em_flow_uri=flow,
            now=datetime(2026, 8, 24, 10, 42),
            trade_date=None,
            turnover="1.42万亿",
            turnover_delta="放量 840亿",
            turnover_tone="up",
            data_hy_fall=fall,
            data_hy_rise=rise,
            data_gn_fall=fall,
            data_gn_rise=rise,
            source_line="数据来源：指数腾讯财经 板块东方财富 | SayuStock",
            title_num="5",
        )

    flow_image = Image.new("RGB", (500, 274), "white")
    drawing = ImageDraw.Draw(flow_image)
    for y in range(32, 250, 42):
        drawing.line((16, y, 488, y), fill=(185, 185, 185))
    for base, color in ((113, (37, 155, 179)), (161, (135, 58, 24)), (200, (89, 21, 73))):
        curve = [(x, round(base - x * 0.13 + 14 * sin(x / 32 + base))) for x in range(16, 489)]
        drawing.line(curve, fill=color, width=2)
    weather = build_all_weather_html(
        [
            ("国际市场", indexes + [DisplayItem("日经225", 55642.1, -0.7), DisplayItem("纳斯达克", 25760.31, 0.44)]),
            ("大宗商品", [DisplayItem("XAU", 4210.53, 0.16), DisplayItem("NYMEX原油", 68.14, -1.73)]),
            (
                "债券市场",
                [DisplayItem("美国10年期国债收益率", 4.117, 0.52), DisplayItem("中国30年期国债", 2.14, -0.05)],
            ),
            ("外汇市场", [DisplayItem("美元兑日元", 155.8, 0.22), DisplayItem("美元兑离岸人民币", 7.04, -0.09)]),
            (
                "加密货币",
                [
                    DisplayItem(name, price, change)
                    for name, price, change in (
                        ("BTC", 85013, 1.87),
                        ("ETH", 3260, 1.17),
                        ("SOL", 167.3, -0.39),
                        ("XRP", 1.91, 0.0),
                    )
                ],
            ),
        ],
        now=datetime(2026, 8, 24, 10, 42),
        sparklines={"上证指数": generic_spark, "XAU": generic_spark, "BTC": generic_spark, "ETH": generic_spark},
    )
    return (
        _watchlist(3),
        _watchlist(18),
        _watchlist(22),
        CardSample("fund", FUND_WIDTH, fund),
        CardSample("overview", OVERVIEW_WIDTH, overview(_flow_image_uri(flow_image))),
        CardSample("overview-no-flow", OVERVIEW_WIDTH, overview("")),
        CardSample("all-weather", WEATHER_WIDTH, weather),
    )


def test_watchlist_quote_move_and_sparkline_do_not_invent_observations() -> None:
    q = _quote("588170", "科创半导体ETF华夏", 0.652, 0.31, 0.002)
    gap = _sample_spark(q, gap=True)
    assert len(re.findall(r"M[\d.]", gap)) >= 4
    dot = re.search(r'<circle cx="([\d.]+)"', gap)
    assert dot is not None and float(dot.group(1)) < 127
    quotes = [
        (q, "588170"),
        (replace(q, change_amount=None, prev_close=0.654), "512880"),
        (replace(q, change_amount=None, prev_close=None), "159819"),
    ]
    html = build_my_stock_html(quotes=quotes, index_quotes=_indexes(), saved_count=3)
    assert ">+0.002<" in html and ">-0.002<" in html
    assert html.count('class="q-move"') == 2


@pytest.mark.usefixtures("require_render_runtime")
def test_card_pages_render_png_with_installed_runtime() -> None:
    target = os.getenv("SAYUSTOCK_RENDER_EVIDENCE_DIR")
    directory = Path(target) if target else None
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)

    async def render_all() -> None:
        for sample in card_samples():
            png = await render_html(sample.html, width=sample.width, label=sample.label)
            with Image.open(BytesIO(png)) as image:
                assert image.format == "PNG"
                assert image.width == round(sample.width * 1.5)
                assert image.height > 300
            if directory is not None:
                (directory / f"{sample.label}.png").write_bytes(png)

    asyncio.run(render_all())
