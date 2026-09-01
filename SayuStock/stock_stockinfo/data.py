"""个股/云图数据编排：只返回领域模型，不经 compat 编码。"""

from __future__ import annotations

import asyncio
from typing import List, Union, Optional
from datetime import datetime
from dataclasses import dataclass

from gsuid_core.logger import logger

from ..utils.market import (
    AssetClass,
    KlinePeriod,
    get_market,
    is_market_error,
    maybe_otc_fund_query,
)
from ..utils.constant import ErroText, bk_dict, market_dict
from ..utils.market.models import SymbolRef, KlineSeries, BoardSnapshot, IntradaySeries

MarketPayload = BoardSnapshot | IntradaySeries | KlineSeries
CloudMapRawData = Union[MarketPayload, str]


@dataclass
class CloudMapDataResult:
    """渲染前数据聚合：payload 为领域模型或错误文本。"""

    raw_data: CloudMapRawData
    raw_datas: List[IntradaySeries | KlineSeries]
    sector: Optional[str]
    special_cache_key: Optional[str]


class CloudMapDataService:
    """根据命令参数组织 MarketDataPort 请求，不解析供应商原始字段。"""

    async def fetch(
        self,
        market: str,
        sector: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> CloudMapDataResult:
        resolved_sector = self.resolve_sector(market, sector)
        raw_datas: List[IntradaySeries | KlineSeries] = []
        special_cache_key: Optional[str] = None
        port = get_market()

        if market == "大盘云图":
            if resolved_sector:
                snap = await port.board(resolved_sector, limit=None, sort_asc=False)
            else:
                snap = await port.hotmap()
            raw_data: CloudMapRawData = snap.message if is_market_error(snap) else snap
        elif market == "行业云图":
            snap = await port.hotmap()
            raw_data = snap.message if is_market_error(snap) else snap
        elif market == "概念云图":
            if resolved_sector:
                resolved_sector, raw_data = await self.fetch_concept(resolved_sector)
            else:
                raw_data = "概念云图需要后跟概念类型, 例如： 概念云图 华为欧拉"
        elif resolved_sector and resolved_sector.startswith("single-stock-kline"):
            if await self._should_compare_otc_fund(market):
                return await self._as_compare_result(market, start_time, end_time)
            raw_data = await self._fetch_kline(market, resolved_sector, start_time, end_time)
            if isinstance(raw_data, KlineSeries) and self._is_otc_fund_kline(raw_data):
                tokens = self._split_queries(market)
                if len(tokens) <= 1:
                    return self._compare_result_from_series([raw_data], start_time, end_time)
                return await self._as_compare_result(market, start_time, end_time)
        elif resolved_sector == "compare-stock":
            return await self._as_compare_result(market, start_time, end_time)
        elif resolved_sector == "single-stock":
            raw_data, single_datas = await self.fetch_single_stock_group(market, start_time, end_time)
            raw_datas = list[IntradaySeries | KlineSeries](single_datas)
        else:
            snap = await port.board(market, limit=100, sort_asc=False)
            raw_data = snap.message if is_market_error(snap) else snap

        return CloudMapDataResult(raw_data, raw_datas, resolved_sector, special_cache_key)

    def resolve_sector(self, market: str, sector: Optional[str]) -> Optional[str]:
        if sector != "single-stock":
            if market in market_dict and "b:" in market_dict[market]:
                return market
            if market in bk_dict:
                return market
        return sector

    async def fetch_concept(self, sector: str) -> tuple[str, CloudMapRawData]:
        port = get_market()
        upper_sector = sector.upper()
        menu = await port.sector_menu("concept")
        if is_market_error(menu):
            return upper_sector, menu.message
        if upper_sector in menu:
            snap = await port.board(str(menu[upper_sector]), limit=None, sort_asc=False)
            return upper_sector, snap.message if is_market_error(snap) else snap
        for concept_name, code in menu.items():
            if upper_sector in concept_name:
                snap = await port.board(str(code), limit=None, sort_asc=False)
                return concept_name, snap.message if is_market_error(snap) else snap
        return upper_sector, ErroText["typemap"]

    @staticmethod
    def _split_queries(market: str) -> List[str]:
        return [item.strip() for item in market.replace("，", " ").replace(",", " ").split() if item.strip()]

    @staticmethod
    def _is_plain_code(query: str) -> bool:
        text = query.strip()
        if text.count(".") == 1:
            left, right = text.split(".", 1)
            return left.isdigit() and right.isdigit()
        return text.isdigit() and len(text) == 6

    async def _should_compare_otc_fund(self, market: str) -> bool:
        port = get_market()
        for token in self._split_queries(market):
            if not maybe_otc_fund_query(token):
                continue
            ref = await port.resolve(token)
            if ref is not None and self._is_otc_fund_ref(ref):
                return True
        return False

    @staticmethod
    def _is_otc_fund_ref(ref: SymbolRef) -> bool:
        return ref.asset_class == AssetClass.FUND or ref.provider_symbol.startswith("150.")

    @staticmethod
    def _is_otc_fund_kline(series: KlineSeries) -> bool:
        return series.symbol.asset_class == AssetClass.FUND or series.symbol.provider_symbol.startswith("150.")

    def _compare_result_from_series(
        self,
        series_list: List[KlineSeries],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> CloudMapDataResult:
        if not series_list:
            return CloudMapDataResult(ErroText["notData"], [], "compare-stock", None)
        st_f = start_time.strftime("%Y%m%d") if start_time else ""
        et_f = end_time.strftime("%Y%m%d") if end_time else ""
        raw_datas = list[IntradaySeries | KlineSeries](series_list)
        return CloudMapDataResult(series_list[0], raw_datas, "compare-stock", f"compare-stock-{st_f}-{et_f}")

    async def _as_compare_result(
        self,
        market: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> CloudMapDataResult:
        raw_data, compare_datas = await self.fetch_compare_stocks(market, start_time, end_time)
        if isinstance(raw_data, str):
            return CloudMapDataResult(raw_data, [], "compare-stock", None)
        return self._compare_result_from_series(compare_datas, start_time, end_time)

    async def _fetch_kline(
        self,
        market: str,
        sector: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> CloudMapRawData:
        kline_code = sector.split("-")[-1]
        try:
            period = KlinePeriod(kline_code)
        except ValueError:
            period = KlinePeriod.D1
        start_d = start_time.date() if start_time is not None else None
        end_d = end_time.date() if end_time is not None else None
        series = await get_market().kline(market, period, start=start_d, end=end_d)
        return series.message if is_market_error(series) else series

    async def fetch_compare_stocks(
        self,
        market: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> tuple[CloudMapRawData, List[KlineSeries]]:
        markets = [item.strip() for item in market.replace("，", " ").replace(",", " ").split() if item.strip()]
        expanded: List[str] = []
        for item in markets:
            if item in {"个股对比", "对比个股", "个股", "对比"}:
                continue
            query = "A500ETF" if item == "A500" else item
            codes = await self._fetch_sector_codes_by_query(query)
            if codes:
                expanded.extend(codes)
            else:
                expanded.append(query)

        results: List[KlineSeries] = []
        start_d = start_time.date() if start_time is not None else None
        end_d = end_time.date() if end_time is not None else None
        port = get_market()
        for query in expanded:
            # 默认窗口与历史一致：未指定时间时走 D1_YEAR（约 365 自然日）
            # 重构时曾误写成 D1_RECENT（仅 50 天），用户会感觉像「最近一个月」
            series = await port.kline(query, KlinePeriod.D1_YEAR, start=start_d, end=end_d)
            if is_market_error(series):
                continue
            results.append(series)
        if not results:
            return ErroText["notData"], []
        return results[0], results

    async def _fetch_sector_codes_by_query(self, query: str) -> List[str]:
        port = get_market()
        menu = await port.sector_menu("industry")
        if not is_market_error(menu) and query in menu:
            snap = await port.board(str(menu[query]), limit=13, sort_asc=False)
            if not is_market_error(snap):
                return [r.code for r in snap.rows if r.code]
        menu_c = await port.sector_menu("concept")
        if not is_market_error(menu_c) and query in menu_c:
            snap = await port.board(str(menu_c[query]), limit=13, sort_asc=False)
            if not is_market_error(snap):
                return [r.code for r in snap.rows if r.code]
        if self._is_plain_code(query):
            return []
        q = await port.quote(query)
        if not is_market_error(q) and "(板块)" in q.symbol.name:
            snap = await port.board(q.symbol.code or query, limit=13, sort_asc=False)
            if not is_market_error(snap):
                return [r.code for r in snap.rows if r.code]
        return []

    async def _fetch_sector_codes(self, board_code: str) -> List[str]:
        snap = await get_market().board(board_code, limit=13, sort_asc=False)
        if is_market_error(snap):
            return []
        return [r.code for r in snap.rows if r.code]

    @staticmethod
    def _is_sector_series(series: IntradaySeries) -> bool:
        name = series.symbol.name or ""
        if series.quote is not None and series.quote.symbol.name:
            name = series.quote.symbol.name
        return "(板块)" in name

    async def _fetch_sector_stocks(
        self,
        board_code: str,
    ) -> tuple[CloudMapRawData, List[IntradaySeries]]:
        codes = await self._fetch_sector_codes(board_code)
        if not codes:
            return ErroText["notData"], []

        port = get_market()
        results = await asyncio.gather(*[port.intraday(code) for code in codes])
        valid: List[IntradaySeries] = []
        for item in results:
            if is_market_error(item):
                continue
            if item.points:
                valid.append(item)
        if not valid:
            return ErroText["notData"], []
        return valid[0], valid

    async def fetch_single_stock_group(
        self,
        market: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> tuple[CloudMapRawData, List[IntradaySeries]]:
        _ = start_time, end_time
        port = get_market()
        market_list = market.split(" ")
        if len(market_list) == 1:
            series = await port.intraday(market_list[0])
            logger.info(f"[SayuStock] 单股结果 {market_list[0]}: type={type(series).__name__}")
            if is_market_error(series):
                return series.message, []
            if self._is_sector_series(series):
                board_code = series.symbol.code or market_list[0]
                return await self._fetch_sector_stocks(board_code)
            return series, []

        tasks = [port.intraday(item) for item in market_list]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        valid_results: List[IntradaySeries] = []
        for idx, item in enumerate(gathered):
            query_name = market_list[idx] if idx < len(market_list) else f"#{idx}"
            if isinstance(item, BaseException):
                logger.error(f"[SayuStock] 多股查询第{idx + 1}只标的[{query_name}]异常: {item!r}")
                continue
            if is_market_error(item):
                logger.warning(f"[SayuStock] 多股查询跳过失败标的[{query_name}]: {item.message}")
                continue
            if not isinstance(item, IntradaySeries):
                logger.warning(
                    f"[SayuStock] 多股查询第{idx + 1}只标的[{query_name}]结果类型异常: type={type(item).__name__}"
                )
                continue
            valid_results.append(item)
        if not valid_results:
            return ErroText["notData"], []
        return valid_results[0], valid_results


CLOUDMAP_DATA_SERVICE = CloudMapDataService()
