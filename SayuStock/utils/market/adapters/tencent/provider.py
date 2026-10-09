"""腾讯财经 MarketDataPort：A股票/指数/ETF 备用源；美股仅盘口（K线/分时无数据）。"""

from __future__ import annotations

import asyncio
from datetime import date
from dataclasses import replace
from collections.abc import Sequence

from .parse import (
    parse_qt_line,
    parse_qt_line_us,
    parse_kline_payload,
    parse_minute_payload,
    intraday_from_minute_bars,
    tencent_symbol_from_secid,
)
from .._base import PartialMarketData, resolve_em_symbol, resolve_em_symbol_safe
from .client import PROVIDER, fetch_minute, fetch_mkline, fetch_fqkline, fetch_qt_lines
from ...enums import KlinePeriod
from ...errors import MarketError, not_found, empty_error, unsupported, network_error
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


# 美股：腾讯仅盘口可用；fqkline/mkline/minute 对美股无有效数据（实测
# 日K只回上市首日+当日两根、分钟K param error、分时仅末点），交给注册表回落
def _parse_us_or_cn(line: str, *, qt_sym: str, symbol: SymbolRef) -> Quote | MarketError:
    if qt_sym.startswith("us"):
        return parse_qt_line_us(line, symbol=symbol)
    return parse_qt_line(line, symbol=symbol)


class TencentMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def resolve(self, query: str) -> SymbolRef | None:
        return await resolve_em_symbol_safe(query)

    async def _symbol_of(self, query: str) -> SymbolRef | MarketError:
        from ....stock.request_utils import ResolveLayerError

        try:
            ref = await resolve_em_symbol(query)
        except ResolveLayerError as error:
            # 解析层（东财 searchapi）瞬断 ≠ 标的不存在；报 network 顺延而非 not_found 短路
            return network_error(f"行情ID解析层不可用: {error}", provider=PROVIDER)
        if ref is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
        if tencent_symbol_from_secid(ref.provider_symbol) is None:
            # 港股/期货等暂不覆盖，交给注册表回落
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
            # 符号已映射成功、只是这一行空（占位行/瞬时缺行）≠ 标的不存在。
            # 报 not_found 会短路整条源链，后面的源一个都试不到；
            # 报 empty 让注册表顺延，"没有这只票"由解析层（not_found）负责。
            return empty_error(f"腾讯盘口无数据行: {qt_sym}", provider=PROVIDER)
        return _parse_us_or_cn(line, qt_sym=qt_sym, symbol=symbol)

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
                    # 同 quote()：空行是缺行不是「没这只票」，用 empty 语义
                    results[i] = empty_error(f"腾讯盘口无数据行: {qt_sym}", provider=PROVIDER)
                    continue
                results[i] = _parse_us_or_cn(line, qt_sym=qt_sym, symbol=sym_by_idx[i])
        return [r if r is not None else not_found(ErroText["notStock"], provider=PROVIDER) for r in results]

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        if ndays > 1:
            return await self._intraday_from_m1(query, ndays)
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        qt_sym = tencent_symbol_from_secid(symbol.provider_symbol)
        assert qt_sym is not None
        if qt_sym.startswith("us"):
            return unsupported("腾讯美股不支持分时（仅盘口）", provider=PROVIDER)
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

    async def _intraday_from_m1(self, query: str, ndays: int) -> IntradaySeries | MarketError:
        """五日分时：1 分钟 K 取最近若干交易日。东财 trends2 失败时由源链落到这里。"""
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        qt_sym = tencent_symbol_from_secid(symbol.provider_symbol)
        assert qt_sym is not None
        if qt_sym.startswith("us"):
            return unsupported("腾讯美股不支持分时（仅盘口）", provider=PROVIDER)
        days = 5 if ndays > 5 else ndays
        payload = await fetch_mkline(qt_sym, "m1", min(2000, days * 240 + 30))
        if isinstance(payload, str):
            return network_error(payload, provider=PROVIDER)
        series = parse_kline_payload(
            payload,
            symbol=symbol,
            tencent_symbol=qt_sym,
            period=KlinePeriod.M5,
            unit="m1",
            adjusted=False,
        )
        if isinstance(series, MarketError):
            return series
        quote = await self.quote(query)
        return intraday_from_minute_bars(
            series.bars,
            symbol=symbol,
            quote=None if isinstance(quote, MarketError) else quote,
            ndays=days,
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
        if qt_sym.startswith("us"):
            return unsupported("腾讯美股不支持K线（仅盘口）", provider=PROVIDER)
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
            # 日期窗内一根都没有（本源只回最近 N 根，窗口更早时会被滤空）。
            # 不能把**未过滤**的原序列当答案返回：调用方拿到的日期范围就是错的。
            # 报 empty 交给注册表顺延到能给这个窗口的源。
            return empty_error(f"腾讯 K 线在 {start}~{end} 窗口内无数据", provider=PROVIDER)
        return replace(series, bars=bars)
