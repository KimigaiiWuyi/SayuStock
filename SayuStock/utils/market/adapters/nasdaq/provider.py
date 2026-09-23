"""纳斯达克 MarketDataPort：美股 IPO 日历专用备用源（其余接口 unsupported 回落）。"""

from __future__ import annotations

from datetime import date

from .parse import months_for_window, parse_nasdaq_calendar
from .._base import PartialMarketData
from .client import PROVIDER, fetch_calendar_month
from ...enums import IpoMarket, coerce_ipo_market
from ...errors import MarketError, unsupported
from ...models import IpoEvent


class NasdaqMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def ipo_calendar(self, market: IpoMarket | str) -> list[IpoEvent] | MarketError:
        m = coerce_ipo_market(market)
        if m != IpoMarket.US:
            return unsupported(f"纳斯达克源仅支持美股IPO（收到 {market!r}）", provider=PROVIDER)
        months = months_for_window(date.today())
        events: list[IpoEvent] = []
        for month in months:
            payload = await fetch_calendar_month(month)
            if isinstance(payload, str):
                # 限流/网络失败：以 unsupported 交还注册表回落东财列表源
                return unsupported(f"纳斯达克接口暂不可用: {payload}", provider=PROVIDER)
            parsed = parse_nasdaq_calendar(payload)
            if isinstance(parsed, MarketError):
                return parsed
            events.extend(parsed)
        if not events:
            return unsupported("纳斯达克日历为空", provider=PROVIDER)
        return events
