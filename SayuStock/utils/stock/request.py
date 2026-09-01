import asyncio
from typing import Any, Dict, List, Tuple, Union, Optional
from datetime import datetime

from gsuid_core.logger import logger

from .utils import calculate_difference
from ..market import KlinePeriod, get_market, is_market_error
from ..eastmoney import EASTMONEY_REQUESTER
from ..stock_period import is_intraday_sector, intraday_ndays_from_sector
from ..market.models import KlineSeries, BoardSnapshot, IntradaySeries


async def get_hours_from_em() -> Tuple[float, float, Optional[datetime]]:
    URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"  # noqa: E501
    y = 0
    ya = 0
    last_trade_date: Optional[datetime] = None
    for mk in ["1.000001", "0.399001"]:
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": "2",
            "secid": mk,
        }
        data = await EASTMONEY_REQUESTER.stock_request(
            URL,
            "GET",
            params=params,
        )
        if isinstance(data, int):
            logger.warning(f"[SayuStock] 获取{mk}数据失败, 错误码: {data}")
            continue
        ya0, y0, ltd = calculate_difference(data["data"]["trends"])
        y += y0
        ya += ya0
        last_trade_date = ltd
    return ya, y, last_trade_date


async def get_bar() -> dict[str, object] | str:
    URL = "https://quotederivates.eastmoney.com/datacenter/updowndistribution"
    PARAMS = {
        "mcodelist": "0.399002,1.000002,0.899050",
        "version": "100",
        "cver": "10.36.2",
    }

    resp = await EASTMONEY_REQUESTER.stock_request(
        URL,
        params=PARAMS,
    )

    if isinstance(resp, int):
        return f"[SayuStock] 请求错误：{resp}"
    if isinstance(resp, dict):
        return resp
    return f"[SayuStock] 请求错误：意外响应类型 {type(resp).__name__}"


async def get_menu(mode: int = 3) -> Dict[str, str]:
    """获取东方财富板块菜单。

    Args:
        mode: `2` 为行业板块，`3` 为概念板块。

    Returns:
        板块名称到板块代码的映射。
    """
    return await EASTMONEY_REQUESTER.get_menu(mode)


async def get_vix(vix_name: str) -> IntradaySeries | str:
    """VIX → IntradaySeries（经 MarketDataPort；源数据缓存在 get_vix_data）。"""
    series = await get_market().intraday(vix_name)
    if is_market_error(series):
        return series.message
    return series


async def get_single_fig_data(secid: str) -> Union[List[Dict[str, Union[str, float, int]]], str]:
    """获取个股当日分时走势。"""
    return await EASTMONEY_REQUESTER.get_stock_trends(secid)


async def get_gg(
    market: str,
    sector: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> IntradaySeries | KlineSeries | str:
    """个股分时/K 线入口：直接返回领域模型。"""
    logger.info(f"[SayuStock] get_gg code: {market} sector: {sector}")
    port = get_market()

    if is_intraday_sector(sector):
        ndays = intraday_ndays_from_sector(sector)
        if ndays is None:
            ndays = 1
        series = await port.intraday(market, ndays=ndays)
        if is_market_error(series):
            return series.message
        return series

    if sector.startswith("single-stock-kline"):
        kline_code = sector.split("-")[-1]
        try:
            period = KlinePeriod(kline_code)
        except ValueError:
            period = KlinePeriod.D1
        start_d = start_time.date() if start_time is not None else None
        end_d = end_time.date() if end_time is not None else None
        series_k = await port.kline(market, period, start=start_d, end=end_d)
        if is_market_error(series_k):
            return series_k.message
        return series_k

    return "未知 sector"


async def _get_gg(sec_id: str, sec_type: str) -> Union[Dict[str, Any], str]:
    """获取个股实时盘口并合并当日分时（底层 HTTP，仅 adapter 使用）。"""
    logger.info(f"[SayuStock] get_single_fig_data secid: {sec_id}")
    return await EASTMONEY_REQUESTER.get_single_stock(sec_id, sec_type)


async def _get_gg_kline(
    sec_id: str,
    sec_type: str,
    kline_code: Union[str, int],
    start_time: str,
    end_time: str,
) -> Union[Dict[str, Any], str]:
    """获取个股历史 K 线（底层 HTTP，仅 adapter 使用）。"""
    logger.info(f"[SayuStock] get_single_fig_data secid: {sec_id}")
    return await EASTMONEY_REQUESTER.get_stock_kline(sec_id, sec_type, kline_code, start_time, end_time)


async def get_mtdata(
    market: str,
    is_loop: bool = False,
    po: int = 1,
    pz: int = 20,
) -> BoardSnapshot | str:
    """获取行情列表/板块成分列表（领域模型）。"""
    port = get_market()
    if is_loop:
        # 全量拉取仍走 requester 分页，再解析为 BoardSnapshot
        raw = await EASTMONEY_REQUESTER.get_market_list(market, is_loop, po, pz)
        if isinstance(raw, str):
            return raw
        from ..market.enums import BoardKind
        from ..market.adapters.eastmoney.parse_board import parse_board_payload

        snap = parse_board_payload(raw, kind=BoardKind.OTHER, title=market)
        if is_market_error(snap):
            return snap.message
        return snap
    snap = await port.board(market, limit=pz, sort_asc=po == 1)
    if is_market_error(snap):
        return snap.message
    return snap


async def _get_data(
    resp: Dict[str, Any],
    url: str,
    params: List[tuple],
    stop_event: asyncio.Event,
) -> None:
    """兼容旧内部分页函数，实际分页逻辑已迁移到请求类。"""
    await EASTMONEY_REQUESTER._append_market_page(resp, url, params, stop_event)


async def get_hotmap() -> BoardSnapshot | str:
    """获取大盘云图 BoardSnapshot。"""
    snap = await get_market().hotmap()
    if is_market_error(snap):
        return snap.message
    return snap


async def stock_request(*args: Any, **kwargs: Any) -> Union[Dict[str, Any], int]:
    """兼容旧导入路径的东方财富请求工厂。"""
    return await EASTMONEY_REQUESTER.stock_request(*args, **kwargs)
