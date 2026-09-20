"""Adapter 共用：未实现能力返回 unsupported。"""

from __future__ import annotations

from typing import Literal
from datetime import date
from collections.abc import Sequence

from ..enums import RankBy, BoardKind, ValueKind, KlinePeriod
from ..errors import MarketError, unsupported
from ..models import (
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


async def resolve_em_symbol(query: str) -> SymbolRef | None:
    """名称/代码 → SymbolRef，各 equity 源共用。

    provider_symbol 统一为东财 secid（如 1.600519）：调用方依赖 150.* 前缀
    判定场外基金，行情源切换不能破坏该约定。
    """
    from ...load_data import get_full_security_code
    from .eastmoney.provider import _exchange_of, _sec_type_to_asset
    from ...stock.request_utils import get_code_id

    code_info = await get_code_id(query)
    if code_info is None:
        return None
    secid = get_full_security_code(code_info[0])
    if not secid or "." not in secid:
        return None
    return SymbolRef(
        code=secid.split(".")[-1],
        name=code_info[1] or secid.split(".")[-1],
        asset_class=_sec_type_to_asset(code_info[2], secid=secid),
        exchange=_exchange_of(secid, code_info[2]),
        provider_symbol=secid,
        sec_type=code_info[2] or "",
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

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        _ = ndays
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

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError:
        return unsupported("rank_list 未实现", provider=self.provider_name)

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
