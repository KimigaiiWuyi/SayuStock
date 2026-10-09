"""按 query 路由 equity / crypto / vix / 场外基金。"""

from __future__ import annotations

from typing import Literal
from datetime import date
from collections.abc import Sequence

from ..port import MarketDataPort
from ..enums import RankBy, BoardKind, IpoMarket, ValueKind, AssetClass, KlinePeriod
from ..errors import MarketError, is_market_error
from ..models import (
    Quote,
    IpoEvent,
    SymbolRef,
    BreadthBar,
    KlineSeries,
    ValueSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)
from ..display import stamp_provider
from ..fund_route import maybe_otc_fund_query
from .okx.provider import is_crypto_query
from .vix.provider import is_vix_query
from .tiantian.provider import is_otc_fund_query


class CompositeMarketData:
    def __init__(
        self,
        equity: MarketDataPort,
        crypto: MarketDataPort,
        vix: MarketDataPort,
        fund: MarketDataPort | None = None,
    ) -> None:
        self._equity = equity
        self._crypto = crypto
        self._vix = vix
        self._fund = fund

    def _route(self, query: str) -> MarketDataPort:
        if is_vix_query(query):
            return self._vix
        if is_crypto_query(query):
            return self._crypto
        return self._equity

    def _slot_provider(self, port: MarketDataPort) -> str | None:
        """非 equity 槽位的数据源 id；equity 由优先级链自行盖章。"""
        if port is self._crypto:
            return "okx"
        if port is self._vix:
            return "vix"
        if port is self._fund:
            return "tiantian"
        return None

    async def _kline_port(self, query: str) -> MarketDataPort:
        if is_vix_query(query):
            return self._vix
        if is_crypto_query(query):
            return self._crypto
        if self._fund is not None and maybe_otc_fund_query(query) and await is_otc_fund_query(query):
            return self._fund
        return self._equity

    async def resolve(self, query: str) -> SymbolRef | None:
        port = await self._kline_port(query)
        return await port.resolve(query)

    async def quote(self, query: str) -> Quote | MarketError:
        port = await self._kline_port(query)
        result = await port.quote(query)
        pid = self._slot_provider(port)
        if pid is not None and not is_market_error(result):
            result = stamp_provider(result, pid)
        return result

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return [await self.quote(q) for q in queries]

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        port = self._route(query)
        result = await port.intraday(query, ndays=ndays)
        pid = self._slot_provider(port)
        if pid is not None and not is_market_error(result):
            result = stamp_provider(result, pid)
        return result

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        port = await self._kline_port(query)
        series = await port.kline(query, period, start=start, end=end)
        if (
            port is self._equity
            and self._fund is not None
            and not is_market_error(series)
            and (series.symbol.asset_class == AssetClass.FUND or series.symbol.provider_symbol.startswith("150."))
        ):
            fund_series = await self._fund.kline(query, period, start=start, end=end)
            if not is_market_error(fund_series):
                return stamp_provider(fund_series, "tiantian")
        pid = self._slot_provider(port)
        if pid is not None and not is_market_error(series):
            series = stamp_provider(series, pid)
        return series

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        return await self._equity.board(kind, sector=sector, limit=limit, sort_asc=sort_asc)

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError:
        return await self._equity.rank_list(rank_by, limit=limit, high_first=high_first)

    async def hotmap(self) -> BoardSnapshot | MarketError:
        return await self._equity.hotmap()

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        return await self._equity.sector_menu(kind)

    async def breadth(self) -> BreadthBar | MarketError:
        return await self._equity.breadth()

    async def market_turnover(self) -> MarketTurnover | MarketError:
        return await self._equity.market_turnover()

    async def northbound(self) -> NorthboundFlow | MarketError:
        return await self._equity.northbound()

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError:
        return await self._equity.valuation_series(query, kind)

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError:
        return await self._equity.financial_snapshot(code)

    async def ipo_calendar(self, market: IpoMarket | str) -> list[IpoEvent] | MarketError:
        return await self._equity.ipo_calendar(market)
