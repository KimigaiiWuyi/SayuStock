"""Adapter 共用：未实现能力返回 unsupported。"""

from __future__ import annotations

from typing import Literal
from datetime import date
from collections.abc import Sequence

from ..enums import BoardKind, ValueKind, KlinePeriod
from ..errors import MarketError, unsupported
from ..models import (
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


class PartialMarketData:
    """子类实现子集方法；其余返回 unsupported。"""

    provider_name: str = "partial"

    async def resolve(self, query: str) -> SymbolRef | None:
        return None

    async def quote(self, query: str) -> Quote | MarketError:
        return unsupported("quote 未实现", provider=self.provider_name)

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return [await self.quote(q) for q in queries]

    async def intraday(self, query: str) -> IntradaySeries | MarketError:
        return unsupported("intraday 未实现", provider=self.provider_name)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        return unsupported("kline 未实现", provider=self.provider_name)

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        return unsupported("board 未实现", provider=self.provider_name)

    async def hotmap(self) -> BoardSnapshot | MarketError:
        return unsupported("hotmap 未实现", provider=self.provider_name)

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        return unsupported("sector_menu 未实现", provider=self.provider_name)

    async def breadth(self) -> BreadthBar | MarketError:
        return unsupported("breadth 未实现", provider=self.provider_name)

    async def market_turnover(self) -> MarketTurnover | MarketError:
        return unsupported("market_turnover 未实现", provider=self.provider_name)

    async def northbound(self) -> NorthboundFlow | MarketError:
        return unsupported("northbound 未实现", provider=self.provider_name)

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError:
        return unsupported("valuation_series 未实现", provider=self.provider_name)

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError:
        return unsupported("financial_snapshot 未实现", provider=self.provider_name)
