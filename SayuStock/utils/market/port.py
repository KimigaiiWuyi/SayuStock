"""MarketDataPort：供应商无关的异步行情契约。"""

from __future__ import annotations

from typing import Literal, Protocol
from datetime import date
from collections.abc import Sequence

from .enums import RankBy, BoardKind, ValueKind, KlinePeriod
from .errors import MarketError
from .models import (
    Quote,
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


class MarketDataPort(Protocol):
    async def resolve(self, query: str) -> SymbolRef | None: ...

    async def quote(self, query: str) -> Quote | MarketError: ...

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]: ...

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError: ...

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError: ...

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError: ...

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError: ...

    async def hotmap(self) -> BoardSnapshot | MarketError: ...

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError: ...

    async def breadth(self) -> BreadthBar | MarketError: ...

    async def market_turnover(self) -> MarketTurnover | MarketError: ...

    async def northbound(self) -> NorthboundFlow | MarketError: ...

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError: ...

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError: ...
