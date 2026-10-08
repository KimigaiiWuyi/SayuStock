"""同花顺金融数据 API（扶摇）MarketDataPort：A股（含北交所）/指数/ETF 的盘口与前复权日K。

分时与分钟K的高频动向接口「暂未开放外部接入」→ unsupported 交给注册表回落；
板块/排行/云图等东财独占接口同样回落东财。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone, timedelta
from collections.abc import Sequence

from .parse import parse_snapshot_item, parse_historical_bars, ths_symbol_from_secid
from .._base import PartialMarketData, resolve_em_symbol, resolve_em_symbol_safe
from .client import (
    PROVIDER,
    fetch_fund_snapshot,
    fetch_index_snapshot,
    fetch_stock_snapshot,
    fetch_fund_historical,
    fetch_index_historical,
    fetch_stock_historical,
)
from ...enums import KlinePeriod
from ...errors import MarketError, not_found, empty_error, parse_error, unsupported, network_error
from ...models import Quote, SymbolRef, KlineSeries
from ....constant import ErroText

_BJ_TZ = timezone(timedelta(hours=8))

# 日级周期 → 默认回看天数（扶摇按时间窗口取数，无根数参数）
_DAILY_WINDOW_DAYS: dict[KlinePeriod, int] = {
    KlinePeriod.D1: 800,  # ≈500 根日K
    KlinePeriod.D1_RECENT: 120,  # ≈60 根
    KlinePeriod.D1_YEAR: 550,  # ≈365 根
}
# 各端点日K窗口上限：个股/指数 10 年，场内基金 5 个自然年
_MAX_WINDOW_DAYS: dict[str, int] = {"stock": 3650, "index": 3650, "fund": 1825}
# 个股快照批量分片上限（防 URL 过长）
_SNAPSHOT_CHUNK = 50


def _bj_midnight_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_BJ_TZ).timestamp() * 1000)


def _end_of_day_ms(d: date) -> int:
    return _bj_midnight_ms(d + timedelta(days=1)) - 1


class THSMarketData(PartialMarketData):
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
        if ths_symbol_from_secid(ref.provider_symbol, ref.asset_class) is None:
            # 美股/港股/场外基金等不覆盖，交给注册表回落
            return unsupported(f"同花顺不支持 {ref.provider_symbol}", provider=PROVIDER)
        return ref

    async def quote(self, query: str) -> Quote | MarketError:
        results = await self.quotes([query])
        return results[0]

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        symbols = await asyncio.gather(*[self._symbol_of(q) for q in queries])
        results: list[Quote | MarketError | None] = [None] * len(queries)
        # idx → thscode，按端点类别分组批量拉取
        stock_codes: dict[int, str] = {}
        index_codes: dict[int, str] = {}
        fund_codes: dict[int, str] = {}
        for i, symbol in enumerate(symbols):
            if isinstance(symbol, MarketError):
                results[i] = symbol
                continue
            mapped = ths_symbol_from_secid(symbol.provider_symbol, symbol.asset_class)
            assert mapped is not None
            thscode, category = mapped
            if category == "stock":
                stock_codes[i] = thscode
            elif category == "index":
                index_codes[i] = thscode
            else:
                fund_codes[i] = thscode
        items = list(stock_codes.items())
        for chunk_start in range(0, len(items), _SNAPSHOT_CHUNK):
            chunk = dict(items[chunk_start : chunk_start + _SNAPSHOT_CHUNK])
            payload = await fetch_stock_snapshot(list(chunk.values()))
            _fill_quote_results(payload, chunk, symbols, results)
        if index_codes:
            payload = await fetch_index_snapshot(list(index_codes.values()))
            _fill_quote_results(payload, index_codes, symbols, results)
        if fund_codes:
            # 场内基金快照仅支持单只，并发拉取
            payloads = await asyncio.gather(*[fetch_fund_snapshot(t) for t in fund_codes.values()])
            for (i, thscode), payload in zip(fund_codes.items(), payloads):
                _fill_quote_results(payload, {i: thscode}, symbols, results)
        return [r if r is not None else empty_error("同花顺无该标的快照", provider=PROVIDER) for r in results]

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        window_days = _DAILY_WINDOW_DAYS.get(period)
        if window_days is None:
            return unsupported(f"同花顺不支持 {period} K线（仅日线）", provider=PROVIDER)
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        mapped = ths_symbol_from_secid(symbol.provider_symbol, symbol.asset_class)
        assert mapped is not None
        thscode, category = mapped
        end_d = end or date.today()
        start_d = start or (end_d - timedelta(days=window_days))
        if start_d > end_d:
            start_d = end_d
        max_days = _MAX_WINDOW_DAYS[category]
        if (end_d - start_d).days > max_days:
            start_d = end_d - timedelta(days=max_days)
        start_ms = _bj_midnight_ms(start_d)
        end_ms = _end_of_day_ms(end_d)
        if category == "stock":
            payload = await fetch_stock_historical(thscode, start_ms, end_ms)
        elif category == "index":
            payload = await fetch_index_historical(thscode, start_ms, end_ms)
        else:
            payload = await fetch_fund_historical(thscode, start_ms, end_ms)
        if isinstance(payload, MarketError):
            return payload
        if not isinstance(payload, dict):
            return parse_error("同花顺K线 data 非对象", provider=PROVIDER)
        bars = parse_historical_bars(payload)
        if not bars:
            return empty_error("同花顺K线解析后为空", provider=PROVIDER)
        # 个股/ETF 前复权；指数无复权语义，与东财/腾讯日K口径一致标记 True
        return KlineSeries(symbol=symbol, period=period, bars=tuple(bars), adjusted=True)


def _fill_quote_results(
    payload: object,
    code_by_idx: dict[int, str],
    symbols: Sequence[SymbolRef | MarketError],
    results: list[Quote | MarketError | None],
) -> None:
    """把一批快照 payload 按 thscode 回填到对应下标的结果槽位。"""
    if isinstance(payload, MarketError):
        for i in code_by_idx:
            results[i] = payload
        return
    if not isinstance(payload, dict):
        for i in code_by_idx:
            results[i] = parse_error("同花顺快照 data 非对象", provider=PROVIDER)
        return
    by_code: dict[str, dict] = {}
    items = payload.get("item")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("thscode"), str):
                by_code[item["thscode"]] = item
    ts_ms = payload.get("timestamp")
    for i, thscode in code_by_idx.items():
        item = by_code.get(thscode)
        if item is None:
            results[i] = empty_error(f"同花顺快照缺少 {thscode}", provider=PROVIDER)
            continue
        symbol = symbols[i]
        assert not isinstance(symbol, MarketError)
        results[i] = parse_snapshot_item(item, symbol=symbol, timestamp_ms=ts_ms)
