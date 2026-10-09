"""EastMoneyMarketData：东财 MarketDataPort 实现。"""

from __future__ import annotations

import asyncio
from typing import Literal
from datetime import date, timedelta
from dataclasses import replace
from collections.abc import Sequence

from gsuid_core.logger import logger

from ...enums import RankBy, BoardKind, IpoMarket, ValueKind, AssetClass, KlinePeriod, coerce_ipo_market
from ...errors import MarketError, not_found, empty_error, unsupported, network_error, is_market_error
from ...models import (
    Quote,
    IpoEvent,
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
from .json_util import opt_float, as_mapping, require_mapping
from .parse_ipo import parse_ipo_apply_payload, parse_ipo_clist_payload
from .map_fields import PROVIDER
from .parse_rank import (
    RANK_SPECS_INTERNAL,
    rank_fields_csv,
    resolve_rank_by,
    parse_rank_payload,
)
from ....constant import ErroText, market_dict
from .parse_board import parse_board_payload
from .parse_kline import parse_kline_payload
from .parse_quote import parse_quote_payload
from .parse_value import parse_value_series_payload
from ....eastmoney import EASTMONEY_REQUESTER, EastMoneyStockItem
from ....load_data import get_full_security_code
from .parse_breadth import parse_breadth_payload
from .parse_intraday import extract_trends_from_payload, parse_intraday_from_trends_list
from ....eastmoney_finance import get_financial_snapshot as _fetch_fin_snapshot

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


def _sec_type_to_asset(sec_type: str, *, secid: str = "") -> AssetClass:
    if "ETF" in sec_type:
        return AssetClass.ETF
    if secid.startswith("150."):
        return AssetClass.FUND
    if "基金" in sec_type:
        return AssetClass.ETF
    if "指数" in sec_type:
        return AssetClass.INDEX
    if "期货" in sec_type:
        return AssetClass.FUTURE
    if "债" in sec_type:
        return AssetClass.BOND
    return AssetClass.EQUITY


def _exchange_of(secid: str, sec_type: str) -> str:
    if secid.startswith("1."):
        return "SSE"
    if "京" in sec_type:
        # 北交所 secid 前缀与深市同为 0.，须先按 sec_type 判定
        return "BSE"
    if secid.startswith("0."):
        return "SZSE"
    if "港" in sec_type or secid.startswith("116."):
        return "HKEX"
    if "美" in sec_type or secid.startswith(("105.", "106.", "107.", "153.")):
        return "US"
    if "韩" in sec_type or secid.startswith("177."):
        return "KRX"
    return "EM"


def _board_kind_for_market(market: str) -> BoardKind:
    if market in ("主要指数",):
        return BoardKind.INDEX
    if market in ("行业板块", "行业"):
        return BoardKind.INDUSTRY
    if market in ("概念板块", "概念"):
        return BoardKind.CONCEPT
    if market in ("沪深A", "stock", "沪A", "深A", "创业板", "科创板"):
        return BoardKind.A_SHARE
    if market in ("国际市场",):
        return BoardKind.INTERNATIONAL
    if market in ("外汇",):
        return BoardKind.FX
    return BoardKind.OTHER


def _is_bk_market(market: str) -> bool:
    text = market.strip()
    if len(text) >= 2 and text[0].lower() == "b" and text[1] == ":":
        text = text[2:]
    core = text.split("+", 1)[0].upper()
    return core.startswith("BK") and len(core) >= 6 and core[2:].isdigit()


def _market_key(kind: BoardKind | str, sector: str | None) -> str:
    if isinstance(kind, str) and kind not in {b.value for b in BoardKind}:
        return kind
    if sector:
        return sector
    if kind == BoardKind.INDUSTRY or kind == "industry":
        return "行业板块"
    if kind == BoardKind.CONCEPT or kind == "concept":
        return "概念板块"
    if kind == BoardKind.INDEX or kind == "index":
        return "主要指数"
    if kind == BoardKind.A_SHARE or kind == "a_share":
        return "沪深A"
    return str(kind.value if isinstance(kind, BoardKind) else kind)


async def _resolve_code(query: str) -> tuple[str, str, str] | MarketError:
    """query → (QuoteID, Name, SecurityTypeName)。

    解析层（东财 searchapi）瞬断时返回 network 错误让注册表顺延其他源，
    而不是误报 not_found 短路整条链。
    """
    from ....stock.request_utils import ResolveLayerError, get_code_id_strict

    try:
        code_info = await get_code_id_strict(query)
    except ResolveLayerError as error:
        return network_error(f"行情ID解析层不可用: {error}", provider=PROVIDER)
    if code_info is None:
        return not_found(ErroText["notStock"], provider=PROVIDER)
    return code_info


async def _resolve_stock_item(query: str) -> EastMoneyStockItem | MarketError:
    """query → EastMoneyStockItem（估值序列等按 ``resolve_stock`` 形状的调用方复用）。

    与 :func:`_resolve_code` 同语义：解析层瞬断返回 network 顺延其他源，
    只有标的不存在才返回 not_found。
    """
    code_info = await _resolve_code(query)
    if isinstance(code_info, MarketError):
        return code_info
    secid = get_full_security_code(code_info[0])
    return {
        "secid": secid,
        "code": secid.split(".")[-1],
        "name": code_info[1] or secid,
        "sec_type": code_info[2],
    }


# 短于行情源 25s 预算，超时只丢掉增强，东财列表已经在手上
_AASTOCKS_TIMEOUT_S = 8.0


class EastMoneyMarketData:
    """东财适配器；HTTP/缓存仍走 EASTMONEY_REQUESTER。"""

    async def resolve(self, query: str) -> SymbolRef | None:
        item = await EASTMONEY_REQUESTER.resolve_stock(query)
        if item is None:
            return None
        return SymbolRef(
            code=item["code"],
            name=item["name"],
            asset_class=_sec_type_to_asset(item["sec_type"], secid=item["secid"]),
            exchange=_exchange_of(item["secid"], item["sec_type"]),
            provider_symbol=item["secid"],
            sec_type=item["sec_type"] or "",
        )

    async def quote(self, query: str) -> Quote | MarketError:
        code_info = await _resolve_code(query)
        if isinstance(code_info, MarketError):
            return code_info
        secid = get_full_security_code(code_info[0])
        sec_type = code_info[2]
        raw = await EASTMONEY_REQUESTER.get_single_stock(secid, sec_type)
        if isinstance(raw, str):
            return (
                not_found(raw, provider=PROVIDER)
                if "找不到" in raw or "未" in raw
                else network_error(raw, provider=PROVIDER)
            )
        return parse_quote_payload(raw, provider_symbol=secid, sec_type=sec_type)

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return list(await asyncio.gather(*[self.quote(q) for q in queries]))

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        code_info = await _resolve_code(query)
        if isinstance(code_info, MarketError):
            return code_info
        secid = get_full_security_code(code_info[0])
        sec_type = code_info[2]
        symbol = SymbolRef(
            code=secid.split(".")[-1],
            name=code_info[1] or secid,
            asset_class=_sec_type_to_asset(sec_type, secid=secid),
            exchange=_exchange_of(secid, sec_type),
            provider_symbol=secid,
            sec_type=sec_type or "",
        )
        days = ndays if ndays > 1 else 1
        if days > 5:
            days = 5
        raw = await EASTMONEY_REQUESTER.get_single_stock(secid, sec_type)
        quote: Quote | None = None
        if not isinstance(raw, str):
            q = parse_quote_payload(raw, provider_symbol=secid, sec_type=sec_type)
            if not isinstance(q, MarketError):
                quote = q
                symbol = q.symbol
            if days == 1:
                trends = extract_trends_from_payload(raw)
                if trends is not None:
                    return parse_intraday_from_trends_list(trends, symbol, quote, ndays=1)

        trends_only = await EASTMONEY_REQUESTER.get_stock_trends(secid, ndays=days)
        series = parse_intraday_from_trends_list(trends_only, symbol, quote, ndays=days)
        if days > 1 and isinstance(series, IntradaySeries) and series.points:
            ref = await self._window_prev_close(query, series.points[0].ts.date())
            if ref is not None:
                series = replace(series, ref_close=ref)
        return series

    async def _window_prev_close(self, query: str, first_day: date) -> float | None:
        """五日窗口第一天之前的收盘，作为累计涨跌 0 轴。"""
        start = first_day - timedelta(days=21)
        kl = await self.kline(query, KlinePeriod.D1, start=start, end=first_day)
        if is_market_error(kl):
            return None
        prev: float | None = None
        for bar in kl.bars:
            if bar.ts.date() < first_day and bar.close != 0:
                prev = bar.close
        return prev

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        code_info = await _resolve_code(query)
        if isinstance(code_info, MarketError):
            return code_info
        secid = get_full_security_code(code_info[0])
        sec_type = code_info[2]
        symbol = SymbolRef(
            code=secid.split(".")[-1],
            name=code_info[1] or secid,
            asset_class=_sec_type_to_asset(sec_type, secid=secid),
            exchange=_exchange_of(secid, sec_type),
            provider_symbol=secid,
            sec_type=sec_type or "",
        )
        klt: str | int = period.value
        if period in (KlinePeriod.D1_RECENT, KlinePeriod.D1_YEAR):
            klt = 101
        end_d = end or date.today()
        if start is None:
            days = _PERIOD_DAYS.get(period, 400)
            start_d = end_d - timedelta(days=days)
        else:
            start_d = start
        st = start_d.strftime("%Y%m%d")
        et = end_d.strftime("%Y%m%d")
        raw = await EASTMONEY_REQUESTER.get_stock_kline(secid, sec_type, klt, st, et)
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        return parse_kline_payload(raw, symbol=symbol, period=period, adjusted=True)

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        market = _market_key(kind, sector)
        po = 1 if sort_asc else 0
        pz = limit if limit is not None else 100
        # BK 不在 market_dict；limit=None 必须翻页，否则一级行业会被截成 100 只
        is_loop = limit is None and (market in market_dict or _is_bk_market(market))
        raw = await EASTMONEY_REQUESTER.get_market_list(market, is_loop=is_loop, po=po, pz=pz)
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        bk = kind if isinstance(kind, BoardKind) else _board_kind_for_market(market)
        snap = parse_board_payload(raw, kind=bk, title=market)
        if isinstance(snap, MarketError):
            return snap
        if limit is not None and len(snap.rows) > limit:
            return BoardSnapshot(kind=snap.kind, title=snap.title, rows=snap.rows[:limit])
        return snap

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError:
        """沪深 A 通用 clist 排行；f* 只在 parse_rank 内解析。"""
        key = resolve_rank_by(rank_by)
        if key is None or key not in RANK_SPECS_INTERNAL:
            return unsupported(f"未知 rank_by={rank_by!r}", provider=PROVIDER)
        spec = RANK_SPECS_INTERNAL[key]
        # 单页上限与 clist pz 一致（最多 100）；质量池 QUALITY_RANK_LIMIT=80 依赖此上限
        lim = max(1, min(int(limit), 100))
        use_high_first = spec.default_high_first if high_first is None else bool(high_first)
        po = 0 if use_high_first else 1
        fs = market_dict["沪深A"] if "沪深A" in market_dict else "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = [
            ("pz", str(min(100, max(lim, 40)))),
            ("po", str(po)),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", spec.sort_fid),
            ("pn", "1"),
            ("fs", fs),
            ("fields", rank_fields_csv(spec)),
        ]
        raw = await EASTMONEY_REQUESTER.stock_request(url, "GET", params=params)
        if isinstance(raw, int):
            return network_error(f"rank clist 失败: {raw}", provider=PROVIDER)
        return parse_rank_payload(raw, spec=spec, high_first=use_high_first, limit=lim)

    async def hotmap(self) -> BoardSnapshot | MarketError:
        raw = await EASTMONEY_REQUESTER.get_hotmap()
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        return parse_board_payload(raw, kind=BoardKind.HOTMAP, title="大盘云图")

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        mode = 2 if kind == "industry" else 3
        try:
            return await EASTMONEY_REQUESTER.get_menu(mode)
        except RuntimeError as e:
            return network_error(str(e), provider=PROVIDER)

    async def breadth(self) -> BreadthBar | MarketError:
        from ....stock.request import get_bar

        raw = await get_bar()
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        return parse_breadth_payload(raw)

    async def market_turnover(self) -> MarketTurnover | MarketError:
        from ....stock.request import get_hours_from_em

        # get_hours_from_em 的返回是 (今日成交额, 今日-昨日, 日期)，不是 (昨, 今, 日期)：
        # calculate_difference 回的是 (all_today_data, today - yesterday, actual_date)。
        # 名字像「昨日」的 ya 其实是**今日成交额**，昨日要用 今日-差值 反解。
        today_amount, diff, ltd = await get_hours_from_em()
        # trends2 失败时它只 warning 再 continue，两个市场都挂就回 (0, 0, None)。
        # 不能把这种「拉取失败」当成「成交额 0 亿」上报：那样请求链不会顺延，
        # 大盘概览会在东财限流期间静默显示 0 亿（实测 -400016 时就是这个表现）。
        if today_amount <= 0:
            return network_error("两市成交额拉取失败（trends2 无有效数据）", provider=PROVIDER)
        return MarketTurnover(prev_amount=today_amount - diff, amount=today_amount, last_trade_date=ltd)

    async def northbound(self) -> NorthboundFlow | MarketError:
        url = "https://push2.eastmoney.com/api/qt/kamt/get"
        raw = await EASTMONEY_REQUESTER.stock_request(url)
        if isinstance(raw, int):
            return network_error(f"北向请求失败: {raw}", provider=PROVIDER)
        root = as_mapping(raw)
        if root is None:
            return empty_error("北向响应无效", provider=PROVIDER)
        data = require_mapping(root, "data")
        if data is None:
            return empty_error("北向 data 为空", provider=PROVIDER)
        sh = opt_float(data, "f55")
        sz = opt_float(data, "f56")
        if sh is None or sz is None:
            return empty_error("北向字段缺失", provider=PROVIDER)
        # 接口单位：万元 → 亿元
        return NorthboundFlow(sh_net_yi=sh / 10000.0, sz_net_yi=sz / 10000.0)

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError:
        stock = await _resolve_stock_item(query)
        if isinstance(stock, MarketError):
            return stock
        if kind == ValueKind.PE:
            raw = await EASTMONEY_REQUESTER.get_pe_series(stock)
        elif kind == ValueKind.PB:
            raw = await EASTMONEY_REQUESTER.get_pb_series(stock)
        elif kind == ValueKind.DY:
            raw = await EASTMONEY_REQUESTER.get_dy_series(stock)
        else:
            return unsupported(f"未知估值类型 {kind}", provider=PROVIDER)
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        return parse_value_series_payload(raw, kind=kind)

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError:
        pure = code.split(".")[-1]
        snap = await _fetch_fin_snapshot(pure)
        if not snap:
            return empty_error("无财务快照", provider=PROVIDER)
        # eastmoney_finance 已映射为语义键
        industry = (
            snap["industry_type"]
            if "industry_type" in snap
            else (snap["_industry_type"] if "_industry_type" in snap else "standard")
        )
        if industry not in ("standard", "bank"):
            industry = "standard"
        gap_raw = snap["_gap"] if "_gap" in snap and isinstance(snap["_gap"], list) else []
        missing = tuple(str(x) for x in gap_raw)
        return FinancialSnapshot(
            code=pure,
            report_date=str(snap["report_date"]) if "report_date" in snap and snap["report_date"] else "",
            roe=float(snap["roe"]) if "roe" in snap and isinstance(snap["roe"], (int, float)) else None,
            revenue_yoy=(
                float(snap["revenue_yoy"])
                if "revenue_yoy" in snap and isinstance(snap["revenue_yoy"], (int, float))
                else None
            ),
            profit_yoy=(
                float(snap["profit_yoy"])
                if "profit_yoy" in snap and isinstance(snap["profit_yoy"], (int, float))
                else None
            ),
            gross_margin=(
                float(snap["gross_margin"])
                if "gross_margin" in snap and isinstance(snap["gross_margin"], (int, float))
                else None
            ),
            net_margin=(
                float(snap["net_margin"])
                if "net_margin" in snap and isinstance(snap["net_margin"], (int, float))
                else None
            ),
            debt_ratio=(
                float(snap["debt_ratio"])
                if "debt_ratio" in snap and isinstance(snap["debt_ratio"], (int, float))
                else None
            ),
            eps=float(snap["eps"]) if "eps" in snap and isinstance(snap["eps"], (int, float)) else None,
            bps=float(snap["bps"]) if "bps" in snap and isinstance(snap["bps"], (int, float)) else None,
            net_interest_margin=(
                float(snap["net_interest_margin"])
                if "net_interest_margin" in snap and isinstance(snap["net_interest_margin"], (int, float))
                else None
            ),
            industry_type="bank" if industry == "bank" else "standard",
            missing_fields=missing,
        )

    async def ipo_calendar(self, market: IpoMarket | str) -> list[IpoEvent] | MarketError:
        m = coerce_ipo_market(market)
        if m is None:
            return unsupported(f"未知 IPO 市场 {market!r}", provider=PROVIDER)
        if m == IpoMarket.CN:
            raw = await EASTMONEY_REQUESTER.get_cn_ipo_apply()
            if isinstance(raw, str):
                return network_error(raw, provider=PROVIDER)
            return parse_ipo_apply_payload(raw)
        if m == IpoMarket.HK:
            parsed = await self._hk_from_clist()
            if isinstance(parsed, MarketError):
                fallback = await self._hk_ipo_fallback()
                if fallback is not None:
                    return fallback
                return parsed
            return await self._enrich_hk_ipo(parsed)
        raw = await EASTMONEY_REQUESTER.get_ipo_clist("us")
        if isinstance(raw, str):
            return network_error(raw, provider=PROVIDER)
        return parse_ipo_clist_payload(raw, m)

    async def _hk_from_clist(self) -> list[IpoEvent] | MarketError:
        """主板、创业板分开查，板块由查询参数决定，不看 f13。"""
        specs = (("main", "港交所主板"), ("gem", "港交所创业板"))
        raws = await asyncio.gather(*(EASTMONEY_REQUESTER.get_ipo_clist("hk", board=key) for key, _ in specs))
        events: list[IpoEvent] = []
        saw_network = False
        for (_, label), raw in zip(specs, raws, strict=True):
            if isinstance(raw, str):
                saw_network = True
                continue
            parsed = parse_ipo_clist_payload(raw, IpoMarket.HK, board=label)
            if isinstance(parsed, MarketError):
                continue
            events.extend(parsed)
        if events:
            return events
        if saw_network:
            return network_error("东财港股IPO列表不可用", provider=PROVIDER)
        return empty_error("港股 IPO上市列表为空", provider=PROVIDER)

    async def _aastocks_pages(self) -> tuple[str, str]:
        """限时抓两页。超时只放弃增强，不把已解析的东财列表一起取消。"""
        from ..aastocks import fetch_mainpage, fetch_upcoming

        try:
            main_html, up_html = await asyncio.wait_for(
                asyncio.gather(fetch_mainpage(), fetch_upcoming()),
                timeout=_AASTOCKS_TIMEOUT_S,
            )
        except TimeoutError:
            logger.warning("[SayuStock][行情API] AAStocks 超时，沿用东财港股列表")
            return "", ""
        return main_html, up_html

    async def _hk_ipo_fallback(self) -> list[IpoEvent] | None:
        """东财港股列表不可达时的 AAStocks 降级数据（不含 GEM/介绍上市，名称为繁体）。"""
        from ..aastocks.parse import HkIpoExtra, parse_mainpage, parse_upcoming, hk_ipo_events_from_extras

        main_html, up_html = await self._aastocks_pages()
        extras: dict[str, HkIpoExtra] = {}
        if main_html:
            extras.update(parse_mainpage(main_html))
        if up_html:
            for code, extra in parse_upcoming(up_html).items():
                base = extras.get(code)
                if base is None:
                    extras[code] = extra
                else:
                    base.grey_market_date = base.grey_market_date or extra.grey_market_date
                    base.apply_end_date = base.apply_end_date or extra.apply_end_date
                    base.listing_date = base.listing_date or extra.listing_date
        events = hk_ipo_events_from_extras(extras)
        if not events:
            return None
        logger.warning(
            f"[SayuStock][行情API] 东财港股列表不可用，改用 AAStocks（{len(events)} 只，不含 GEM/介绍上市，名称为繁体）"
        )
        return events

    async def _enrich_hk_ipo(self, events: list[IpoEvent]) -> list[IpoEvent]:
        """AAStocks 补招股截止/暗盘/上市价/超购/首日表现。超时或空页保留原列表。"""
        from ..aastocks import parse_mainpage, parse_upcoming, merge_hk_ipo_extra

        main_html, up_html = await self._aastocks_pages()
        if main_html:
            events = merge_hk_ipo_extra(events, parse_mainpage(main_html))
        if up_html:
            events = merge_hk_ipo_extra(events, parse_upcoming(up_html))
        return events
