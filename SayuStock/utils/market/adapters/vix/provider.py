"""VIX 数据源 → 领域模型。"""

from __future__ import annotations

from datetime import date, datetime

from .._base import PartialMarketData
from ...enums import AssetClass
from ...errors import MarketError, not_found, empty_error, unsupported, network_error
from ...models import Quote, SymbolRef, IntradayPoint, IntradaySeries
from ....constant import VIX_LIST
from ....stock.get_vix import get_vix_data

PROVIDER = "vix"


def resolve_vix_key(query: str) -> str | None:
    key = query.replace(" ", "").upper()
    if key in VIX_LIST:
        return VIX_LIST[key]
    # 直接传内部名
    if key.lower() in {v.lower() for v in VIX_LIST.values()}:
        return key.lower()
    return None


def is_vix_query(query: str) -> bool:
    return resolve_vix_key(query) is not None


class VixMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def resolve(self, query: str) -> SymbolRef | None:
        vix = resolve_vix_key(query)
        if vix is None:
            return None
        return SymbolRef(
            code=vix,
            name=query.strip(),
            asset_class=AssetClass.VIX,
            exchange="OPTBBS",
            provider_symbol=vix,
        )

    async def quote(self, query: str) -> Quote | MarketError:
        series = await self.intraday(query)
        if isinstance(series, MarketError):
            return series
        if not series.points:
            return empty_error("VIX 无点", provider=PROVIDER)
        last = series.points[-1]
        first = series.points[0]
        prev = first.open if first.open != 0 else first.price
        chg = ((last.price - prev) / prev * 100.0) if prev else None
        return Quote(
            symbol=series.symbol,
            price=last.price,
            open=first.open if first.open else first.price,
            high=max(p.high for p in series.points),
            low=min(p.low for p in series.points),
            prev_close=prev,
            change_pct=round(chg, 2) if chg is not None else None,
            change_amount=None,
            volume=0.0,
            amount=0.0,
            turnover_rate=0.0,
            pe=None,
            pb=None,
            market_cap=None,
            float_market_cap=None,
            industry=None,
            limit_up=None,
            limit_down=None,
            as_of=last.ts,
        )

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        if ndays != 1:
            return unsupported("VIX 不支持五日分时，请使用 个股 300vix 查看当日分时", provider=PROVIDER)
        vix = resolve_vix_key(query)
        if vix is None:
            return not_found("非 VIX 标的", provider=PROVIDER)
        symbol = SymbolRef(
            code=vix,
            name=query.strip(),
            asset_class=AssetClass.VIX,
            exchange="OPTBBS",
            provider_symbol=vix,
        )
        raw = await get_vix_data(vix)
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        today = date.today()
        points: list[IntradayPoint] = []
        for row in raw:
            # 源数据仅 HH:MM，贴到今日避免丢失日期
            try:
                hm = datetime.strptime(row["datetime"], "%H:%M")
            except ValueError:
                continue
            ts = datetime(today.year, today.month, today.day, hm.hour, hm.minute)
            points.append(
                IntradayPoint(
                    ts=ts,
                    price=float(row["price"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    volume=float(row["amount"]),
                    amount=float(row["money"]),
                    avg_price=float(row["avg_price"]) if row["avg_price"] else float(row["price"]),
                )
            )
        if not points:
            return empty_error("VIX 分时为空", provider=PROVIDER)
        return IntradaySeries(symbol=symbol, points=tuple(points), quote=None)
