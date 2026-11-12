"""Offline PNG gallery for the eight upstream-only SayuStock pages."""

from __future__ import annotations

import os
import asyncio
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass

import pytest
from PIL import Image
from extras_samples import (
    ipo_data,
    ipo_events,
    trade_card,
    paper_trades,
    paper_account,
    paper_positions,
    paper_snapshots,
    screener_result,
    portfolio_report,
    technical_report,
)

from SayuStock.render import render_html
from SayuStock.utils.market import IpoMarket, DisplayItem
from SayuStock.utils.warn_box import Failure
from SayuStock.utils.sparkline import build_sparkline_svg
from SayuStock.render.templates import render_template
from SayuStock.utils.render_data import build_ipo_calendar_render_data
from SayuStock.stock_ipo.ipo_html import CSS_WIDTH, build_ipo_calendar_html
from SayuStock.stock_analysis.render import (
    REPORT_WIDTH,
    SCREENER_WIDTH,
    build_screener_html,
    build_portfolio_html,
    build_technical_html,
    build_trade_card_html,
)
from SayuStock.stock_papertrade.render import PAPER_WIDTH, _rank_rows, _account_html, _account_view
from SayuStock.utils.paper_holdings_html import (
    SPARK_H,
    SPARK_W,
    PAPER_HOLDINGS_WIDTH,
    HoldingBarRow,
    build_paper_holdings_html,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SAYUSTOCK_REQUIRE_RENDER_RUNTIME") != "1",
    reason="SAYUSTOCK_REQUIRE_RENDER_RUNTIME=1 enables the offline Takumi gallery",
)


@dataclass(frozen=True, slots=True)
class _Sample:
    name: str
    width: int
    html: str


def _holding_html(*, empty: bool = False) -> str:
    live = HoldingBarRow(
        "600001", "示例科技", 2000, 82.25, 84.36, 1.25, 4220.0, 2.5653, 168720.0, "tencent", spark_provider="eastmoney"
    )
    cached = HoldingBarRow(
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
    )
    at_cost = HoldingBarRow(
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
    )
    indices = [
        DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001", provider="eastmoney"),
        DisplayItem(name="深证成指", price=11291.05, change_pct=0.41, code="399001", provider="tencent"),
        DisplayItem(name="中证A500", price=4857.27, change_pct=0.0, code="000510", provider="eastmoney"),
        DisplayItem(name="中证2000", price=2846.32, change_pct=0.35, code="932000", provider="eastmoney"),
    ]
    prices: list[float | None] = [82.2, 81.9, 82.4, 82.8, None, 82.9, 83.2, 82.85, 83.4, 84.36, None, None]
    svg = build_sparkline_svg(prices, 82.2, up=True, width=SPARK_W, height=SPARK_H)
    return build_paper_holdings_html(
        account_name="示例多因子盘",
        strategy_id="multi_factor",
        enabled=True,
        cash=1_000_000.0 if empty else 638_075.0,
        initial_cash=1_000_000.0,
        holdings=[] if empty else [live, cached, at_cost],
        index_items=indices,
        sparklines={live.code: svg},
        failures=[] if empty else [Failure(f"{row.name}({row.code})", row.quote_error) for row in (cached, at_cost)],
        warn_title="2/3 只持仓今日行情获取失败",
    )


def _samples() -> list[_Sample]:
    accounts = {i: f"示例策略盘 {i:02}" for i in range(1, 20)}
    rank = _rank_rows(paper_snapshots(), accounts)
    partial_ipo = build_ipo_calendar_render_data(
        [event for event in ipo_events() if event.market != IpoMarket.US], anchor=ipo_data().anchor
    )
    return [
        _Sample(
            "paper-account",
            PAPER_WIDTH,
            _account_html(_account_view(paper_account(), paper_positions(), paper_trades())),
        ),
        _Sample("paper-account-empty", PAPER_WIDTH, _account_html(_account_view(paper_account(), [], []))),
        _Sample("paper-holdings", PAPER_HOLDINGS_WIDTH, _holding_html()),
        _Sample("paper-holdings-empty", PAPER_HOLDINGS_WIDTH, _holding_html(empty=True)),
        _Sample(
            "paper-leaderboard",
            PAPER_WIDTH,
            render_template(
                "paper_leaderboard.html", width=PAPER_WIDTH, rows=rank, footer="SayuStock · 模拟盘每日净值快照"
            ),
        ),
        _Sample(
            "paper-leaderboard-empty",
            PAPER_WIDTH,
            render_template(
                "paper_leaderboard.html", width=PAPER_WIDTH, rows=(), footer="SayuStock · 模拟盘每日净值快照"
            ),
        ),
        _Sample("ipo-calendar", CSS_WIDTH, build_ipo_calendar_html(ipo_data())),
        _Sample("ipo-partial", CSS_WIDTH, build_ipo_calendar_html(partial_ipo, failed=["美股"])),
        _Sample(
            "ipo-hk",
            CSS_WIDTH,
            build_ipo_calendar_html(
                build_ipo_calendar_render_data(ipo_events(), anchor=ipo_data().anchor, markets=[IpoMarket.HK])
            ),
        ),
        _Sample("analysis-technical", REPORT_WIDTH, build_technical_html(technical_report())),
        _Sample("analysis-trade-card", REPORT_WIDTH, build_trade_card_html(trade_card())),
        _Sample("analysis-trade-card-partial", REPORT_WIDTH, build_trade_card_html(trade_card(partial=True))),
        _Sample("analysis-screener", SCREENER_WIDTH, build_screener_html(screener_result())),
        _Sample("analysis-screener-empty", SCREENER_WIDTH, build_screener_html(screener_result(empty=True))),
        _Sample("analysis-portfolio", REPORT_WIDTH, build_portfolio_html(portfolio_report(), requested_count=10)),
    ]


@pytest.mark.asyncio
async def test_extras_node_gallery() -> None:
    samples = _samples()
    results = await asyncio.gather(
        *(render_html(sample.html, width=sample.width, label=sample.name) for sample in samples)
    )
    root = os.environ.get("SAYUSTOCK_RENDER_EVIDENCE_DIR")
    directory = Path(root) / "extras" if root else None
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
    heights: dict[str, int] = {}
    for sample, png in zip(samples, results, strict=True):
        with Image.open(BytesIO(png)) as image:
            image.load()
            assert image.format == "PNG"
            assert image.width == sample.width * 1.5
            assert image.height > 200
            assert len(image.getcolors(maxcolors=64) or []) == 0
            heights[sample.name] = image.height
        if directory:
            (directory / f"{sample.name}.png").write_bytes(png)
    assert heights["paper-account"] > heights["paper-account-empty"]
    assert heights["paper-holdings"] > heights["paper-holdings-empty"]
    assert heights["paper-leaderboard"] > heights["paper-leaderboard-empty"] + 20 * 40
    assert heights["analysis-screener"] > heights["analysis-screener-empty"] + 8 * 40
