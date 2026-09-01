"""EastMoneyMarketData：东财 MarketDataPort 实现。"""

from __future__ import annotations

import asyncio
from typing import Literal
from datetime import date, timedelta
from collections.abc import Sequence

from ...enums import RankBy, BoardKind, ValueKind, AssetClass, KlinePeriod
from ...errors import MarketError, not_found, empty_error, unsupported, network_error
from ...models import (
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
from .json_util import opt_float, as_mapping, require_mapping
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
from ....eastmoney import EASTMONEY_REQUESTER
from ....load_data import get_full_security_code
from .parse_intraday import extract_trends_from_payload, parse_intraday_from_trends_list
from ....eastmoney_finance import get_financial_snapshot as _fetch_fin_snapshot
from ....stock.request_utils import get_code_id

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
        code_info = await get_code_id(query)
        if code_info is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
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
        code_info = await get_code_id(query)
        if code_info is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
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
        return parse_intraday_from_trends_list(trends_only, symbol, quote, ndays=days)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        code_info = await get_code_id(query)
        if code_info is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
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
        is_loop = limit is None and market in market_dict
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
        # 旧 draw 依赖原始结构；语义 buckets 暂空
        return BreadthBar(buckets=(), raw=raw)

    async def market_turnover(self) -> MarketTurnover | MarketError:
        from ....stock.request import get_hours_from_em

        prev_amount, amount, ltd = await get_hours_from_em()
        return MarketTurnover(prev_amount=prev_amount, amount=amount, last_trade_date=ltd)

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
        stock = await EASTMONEY_REQUESTER.resolve_stock(query)
        if stock is None:
            return not_found(ErroText["notStock"], provider=PROVIDER)
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
