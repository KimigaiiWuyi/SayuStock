"""纳斯达克 MarketDataPort：美股 IPO 日历专用备用源（其余接口 unsupported 回落）。"""

from __future__ import annotations

from datetime import date

from .parse import dedupe_ipo_events, months_for_window, parse_nasdaq_calendar
from .._base import PartialMarketData
from .client import PROVIDER, fetch_calendar_month
from ...enums import IpoMarket, coerce_ipo_market
from ...errors import MarketError, unsupported, network_error
from ...models import IpoEvent


class NasdaqMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def ipo_calendar(self, market: IpoMarket | str) -> list[IpoEvent] | MarketError:
        m = coerce_ipo_market(market)
        if m != IpoMarket.US:
            return unsupported(f"纳斯达克源仅支持美股IPO（收到 {market!r}）", provider=PROVIDER)
        months = months_for_window(date.today())
        events: list[IpoEvent] = []
        failures: list[str] = []
        for month in months:
            payload = await fetch_calendar_month(month)
            if isinstance(payload, str):
                failures.append(payload)
                continue
            parsed = parse_nasdaq_calendar(payload)
            if isinstance(parsed, MarketError):
                failures.append(parsed.message)
                continue
            events.extend(parsed)
        if events:
            return dedupe_ipo_events(events)
        if failures:
            # 整窗失败才记 network，注册表才能顺延东财；单月失败上面已留下其余月份
            return network_error(f"纳斯达克接口暂不可用: {failures[0]}", provider=PROVIDER)
        return unsupported("纳斯达克日历为空", provider=PROVIDER)
