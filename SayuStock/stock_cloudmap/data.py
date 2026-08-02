"""云图数据编排：只返回 BoardSnapshot，不经 compat 编码。"""

from __future__ import annotations

from typing import List, Union, Optional
from datetime import datetime
from dataclasses import dataclass

from ..utils.market import get_market, is_market_error
from ..utils.constant import ErroText, bk_dict, market_dict
from ..utils.market.models import KlineSeries, BoardSnapshot, IntradaySeries

MarketPayload = BoardSnapshot | IntradaySeries | KlineSeries
CloudMapRawData = Union[MarketPayload, str]


@dataclass
class CloudMapDataResult:
    """云图渲染前的数据聚合结果。"""

    raw_data: CloudMapRawData
    raw_datas: List[IntradaySeries | KlineSeries]
    sector: Optional[str]
    special_cache_key: Optional[str]


class CloudMapDataService:
    """大盘/行业/概念云图数据请求，只走 MarketDataPort。"""

    async def fetch(
        self,
        market: str,
        sector: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> CloudMapDataResult:
        _ = start_time, end_time
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


CLOUDMAP_DATA_SERVICE = CloudMapDataService()
