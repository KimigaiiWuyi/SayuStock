"""新浪财经 MarketDataPort：A股票/指数/ETF 的备用权益行情源。"""

from __future__ import annotations

import asyncio
from typing import Literal
from datetime import date
from dataclasses import replace
from collections.abc import Sequence

from .parse import (
    SINA_RANK_SORT,
    industry_menu,
    parse_hq_line,
    parse_rank_rows,
    parse_kline_rows,
    parse_node_board,
    node_for_industry,
    parse_minline_rows,
    parse_industry_summary,
    sina_symbol_from_secid,
)
from .._base import PartialMarketData, resolve_em_symbol
from .client import PROVIDER, fetch_kline, fetch_minline, fetch_hq_lines, fetch_node_rows, fetch_industry_summary
from ...enums import RankBy, BoardKind, KlinePeriod, resolve_rank_by
from ...errors import MarketError, not_found, unsupported, network_error
from ...models import (
    Quote,
    SymbolRef,
    KlineSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradaySeries,
)
from ....constant import ErroText

# K线周期 → 新浪 scale；240=日K。周/月/季/年新浪无对应接口。
_PERIOD_SCALE: dict[KlinePeriod, int] = {
    KlinePeriod.M5: 5,
    KlinePeriod.M15: 15,
    KlinePeriod.M30: 30,
    KlinePeriod.M60: 60,
    KlinePeriod.D1: 240,
    KlinePeriod.D1_RECENT: 240,
    KlinePeriod.D1_YEAR: 240,
}

# 各周期默认拉取的 bar 数上限（新浪 datalen 上限约 2000，保守取 1023）
_PERIOD_BARS: dict[KlinePeriod, int] = {
    KlinePeriod.M5: 1023,
    KlinePeriod.M15: 1023,
    KlinePeriod.M30: 1023,
    KlinePeriod.M60: 1023,
    KlinePeriod.D1_RECENT: 60,
    KlinePeriod.D1: 500,
    KlinePeriod.D1_YEAR: 365,
}


class SinaMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def resolve(self, query: str) -> SymbolRef | None:
        return await resolve_em_symbol(query)

    async def _symbol_of(self, query: str) -> SymbolRef | MarketError:
        ref = await self.resolve(query)
        if ref is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
        if sina_symbol_from_secid(ref.provider_symbol) is None:
            # 港美股/期货等新浪 hq 接口不覆盖，交给注册表回落
            return unsupported(f"新浪不支持 {ref.provider_symbol}", provider=PROVIDER)
        return ref

    async def quote(self, query: str) -> Quote | MarketError:
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        sina_sym = sina_symbol_from_secid(symbol.provider_symbol)
        assert sina_sym is not None
        lines = await fetch_hq_lines([sina_sym])
        if isinstance(lines, MarketError):
            return lines
        line = lines.get(sina_sym)
        if line is None or not line.strip():
            return not_found(ErroText["notStock"], provider=PROVIDER)
        return parse_hq_line(line, symbol=symbol)

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        symbols = await asyncio.gather(*[self._symbol_of(q) for q in queries])
        sym_by_idx: dict[int, SymbolRef] = {}
        sina_by_idx: dict[int, str] = {}
        results: list[Quote | MarketError | None] = [None] * len(queries)
        for i, symbol in enumerate(symbols):
            if isinstance(symbol, MarketError):
                results[i] = symbol
                continue
            sina_sym = sina_symbol_from_secid(symbol.provider_symbol)
            if sina_sym is None:
                results[i] = unsupported(f"新浪不支持 {symbol.provider_symbol}", provider=PROVIDER)
                continue
            sym_by_idx[i] = symbol
            sina_by_idx[i] = sina_sym
        if sina_by_idx:
            lines = await fetch_hq_lines(list(sina_by_idx.values()))
            for i, sina_sym in sina_by_idx.items():
                if isinstance(lines, MarketError):
                    results[i] = lines
                    continue
                line = lines.get(sina_sym)
                if line is None or not line.strip():
                    results[i] = not_found(ErroText["notStock"], provider=PROVIDER)
                    continue
                results[i] = parse_hq_line(line, symbol=sym_by_idx[i])
        return [r if r is not None else not_found(ErroText["notStock"], provider=PROVIDER) for r in results]

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        if ndays > 1:
            return unsupported("新浪仅支持当日分时", provider=PROVIDER)
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        sina_sym = sina_symbol_from_secid(symbol.provider_symbol)
        assert sina_sym is not None
        quote = await self.quote(query)
        rows = await fetch_minline(sina_sym)
        if isinstance(rows, str):
            return network_error(rows, provider=PROVIDER)
        trade_date = date.today().strftime("%Y-%m-%d")
        if not isinstance(quote, MarketError) and quote.as_of is not None:
            trade_date = quote.as_of.strftime("%Y-%m-%d")
        return parse_minline_rows(
            rows,
            symbol=symbol,
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
        scale = _PERIOD_SCALE.get(period)
        if scale is None:
            return unsupported(f"新浪不支持 {period} K线", provider=PROVIDER)
        symbol = await self._symbol_of(query)
        if isinstance(symbol, MarketError):
            return symbol
        sina_sym = sina_symbol_from_secid(symbol.provider_symbol)
        assert sina_sym is not None
        datalen = _PERIOD_BARS.get(period, 400)
        if start is not None:
            end_d = end or date.today()
            datalen = max(datalen, (end_d - start).days)
        rows = await fetch_kline(sina_sym, scale, min(datalen, 1900))
        if isinstance(rows, str):
            return network_error(rows, provider=PROVIDER)
        series = parse_kline_rows(rows, symbol=symbol, period=period)
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

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        key = sector or (kind.value if isinstance(kind, BoardKind) else str(kind))
        # 行业板块汇总表自带全行业
        if key in ("行业板块", "行业", "industry"):
            payload = await fetch_industry_summary()
            if isinstance(payload, str):
                return network_error(payload, provider=PROVIDER)
            return parse_industry_summary(payload, kind=BoardKind.INDUSTRY, title="行业板块")
        node: str | None = None
        title = key
        board_kind = BoardKind.OTHER
        if key in ("沪深A", "stock", "沪A", "深A", "创业板", "科创板", "a_share"):
            node = "hs_a"
            title = "沪深A"
            board_kind = BoardKind.A_SHARE
        elif key in ("主要指数", "index"):
            node = "hs_s"
            title = "主要指数"
            board_kind = BoardKind.INDEX
        elif sector:
            # 行业成分：sector 为行业名或新浪节点（new_xxxx）
            menu_payload = await fetch_industry_summary()
            if isinstance(menu_payload, str):
                return network_error(menu_payload, provider=PROVIDER)
            menu = industry_menu(menu_payload)
            if isinstance(menu, MarketError):
                return menu
            node = node_for_industry(menu, sector)
            if node is None:
                return unsupported(f"新浪不支持板块 {sector}", provider=PROVIDER)
            board_kind = BoardKind.INDUSTRY
            title = sector
        else:
            return unsupported(f"新浪不支持列表 {key}", provider=PROVIDER)
        rows = await fetch_node_rows(node, sort="changepercent", asc=sort_asc, limit=limit)
        if isinstance(rows, MarketError):
            return rows
        return parse_node_board(rows, kind=board_kind, title=title, limit=limit)

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError:
        key = resolve_rank_by(rank_by)
        if key is None or key not in SINA_RANK_SORT:
            return unsupported(f"新浪不支持排行 {rank_by!r}", provider=PROVIDER)
        use_high_first = True if high_first is None else bool(high_first)
        lim = max(1, min(int(limit), 100))
        rows = await fetch_node_rows("hs_a", sort=SINA_RANK_SORT[key], asc=not use_high_first, limit=lim)
        if isinstance(rows, MarketError):
            return rows
        return parse_rank_rows(rows, rank_by=key, high_first=use_high_first, limit=lim)

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        if kind != "industry":
            return unsupported("新浪仅支持行业板块菜单", provider=PROVIDER)
        payload = await fetch_industry_summary()
        if isinstance(payload, str):
            return network_error(payload, provider=PROVIDER)
        return industry_menu(payload)
