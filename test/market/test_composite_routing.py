"""Composite 路由与 FakePort 注入。"""

from __future__ import annotations

import asyncio
from typing import Literal
from datetime import date
from collections.abc import Sequence

from SayuStock.utils.market import get_market, set_market
from SayuStock.utils.market.enums import BoardKind, ValueKind, AssetClass, KlinePeriod
from SayuStock.utils.market.errors import MarketError, unsupported, is_market_error
from SayuStock.utils.market.models import (
    Quote,
    SymbolRef,
    BreadthBar,
    KlineSeries,
    ValueSeries,
    BoardSnapshot,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)
from SayuStock.utils.market.adapters.composite import CompositeMarketData
from SayuStock.utils.market.adapters.okx.provider import is_crypto_query
from SayuStock.utils.market.adapters.vix.provider import is_vix_query


class _TagPort:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    async def resolve(self, query: str) -> SymbolRef | None:
        return SymbolRef(
            code=query,
            name=self.tag,
            asset_class=AssetClass.OTHER,
            exchange=self.tag,
            provider_symbol=query,
        )

    async def quote(self, query: str) -> Quote | MarketError:
        sym = await self.resolve(query)
        assert sym is not None
        return Quote(
            symbol=sym,
            price=1.0,
            open=None,
            high=None,
            low=None,
            prev_close=None,
            change_pct=None,
            change_amount=None,
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
            as_of=None,
        )

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return [await self.quote(q) for q in queries]

    async def intraday(self, query: str) -> IntradaySeries | MarketError:
        return unsupported("x", provider=self.tag)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        return unsupported("x", provider=self.tag)

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        return unsupported("x", provider=self.tag)

    async def rank_list(
        self,
        rank_by: object,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> MarketError:
        return unsupported("x", provider=self.tag)

    async def hotmap(self) -> BoardSnapshot | MarketError:
        return unsupported("x", provider=self.tag)

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        return unsupported("x", provider=self.tag)

    async def breadth(self) -> BreadthBar | MarketError:
        return unsupported("x", provider=self.tag)

    async def market_turnover(self) -> MarketTurnover | MarketError:
        return unsupported("x", provider=self.tag)

    async def northbound(self) -> NorthboundFlow | MarketError:
        return unsupported("x", provider=self.tag)

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError:
        return unsupported("x", provider=self.tag)

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError:
        return unsupported("x", provider=self.tag)


def test_is_crypto_and_vix() -> None:
    assert is_crypto_query("BTC")
    assert is_vix_query("300VIX")
    assert not is_vix_query("600519")


def test_composite_routes_by_query() -> None:
    async def _run() -> None:
        port = CompositeMarketData(_TagPort("equity"), _TagPort("crypto"), _TagPort("vix"))
        q1 = await port.quote("600519")
        assert not is_market_error(q1)
        assert q1.symbol.name == "equity"
        q2 = await port.quote("BTC")
        assert not is_market_error(q2)
        assert q2.symbol.name == "crypto"
        q3 = await port.quote("300VIX")
        assert not is_market_error(q3)
        assert q3.symbol.name == "vix"

    asyncio.run(_run())


def test_set_market_injection() -> None:
    async def _run() -> None:
        fake = _TagPort("fake")
        set_market(fake)
        try:
            m = get_market()
            q = await m.quote("anything")
            assert not is_market_error(q)
            assert q.symbol.name == "fake"
        finally:
            set_market(None)

    asyncio.run(_run())
