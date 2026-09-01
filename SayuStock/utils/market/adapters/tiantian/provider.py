"""天天基金 MarketDataPort：场外基金净值 → KlineSeries（对比图用）。"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .parse import (
    FundHit,
    parse_nav_rows,
    make_fund_symbol,
    quote_from_nav_rows,
    parse_search_payload,
)
from .._base import PartialMarketData
from .client import fetch_nav_pages, fetch_fund_search, fetch_latest_nav_rows
from ...enums import KlinePeriod
from ...errors import MarketError, not_found, network_error
from ...models import Quote, SymbolRef, KlineSeries
from ...fund_route import extract_fund_code

PROVIDER = "tiantian"

_PERIOD_DAYS: dict[KlinePeriod, int] = {
    KlinePeriod.M5: 30,
    KlinePeriod.M15: 40,
    KlinePeriod.M30: 60,
    KlinePeriod.M60: 100,
    KlinePeriod.D1_RECENT: 50,
    KlinePeriod.D1: 400,
    KlinePeriod.W1: 1000,
    KlinePeriod.MON1: 2400,
    KlinePeriod.Q1: 4500,
    KlinePeriod.H1: 7000,
    KlinePeriod.Y1: 13000,
    KlinePeriod.D1_YEAR: 365,
}


async def is_otc_fund_query(query: str) -> bool:
    """东财 QuoteID 以 150. 开头的场外基金。"""
    text = query.strip()
    if not text:
        return False
    if re.fullmatch(r"150\.\d{6}", text):
        return True
    from ....load_data import get_full_security_code
    from ....stock.request_utils import get_code_id

    code_info = await get_code_id(text)
    if code_info is None:
        return False
    secid = get_full_security_code(code_info[0])
    return secid.startswith("150.")


class TiantianFundMarketData(PartialMarketData):
    provider_name = PROVIDER

    @staticmethod
    async def _otc_code_from_eastmoney(query: str) -> str | None:
        from ....load_data import get_full_security_code
        from ....stock.request_utils import get_code_id

        info = await get_code_id(query)
        if info is None:
            return None
        secid = get_full_security_code(info[0])
        if secid.startswith("150."):
            return secid.split(".", 1)[1]
        return None

    async def _resolve_hit(self, query: str) -> FundHit | MarketError:
        code = extract_fund_code(query)
        if code is None:
            code = await self._otc_code_from_eastmoney(query)
        key = code if code is not None else query.strip()
        payload = await fetch_fund_search(key)
        if isinstance(payload, str):
            return network_error(payload, provider=PROVIDER)
        hit = parse_search_payload(payload, key)
        if hit is None:
            return not_found("未找到该基金", provider=PROVIDER)
        return hit

    async def resolve(self, query: str) -> SymbolRef | None:
        hit = await self._resolve_hit(query)
        if isinstance(hit, MarketError):
            return None
        return make_fund_symbol(hit.code, hit.name, hit.fund_type)

    async def quote(self, query: str) -> Quote | MarketError:
        hit = await self._resolve_hit(query)
        if isinstance(hit, MarketError):
            return hit
        symbol = make_fund_symbol(hit.code, hit.name, hit.fund_type)
        rows = await fetch_latest_nav_rows(hit.code, limit=2)
        if isinstance(rows, MarketError):
            if hit.unit_nav is None:
                return rows
            return quote_from_nav_rows(symbol, hit, [])
        return quote_from_nav_rows(symbol, hit, rows)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        hit = await self._resolve_hit(query)
        if isinstance(hit, MarketError):
            return hit
        symbol = make_fund_symbol(hit.code, hit.name, hit.fund_type)
        end_d = end or date.today()
        if start is None:
            start_d = end_d - timedelta(days=_PERIOD_DAYS.get(period, 365))
        else:
            start_d = start
        span = (end_d - start_d).days + 30
        min_rows = min(3200, max(80, span))
        rows = await fetch_nav_pages(hit.code, min_rows=min_rows)
        if isinstance(rows, MarketError):
            return rows
        return parse_nav_rows(rows, symbol=symbol, period=period, start=start_d, end=end_d)
