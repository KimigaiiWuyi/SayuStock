"""全天候：国际市场板块失败时仍画出其余能报价的格子。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from SayuStock.stock_info import draw_future
from SayuStock.utils.market.enums import BoardKind, AssetClass
from SayuStock.utils.market.errors import MarketError, unsupported
from SayuStock.utils.market.models import Quote, BoardRow, SymbolRef, BoardSnapshot


def _quote(code: str, name: str, price: float, provider: str) -> Quote:
    return Quote(
        symbol=SymbolRef(
            code=code,
            name=name,
            asset_class=AssetClass.INDEX,
            exchange="",
            provider_symbol=code,
        ),
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=price,
        change_pct=0.1,
        change_amount=0.0,
        volume=None,
        amount=None,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=datetime(2026, 10, 9, 14, 30),
        provider=provider,
    )


class _Market:
    def __init__(self, *, board_ok: bool, quotes_ok: bool) -> None:
        self.board_ok = board_ok
        self.quotes_ok = quotes_ok
        self.quotes: list[str] = []

    async def board(self, kind: str, *, limit: int, sort_asc: bool) -> BoardSnapshot | MarketError:
        _ = (limit, sort_asc)
        if not self.board_ok:
            return unsupported("国际市场失败", provider="eastmoney")
        row = BoardRow(
            code="100.HSI",
            name="恒生指数",
            price=24000.0,
            change_pct=1.0,
            amount=None,
            market_cap=None,
            industry=None,
            lead_name=None,
            lead_change_pct=None,
        )
        return BoardSnapshot(kind=BoardKind.INTERNATIONAL, title=kind, rows=(row,), provider="eastmoney")

    async def quote(self, query: str) -> Quote | MarketError:
        self.quotes.append(query)
        if not self.quotes_ok:
            return unsupported(f"无 {query}", provider="tencent")
        if query == "1.000001":
            return _quote("1.000001", "上证指数", 3821.0, "tencent")
        if query == "BTC":
            return _quote("BTC", "BTC", 82500.0, "okx")
        return unsupported(f"无 {query}", provider="tencent")

    async def intraday(self, query: str) -> MarketError:
        _ = query
        return unsupported("仅盘口", provider="tencent")


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    market: _Market,
    render: object,
    ai: object,
) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(draw_future, "get_market", lambda: market)
    monkeypatch.setattr(draw_future, "render_html_to_bytes", render)
    monkeypatch.setattr(draw_future, "ai_return", ai)
    monkeypatch.setattr(draw_future.asyncio, "sleep", _sleep)


def test_board_failure_still_renders_other_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    market = _Market(board_ok=False, quotes_ok=True)
    order: list[str] = []

    async def _render(html: str, **_kwargs: object) -> bytes:
        order.append("render")
        assert "上证指数" in html
        assert "BTC" in html
        assert "腾讯财经" in html
        assert "OKX" in html
        assert "债券市场" not in html
        return b"png"

    def _ai(text: str) -> None:
        order.append("ai")
        assert "上证指数" in text

    _patch(monkeypatch, market, _render, _ai)
    assert asyncio.run(draw_future.draw_future_img()) == b"png"
    assert order == ["ai", "render"]
    assert "1.000001" in market.quotes
    assert "BTC" in market.quotes


def test_board_success_keeps_clist_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    market = _Market(board_ok=True, quotes_ok=True)

    async def _render(html: str, **_kwargs: object) -> bytes:
        assert "恒生指数" in html
        assert "东方财富" in html
        return b"ok"

    _patch(monkeypatch, market, _render, lambda _text: None)
    assert asyncio.run(draw_future.draw_future_img()) == b"ok"
    assert "100.HSI" not in market.quotes
    assert "1.000001" not in market.quotes


def test_nothing_to_draw_returns_board_message(monkeypatch: pytest.MonkeyPatch) -> None:
    market = _Market(board_ok=False, quotes_ok=False)
    called = False

    async def _render(_html: str, **_kwargs: object) -> bytes:
        nonlocal called
        called = True
        return b"no"

    _patch(monkeypatch, market, _render, lambda _text: None)
    assert asyncio.run(draw_future.draw_future_img()) == "国际市场失败"
    assert called is False
