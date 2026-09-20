"""腾讯财经 MarketDataPort：A股票/指数/ETF 的备用权益行情源。"""

from __future__ import annotations

import asyncio
from datetime import date
from dataclasses import replace
from collections.abc import Sequence

from .parse import (
    parse_qt_line,
    parse_kline_payload,
    parse_minute_payload,
    tencent_symbol_from_secid,
)
from .._base import PartialMarketData, resolve_em_symbol
from .client import PROVIDER, fetch_minute, fetch_mkline, fetch_fqkline, fetch_qt_lines
from ...enums import KlinePeriod
from ...errors import MarketError, not_found, unsupported, network_error
from ...models import Quote, SymbolRef, KlineSeries, IntradaySeries
from ....constant import ErroText

# 分钟周期 → 腾讯 mkline unit
_MINUTE_UNIT: dict[KlinePeriod, str] = {
    KlinePeriod.M5: "m5",
    KlinePeriod.M15: "m15",
    KlinePeriod.M30: "m30",
    KlinePeriod.M60: "m60",
}

# 日级周期 → 腾讯 fqkline unit；季/半年/年腾讯无对应接口
_DAILY_UNIT: dict[KlinePeriod, str] = {
    KlinePeriod.D1: "day",
    KlinePeriod.D1_RECENT: "day",
    KlinePeriod.D1_YEAR: "day",
    KlinePeriod.W1: "week",
    KlinePeriod.MON1: "month",
}

# 各周期默认拉取 bar 数
_PERIOD_BARS: dict[KlinePeriod, int] = {
    KlinePeriod.M5: 1000,
    KlinePeriod.M15: 1000,
    KlinePeriod.M30: 1000,
    KlinePeriod.M60: 1000,
    KlinePeriod.D1_RECENT: 60,
    KlinePeriod.D1: 500,
    KlinePeriod.D1_YEAR: 365,
    KlinePeriod.W1: 520,
    KlinePeriod.MON1: 240,
}


class TencentMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def resolve(self, query: str) -> SymbolRef | None:
        return await resolve_em_symbol(query)

    async def _symbol_of(self, query: str) -> SymbolRef | MarketError:
        ref = await self.resolve(query)
        if ref is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
        if tencent_symbol_from_secid(ref.provider_symbol) is None:
            # 港美股/期货等暂不覆盖，交给注册表回落
            return unsupported(f"腾讯不支持 {ref.provider_symbol}", provider=PROVIDER)
        return ref

    async def quote(self, query: str) -> Quote | MarketError:
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        qt_sym = tencent_symbol_from_secid(symbol.provider_symbol)
        assert qt_sym is not None
        lines = await fetch_qt_lines([qt_sym])
        if isinstance(lines, MarketError):
            return lines
        line = lines.get(qt_sym)
        if line is None or not line.strip():
            return not_found(ErroText["notStock"], provider=PROVIDER)
        return parse_qt_line(line, symbol=symbol)

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        symbols = await asyncio.gather(*[self._symbol_of(q) for q in queries])
        sym_by_idx: dict[int, SymbolRef] = {}
        qt_by_idx: dict[int, str] = {}
        results: list[Quote | MarketError | None] = [None] * len(queries)
        for i, symbol in enumerate(symbols):
            if isinstance(symbol, MarketError):
                results[i] = symbol
                continue
            qt_sym = tencent_symbol_from_secid(symbol.provider_symbol)
            if qt_sym is None:
                results[i] = unsupported(f"腾讯不支持 {symbol.provider_symbol}", provider=PROVIDER)
                continue
            sym_by_idx[i] = symbol
            qt_by_idx[i] = qt_sym
        if qt_by_idx:
            lines = await fetch_qt_lines(list(qt_by_idx.values()))
            for i, qt_sym in qt_by_idx.items():
                if isinstance(lines, MarketError):
                    results[i] = lines
                    continue
                line = lines.get(qt_sym)
                if line is None or not line.strip():
                    results[i] = not_found(ErroText["notStock"], provider=PROVIDER)
                    continue
                results[i] = parse_qt_line(line, symbol=sym_by_idx[i])
        return [r if r is not None else not_found(ErroText["notStock"], provider=PROVIDER) for r in results]

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        if ndays > 1:
            return unsupported("腾讯仅支持当日分时", provider=PROVIDER)
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        qt_sym = tencent_symbol_from_secid(symbol.provider_symbol)
        assert qt_sym is not None
        quote = await self.quote(query)
        payload = await fetch_minute(qt_sym)
        if isinstance(payload, str):
            return network_error(payload, provider=PROVIDER)
        trade_date = date.today().strftime("%Y-%m-%d")
        if not isinstance(quote, MarketError) and quote.as_of is not None:
            trade_date = quote.as_of.strftime("%Y-%m-%d")
        return parse_minute_payload(
            payload,
            symbol=symbol,
            tencent_symbol=qt_sym,
            quote=None if isinstance(quote, MarketError) else quote,
            trade_date=trade_date,
        )

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        minute_unit = _MINUTE_UNIT.get(period)
        daily_unit = _DAILY_UNIT.get(period)
        if minute_unit is None and daily_unit is None:
            return unsupported(f"腾讯不支持 {period} K线", provider=PROVIDER)
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        qt_sym = tencent_symbol_from_secid(symbol.provider_symbol)
        assert qt_sym is not None
        datalen = _PERIOD_BARS.get(period, 400)
        if start is not None:
            end_d = end or date.today()
            datalen = max(datalen, (end_d - start).days)
        datalen = min(datalen, 2000)
        if minute_unit is not None:
            payload = await fetch_mkline(qt_sym, minute_unit, datalen)
            adjusted = False
        else:
            assert daily_unit is not None
            payload = await fetch_fqkline(qt_sym, daily_unit, datalen)
            adjusted = True
        if isinstance(payload, str):
            return network_error(payload, provider=PROVIDER)
        series = parse_kline_payload(
            payload,
            symbol=symbol,
            tencent_symbol=qt_sym,
            period=period,
            unit=minute_unit or daily_unit or "day",
            adjusted=adjusted,
        )
        if isinstance(series, MarketError):
            return series
        bars = series.bars
        if start is not None:
            bars = tuple(b for b in bars if b.ts.date() >= start)
        if end is not None:
            bars = tuple(b for b in bars if b.ts.date() <= end)
        if not bars:
            return series
        return replace(series, bars=bars)
