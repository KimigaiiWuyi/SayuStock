import asyncio
from typing import Any, Dict, List, Union, Optional
from datetime import datetime
from dataclasses import dataclass

from ..utils.utils import get_vix_name
from ..utils.constant import ErroText, bk_dict, market_dict
from ..utils.eastmoney import EASTMONEY_REQUESTER
from ..utils.stock.request import get_gg, get_vix

CloudMapRawData = Union[Dict[str, Any], str]


@dataclass
class CloudMapDataResult:
    """云图渲染前的数据聚合结果。

    Attributes:
        raw_data: 主数据源响应。普通云图和单个股票使用该字段。
        raw_datas: 多标的分时或 K 线对比使用的数据列表。
        sector: 经过板块别名、概念菜单解析后的最终 sector。
        special_cache_key: 需要额外区分缓存文件时使用的字符串。
    """

    raw_data: CloudMapRawData
    raw_datas: List[Dict[str, Any]]
    sector: Optional[str]
    special_cache_key: Optional[str]


class CloudMapDataService:
    """stock_cloudmap 数据请求编排服务。

    该服务只负责根据命令参数组织数据请求，不包含 Plotly 渲染逻辑。东方财富
    数据统一经由 `EASTMONEY_REQUESTER` 请求类，VIX 与加密货币兼容逻辑继续
    复用既有封装，避免改变现有功能。
    """

    async def fetch(
        self,
        market: str,
        sector: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> CloudMapDataResult:
        """获取云图/个股/对比图渲染所需原始数据。

        Args:
            market: 用户输入的市场、板块或标的文本。
            sector: 渲染类型或板块筛选条件。
            start_time: K 线或对比图开始时间。
            end_time: K 线或对比图结束时间。

        Returns:
            `CloudMapDataResult`。`raw_data` 为字符串时表示业务错误文本；
            `raw_datas` 仅在多标的场景中填充。
        """
        resolved_sector = self.resolve_sector(market, sector)
        raw_datas: List[Dict[str, Any]] = []
        special_cache_key: Optional[str] = None

        if market == "大盘云图":
            if resolved_sector:
                raw_data = await EASTMONEY_REQUESTER.get_market_list(resolved_sector, True, 1, 100)
            else:
                raw_data = await EASTMONEY_REQUESTER.get_hotmap()
        elif market == "行业云图":
            raw_data = await EASTMONEY_REQUESTER.get_hotmap()
        elif market == "概念云图":
            if resolved_sector:
                resolved_sector, raw_data = await self.fetch_concept(resolved_sector)
            else:
                raw_data = "概念云图需要后跟概念类型, 例如： 概念云图 华为欧拉"
        elif resolved_sector and resolved_sector.startswith("single-stock-kline"):
            raw_data = await get_gg(market, resolved_sector, start_time, end_time)
        elif resolved_sector == "compare-stock":
            raw_datas = await self.fetch_compare_stocks(market, start_time, end_time)
            if raw_datas:
                raw_data = raw_datas[0]
            else:
                raw_data = ErroText["notData"]
            st_f = start_time.strftime("%Y%m%d") if start_time else ""
            et_f = end_time.strftime("%Y%m%d") if end_time else ""
            special_cache_key = f"compare-stock-{st_f}-{et_f}"
        elif resolved_sector == "single-stock":
            raw_data, raw_datas = await self.fetch_single_stock_group(market, start_time, end_time)
        else:
            raw_data = await EASTMONEY_REQUESTER.get_market_list(market)

        return CloudMapDataResult(raw_data, raw_datas, resolved_sector, special_cache_key)

    def resolve_sector(self, market: str, sector: Optional[str]) -> Optional[str]:
        """解析云图板块别名。

        Args:
            market: 用户输入市场名称。
            sector: 原始板块参数。

        Returns:
            修正后的板块参数。市场命中 `market_dict`/`bk_dict` 时会把市场本身
            视作筛选板块。
        """
        if sector != "single-stock":
            if market in market_dict and "b:" in market_dict[market]:
                return market
            if market in bk_dict:
                return market
        return sector

    async def fetch_concept(self, sector: str) -> tuple[str, CloudMapRawData]:
        """获取概念云图数据。

        Args:
            sector: 概念名称或概念代码片段。

        Returns:
            二元组：解析后的概念名称、东方财富行情列表响应；未命中时响应为
            `ErroText["typemap"]`。
        """
        upper_sector = sector.upper()
        concept_menu = await EASTMONEY_REQUESTER.get_menu(3)
        if upper_sector in concept_menu:
            return upper_sector, await EASTMONEY_REQUESTER.get_market_list(concept_menu[upper_sector], True, 1, 100)

        for concept_name in concept_menu:
            if upper_sector in concept_name:
                return concept_name, await EASTMONEY_REQUESTER.get_market_list(concept_menu[concept_name], True, 1, 100)
        return upper_sector, ErroText["typemap"]

    async def fetch_compare_stocks(
        self,
        market: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> List[Dict[str, Any]]:
        """获取多个标的的日 K 对比数据。

        Args:
            market: 以空格分隔的标的列表。
            start_time: 开始时间。
            end_time: 结束时间。

        Returns:
            多个标的的 K 线响应列表。任一请求返回业务错误文本时直接中断并
            返回空列表，保持入口层错误处理兼容。
        """
        markets = [item.strip() for item in market.replace("，", " ").replace(",", " ").split() if item.strip()]
        results: List[Dict[str, Any]] = []
        for item in markets:
            if item in {"个股对比", "对比个股", "个股", "对比"}:
                continue
            query = "A500ETF" if item == "A500" else item
            raw_data = await get_gg(query, "single-stock-kline-111", start_time, end_time)
            if isinstance(raw_data, str):
                return []
            results.append(raw_data)
        return results

    async def fetch_single_stock_group(
        self,
        market: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> tuple[CloudMapRawData, List[Dict[str, Any]]]:
        """获取单个或多个标的分时数据。

        Args:
            market: 单个标的、多个标的或 VIX 别名。
            start_time: 兼容保留参数。
            end_time: 兼容保留参数。

        Returns:
            单标的时返回 `(raw_data, [])`；多标的时返回 `(首个数据, 全部数据)`。
        """
        vix_market = get_vix_name(market)
        if vix_market is not None:
            return await get_vix(vix_market), []

        market_list = market.split(" ")
        if len(market_list) == 1:
            return await get_gg(market_list[0], "single-stock", start_time, end_time), []

        tasks = []
        for item in market_list:
            vix_item = get_vix_name(item)
            if vix_item is None:
                tasks.append(get_gg(item, "single-stock", start_time, end_time))
            else:
                tasks.append(get_vix(vix_item))
        gathered = await asyncio.gather(*tasks)
        valid_results: List[Dict[str, Any]] = []
        for item in gathered:
            if isinstance(item, str):
                return item, []
            valid_results.append(item)
        return valid_results[0], valid_results


CLOUDMAP_DATA_SERVICE = CloudMapDataService()
