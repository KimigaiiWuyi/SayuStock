"""Render-facing command contracts, without real accounts or market requests."""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import replace

import pytest
from extras_samples import (
    ipo_events,
    trade_card,
    paper_account,
    paper_positions,
    screener_result,
    portfolio_report,
    technical_report,
)
from test_sparkline import _a_share_series

from SayuStock.render import RenderError
from SayuStock.stock_ipo import draw_ipo
from SayuStock.utils.market import (
    IpoEvent,
    IpoMarket,
    SymbolRef,
    AssetClass,
    DisplayItem,
    KlinePeriod,
    KlineSeries,
    MarketError,
    IntradaySeries,
)
from SayuStock.stock_analysis import service
from SayuStock.stock_papertrade import render as paper
from SayuStock.utils.render_data import IpoCalendarRenderData, build_ipo_calendar_render_data
from SayuStock.stock_analysis.card import TradeCardData
from SayuStock.utils.market.errors import network_error
from SayuStock.stock_analysis.screener import ScreenerResult
from SayuStock.stock_analysis.portfolio import PortfolioRiskReport
from SayuStock.stock_analysis.technical import TechnicalReport
from SayuStock.utils.database.papertrade_models import SayuPaperAccount, SayuPaperPosition


class _IpoMarketStub:
    def __init__(self, all_fail: bool) -> None:
        self.all_fail = all_fail
        self.called: list[IpoMarket] = []

    async def ipo_calendar(self, market: IpoMarket) -> list[IpoEvent] | MarketError:
        self.called.append(market)
        if market == IpoMarket.US or self.all_fail:
            return network_error("fixture", provider="nasdaq")
        return [event for event in ipo_events() if event.market == market]


@pytest.mark.asyncio
@pytest.mark.parametrize("all_fail", [False, True])
async def test_ipo_failed_market_is_not_an_empty_market(all_fail: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    market = _IpoMarketStub(all_fail)
    trace: list[str] = []
    summaries: list[str] = []

    def build_data(events: list[IpoEvent], *, markets: list[IpoMarket] | None) -> IpoCalendarRenderData:
        return build_ipo_calendar_render_data(events, anchor=date(2026, 9, 22), markets=markets)

    def emit(text: str) -> None:
        trace.append("ai")
        summaries.append(text)

    async def draw(html: str, *, width: int, label: str) -> bytes:
        assert width == draw_ipo.CSS_WIDTH and label == "IPO日历"
        assert "市场数据源暂时不可用，未计入" in html
        assert "窗口内无发行/上市安排" not in html
        assert "示例電子科技" in html and "EXCO" not in html and "A股</h2>" not in html
        trace.append("image")
        return b"png"

    monkeypatch.setattr(draw_ipo, "get_market", lambda: market)
    monkeypatch.setattr(draw_ipo, "build_ipo_calendar_render_data", build_data)
    monkeypatch.setattr(draw_ipo, "ai_return", emit)
    monkeypatch.setattr(draw_ipo, "render_html", draw)
    result = await draw_ipo.draw_ipo_calendar_img("港股 美股")
    assert market.called == [IpoMarket.HK, IpoMarket.US]
    if all_fail:
        assert isinstance(result, str) and "数据获取失败" in result
        assert not trace
    else:
        assert result == b"png"
        assert trace == ["ai", "image"]
        assert "美股市场数据源暂时不可用，未计入" in summaries[0]
        assert "【美股】0只" not in summaries[0]


class _AnalysisMarketStub:
    async def kline(self, query: str, period: KlinePeriod) -> KlineSeries:
        return KlineSeries(
            symbol=SymbolRef("600001", "示例科技", AssetClass.EQUITY, "SSE", "1.600001"),
            period=period,
            bars=(),
            adjusted=True,
            provider="tencent",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_analysis_delivers_text_before_images_and_recovers_original_report(
    failed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace: list[str] = []
    summaries: list[str] = []
    technical = technical_report()
    card = trade_card()
    screener = screener_result(empty=True)
    portfolio = portfolio_report()

    def emit(text: str) -> None:
        trace.append("ai")
        summaries.append(text)

    def outcome() -> bytes:
        trace.append("image")
        if failed:
            raise RenderError("fixture")
        return b"png"

    async def fetch_card(text: str) -> TradeCardData:
        return card

    async def fetch_screener(text: str) -> ScreenerResult:
        return screener

    async def fetch_portfolio(codes: list[str]) -> PortfolioRiskReport:
        assert len(codes) == 9
        return portfolio

    async def image_technical(report: TechnicalReport) -> bytes:
        assert report is technical
        return outcome()

    async def image_card(report: TradeCardData) -> bytes:
        assert report is card
        return outcome()

    async def image_screener(report: ScreenerResult) -> bytes:
        assert report is screener
        return outcome()

    async def image_portfolio(report: PortfolioRiskReport, *, requested_count: int) -> bytes:
        assert report is portfolio and requested_count == 9
        return outcome()

    monkeypatch.setattr(service, "ai_return", emit)
    monkeypatch.setattr(service, "get_market", _AnalysisMarketStub)
    monkeypatch.setattr(service, "build_technical_report", lambda **_: technical)
    monkeypatch.setattr(service, "build_trade_card", fetch_card)
    monkeypatch.setattr(service, "run_screener", fetch_screener)
    monkeypatch.setattr(service, "analyze_portfolio", fetch_portfolio)
    monkeypatch.setattr(service, "render_technical_image", image_technical)
    monkeypatch.setattr(service, "render_card_image", image_card)
    monkeypatch.setattr(service, "render_screener_image", image_screener)
    monkeypatch.setattr(service, "render_portfolio_image", image_portfolio)

    results = [
        await service.run_technical_analysis("600001"),
        await service.run_stock_card("600001"),
        await service.run_auto_screener("换手>1"),
        await service.run_portfolio_check("1 2 3 4 5 6 7 8 9"),
    ]
    assert trace == ["ai", "image"] * 4
    assert results == (summaries if failed else [b"png"] * 4)
    assert "技术分析" in summaries[0] and "股票卡片" in summaries[1]
    assert summaries[2] == "无匹配结果"
    assert summaries[3].startswith("仅覆盖 8/9 只标的")


class _HoldingMarketStub:
    async def intraday(self, query: str) -> IntradaySeries | MarketError:
        if query == "000002":
            return network_error("fixture", provider="eastmoney")
        series = _a_share_series()
        assert series.quote is not None
        price = 84.36 if query == "600001" else float("inf")
        quote = replace(series.quote, price=price, change_pct=1.25 if query == "600001" else None, provider="tencent")
        return replace(series, quote=quote, provider="eastmoney")


@pytest.mark.asyncio
async def test_holdings_write_only_valid_live_price_and_emit_fallback_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    positions = paper_positions()
    trace: list[str] = []

    async def get_account(account_id: int) -> SayuPaperAccount:
        assert account_id == 7
        return paper_account()

    async def list_positions(account_id: int) -> list[SayuPaperPosition]:
        assert account_id == 7
        return positions

    async def index_items() -> list[DisplayItem]:
        return []

    async def write(quotes: list[dict[str, str | float | datetime]], account_id: int) -> int:
        assert account_id == 7 and len(quotes) == 1
        assert quotes[0]["stock_code"] == "600001" and quotes[0]["price"] == 84.36
        assert isinstance(quotes[0]["at"], datetime)
        trace.append("write")
        return 1

    def emit(text: str) -> None:
        assert "上次报价 58.04" in text and "成本估值 0.987" in text
        assert "未获取" in text and "网络异常" in text
        assert "今+1.25%" in text
        trace.append("ai")

    async def render(html: str, *, width: int, label: str) -> bytes:
        assert width == paper.PAPER_HOLDINGS_WIDTH and label == "模拟盘自选"
        assert "上次报价 58.04" in html and "成本估值 0.987" in html
        assert "2/3 只持仓今日行情获取失败" in html and "网络异常" in html
        assert "腾讯财经、东方财富" in html
        trace.append("image")
        raise RenderError("fixture")

    monkeypatch.setattr(paper.db.PaperAccountRepo, "get_by_id", get_account)
    monkeypatch.setattr(paper.db.PaperPositionRepo, "list_by_account", list_positions)
    monkeypatch.setattr(paper.db.PaperPositionRepo, "bulk_set_quote", write)
    monkeypatch.setattr(paper, "get_market", _HoldingMarketStub)
    monkeypatch.setattr(paper, "_load_index_items", index_items)
    monkeypatch.setattr(paper, "ai_return", emit)
    monkeypatch.setattr(paper, "render_html", render)
    assert await paper.build_holdings_snapshot_image(7) == "模拟盘自选出图失败"
    assert trace == ["write", "ai", "image"]
    assert positions[0].plan_stop_pct == -0.05 and positions[0].plan_thesis == "示例入场计划"


@pytest.mark.asyncio
async def test_holdings_command_and_ai_tool_send_png_or_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from base64 import b64decode, b64encode
    from collections.abc import Mapping

    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage
    from pydantic_ai.models.test import TestModel

    from gsuid_core.bot import Bot
    from gsuid_core.models import Event, Message
    from gsuid_core.ai_core.models import ToolContext
    from SayuStock.stock_papertrade import ai_tools, commands

    class CaptureBot(Bot):
        def __init__(self) -> None:
            self.messages: list[Message | list[Message] | str | bytes | list[str]] = []

        async def send(
            self,
            message: Message | list[Message] | str | bytes | list[str],
            at_sender: bool = False,
            extra_metadata: Mapping[str, object] | None = None,
            wait_recall: bool = False,
        ) -> list[str] | None:
            self.messages.append(message)
            return None

    acc = paper_account()
    bot = CaptureBot()
    ctx = RunContext(deps=ToolContext(bot=bot), model=TestModel(), usage=RunUsage())
    png = b"\x89PNG\r\n\x1a\n"
    fail = "模拟盘自选出图失败"
    results = iter((png, png, fail, fail))

    async def get_account(_: str) -> SayuPaperAccount:
        return acc

    async def resolve_account(_bot: Bot, _: str) -> SayuPaperAccount:
        return acc

    async def build_snapshot(account_id: int) -> bytes | str:
        assert account_id == acc.id
        return next(results)

    monkeypatch.setattr(commands, "_resolve_query_account", resolve_account)
    monkeypatch.setattr(commands, "build_holdings_snapshot_image", build_snapshot)
    monkeypatch.setattr(ai_tools, "_read_account", get_account)
    monkeypatch.setattr(paper, "build_holdings_snapshot_image", build_snapshot)

    await commands.send_holdings(bot, Event(text=acc.name))
    assert bot.messages == [png]
    assert "已发送" in await ai_tools.papertrade_holdings_image(ctx, acc.name)
    message = bot.messages[-1]
    assert isinstance(message, Message) and message.type == "image"
    assert message.data == f"base64://{b64encode(png).decode('ascii')}"
    assert b64decode(message.data.removeprefix("base64://")) == png
    await commands.send_holdings(bot, Event(text=acc.name))
    assert bot.messages[-1] == fail
    assert await ai_tools.papertrade_holdings_image(ctx, acc.name) == fail
    assert len(bot.messages) == 3
