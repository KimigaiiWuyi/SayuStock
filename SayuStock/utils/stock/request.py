import asyncio
from typing import Any, Dict, List, Tuple, Union, Optional
from datetime import datetime, timedelta

from gsuid_core.logger import logger

from .utils import async_file_cache, calculate_difference
from .get_vix import get_vix_data
from ..get_OKX import analyze_market_target, get_crypto_trend_as_json, get_crypto_history_kline_as_json
from ..constant import ErroText
from ..eastmoney import EASTMONEY_REQUESTER
from ..load_data import get_full_security_code
from .request_utils import get_code_id


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


async def get_bar():
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
    return resp


async def get_menu(mode: int = 3) -> Dict[str, str]:
    """获取东方财富板块菜单。

    Args:
        mode: `2` 为行业板块，`3` 为概念板块。

    Returns:
        板块名称到板块代码的映射。
    """
    return await EASTMONEY_REQUESTER.get_menu(mode)


@async_file_cache(market="vix_market", sector="{vix_name}", suffix="json")
async def get_vix(vix_name: str):
    trends = await get_vix_data(vix_name)
    if isinstance(trends, str):
        return trends

    price_change_percent = 0.0
    # 确保趋势数据非空且开盘价不为0，以避免除零错误
    if len(trends) > 0:
        latest_price = trends[-1]["price"]
        open_price = trends[0]["open"] if trends[0]["open"] != 0 else trends[0]["price"]

        price_change_percent: float = ((latest_price - open_price) / open_price) * 100  # type: ignore

    resp = {
        "data": {
            "f43": trends[-1]["price"],
            "f44": trends[-1]["price"],
            "f58": vix_name,
            "f60": open_price,
            "f48": 0,
            "f168": 0,
            "f170": round(float(price_change_percent), 2),
        },
        "trends": trends,
    }

    return resp


async def get_single_fig_data(secid: str) -> Union[List[Dict[str, Union[str, float, int]]], str]:
    """获取个股当日分时走势。"""
    return await EASTMONEY_REQUESTER.get_stock_trends(secid)


async def get_gg(
    market: str,
    sector: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
):
    logger.info(f"[SayuStock] get_single_fig_data code: {market}")

    _type, formatted_code = analyze_market_target(market)

    if _type == "crypto":
        pass
    else:
        sec_id_data = await get_code_id(market)
        if sec_id_data is None:
            return ErroText["notStock"]

        sec_id = get_full_security_code(sec_id_data[0])
        if sec_id is None:
            return ErroText["notStock"]

    if sector == "single-stock":
        if _type == "crypto":
            result = await get_crypto_trend_as_json(formatted_code)
        else:
            result = await _get_gg(sec_id, sec_id_data[2])
    elif sector.startswith("single-stock-kline"):
        kline_code = sector.split("-")[-1]
        if kline_code == "100":
            kline_code = 101
            out_day = 50
        elif kline_code == "101":
            out_day = 260
        elif kline_code == "102":
            out_day = 800
        elif kline_code == "103":
            out_day = 2000
        elif kline_code == "104":
            out_day = 4000
        elif kline_code == "105":
            out_day = 6000
        elif kline_code == "106":
            out_day = 10000
        elif kline_code == "111":
            kline_code = 101
            out_day = 365
        elif kline_code == "30":
            out_day = 60
        elif kline_code == "60":
            out_day = 100
        elif kline_code == "15":
            out_day = 40
        elif kline_code == "5":
            out_day = 30
        else:
            out_day = 1600

        if start_time is None:
            start_time = datetime.now() - timedelta(days=out_day)
        if end_time is None:
            end_time = datetime.now()
        st_f = start_time.strftime("%Y%m%d") if start_time else ""
        et_f = end_time.strftime("%Y%m%d") if end_time else ""

        if _type == "crypto":
            result = await get_crypto_history_kline_as_json(
                market,
                str(kline_code),
                st_f,
                et_f,
            )
        else:
            result = await _get_gg_kline(
                sec_id,
                sec_id_data[2],
                kline_code,
                st_f,
                et_f,
            )
    else:
        result = {}

    return result


async def _get_gg(sec_id: str, sec_type: str) -> Union[Dict[str, Any], str]:
    """获取个股实时盘口并合并当日分时。"""
    logger.info(f"[SayuStock] get_single_fig_data secid: {sec_id}")
    return await EASTMONEY_REQUESTER.get_single_stock(sec_id, sec_type)


async def _get_gg_kline(
    sec_id: str,
    sec_type: str,
    kline_code: Union[str, int],
    start_time: str,
    end_time: str,
) -> Union[Dict[str, Any], str]:
    """获取个股历史 K 线。"""
    logger.info(f"[SayuStock] get_single_fig_data secid: {sec_id}")
    return await EASTMONEY_REQUESTER.get_stock_kline(sec_id, sec_type, kline_code, start_time, end_time)


async def get_mtdata(
    market: str,
    is_loop: bool = False,
    po: int = 1,
    pz: int = 20,
) -> Union[Dict[str, Any], str]:
    """获取行情列表/板块成分列表。"""
    return await EASTMONEY_REQUESTER.get_market_list(market, is_loop, po, pz)


async def _get_data(
    resp: Dict[str, Any],
    url: str,
    params: List[tuple],
    stop_event: asyncio.Event,
) -> None:
    """兼容旧内部分页函数，实际分页逻辑已迁移到请求类。"""
    await EASTMONEY_REQUESTER._append_market_page(resp, url, params, stop_event)


async def get_hotmap() -> Union[Dict[str, Any], str]:
    """获取东方财富股票热力图原始数据并转换为云图结构。"""
    return await EASTMONEY_REQUESTER.get_hotmap()


async def stock_request(*args: Any, **kwargs: Any) -> Union[Dict[str, Any], int]:
    """兼容旧导入路径的东方财富请求工厂。"""
    return await EASTMONEY_REQUESTER.stock_request(*args, **kwargs)
