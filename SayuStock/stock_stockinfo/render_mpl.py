"""个股图表对外入口；沿用原命令和文件缓存接口。"""

import asyncio
from pathlib import Path
from datetime import datetime

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from .data import CLOUDMAP_DATA_SERVICE
from ..utils import render_text
from ..render import RenderError
from .chart_kline import to_single_fig_kline
from .chart_compare import to_compare_fig
from .chart_cloudmap import to_fig
from .chart_intraday import (
    to_multi_fig,
    to_single_fig,
)
from ..utils.constant import ErroText
from ..utils.stock_period import is_intraday_sector, intraday_ndays_from_sector
from ..utils.market.models import KlineSeries, BoardSnapshot, IntradaySeries
from ..utils.stock.render_cache import cached_render, get_render_file
from ..stock_config.stock_config import STOCK_CONFIG

__all__ = [
    "render_html",
    "render_image",
    "render_image_file",
    "to_compare_fig",
    "to_fig",
    "to_multi_fig",
    "to_single_fig",
    "to_single_fig_kline",
]


async def render_html(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    """兼容旧入口名：实际返回缓存 PNG 图片路径。"""
    return await render_image_file(market, sector, start_time, end_time)


async def render_image_file(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    logger.info(f"[SayuStock] market: {market} sector: {sector}")
    if is_intraday_sector(sector) and not market:
        return ErroText["notMarket"]
    if not market:
        market = "沪深A"

    logger.info("[SayuStock] 开始获取数据...")
    data_result = await CLOUDMAP_DATA_SERVICE.fetch(market, sector, start_time, end_time)
    raw_data = data_result.raw_data
    raw_datas = data_result.raw_datas
    sector = data_result.sector

    if isinstance(raw_data, str):
        return raw_data

    # 文字必须在缓存判断**之前**发：部分模型看不到图，ai_return 的文字是它唯一的
    # 输入，而命中 PNG 缓存会直接 return，绕过下面的绘图分支 —— 那样同一命令在
    # 刷新窗口内问第二次，AI 就一个字都收不到。
    _emit_ai_text(market, sector, raw_data, raw_datas)

    failures = data_result.failures
    if failures:
        ai_return("未获取到数据：" + "；".join(failures))
    key = data_result.special_cache_key
    if failures:
        key = f"{key}|" + "|".join(failures)
    file = get_render_file(market, "png", sector, key, namespace="takumi-stock-v1")
    minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)

    async def draw() -> bytes | str:
        try:
            if is_intraday_sector(sector):
                if raw_datas:
                    return await to_multi_fig(
                        [s for s in raw_datas if isinstance(s, IntradaySeries)], failures=failures
                    )
                if isinstance(raw_data, IntradaySeries):
                    return await to_single_fig(raw_data)
                return ErroText["notData"]
            if sector == "compare-stock":
                return await to_compare_fig([s for s in raw_datas if isinstance(s, KlineSeries)], failures=failures)
            if sector and sector.startswith("single-stock-kline"):
                if not isinstance(raw_data, KlineSeries):
                    return ErroText["notData"]
                return await to_single_fig_kline(raw_data)
            if not isinstance(raw_data, BoardSnapshot):
                return ErroText["notData"]
            return await to_fig(raw_data, market, sector, 2 if market == "大盘云图" else 1)
        except RenderError as error:
            logger.error(f"[SayuStock] 个股图表绘制失败: {error}")
            return "图表生成失败，请稍后重试。"

    return await cached_render(file, minutes, draw)


def _emit_ai_text(
    market: str,
    sector: str | None,
    raw_data: BoardSnapshot | IntradaySeries | KlineSeries,
    raw_datas: list[IntradaySeries | KlineSeries],
) -> None:
    """按图表种类把图上的数据以文字发给 AI。

    与绘图分支分开、且先于缓存判断执行，保证「有图必有文字」——
    分支条件必须与下面的绘图分发保持一致。
    """
    if is_intraday_sector(sector):
        ndays = intraday_ndays_from_sector(sector)
        if ndays is None:
            ndays = 1
        if raw_datas:
            _ai_return_single_stock(
                [s for s in raw_datas if isinstance(s, IntradaySeries)],
                is_multi=True,
                ndays=ndays,
            )
        elif isinstance(raw_data, IntradaySeries):
            _ai_return_single_stock(raw_data, ndays=ndays)
    elif sector == "compare-stock":
        _ai_return_compare_stock([s for s in raw_datas if isinstance(s, KlineSeries)])
    elif sector and sector.startswith("single-stock-kline"):
        if isinstance(raw_data, KlineSeries):
            _ai_return_kline(raw_data, sector)
    elif isinstance(raw_data, BoardSnapshot):
        _ai_return_cloudmap(raw_data, market, sector)


def _ai_return_single_stock(
    raw_data: IntradaySeries | list[IntradaySeries],
    is_multi: bool = False,
    *,
    ndays: int = 1,
) -> None:
    """把分时图的数据以文字发给 AI（部分模型看不到图，文字是它唯一的输入）。"""
    text = render_text.single_stock_text(raw_data, is_multi, ndays=ndays)
    if text:
        ai_return(text)


def _ai_return_kline(series: KlineSeries, sector: str) -> None:
    """把 K 线图上的全部指标以文字发给 AI。"""
    text = render_text.kline_text(series, sector)
    if text:
        ai_return(text)


def _ai_return_compare_stock(series_list: list[KlineSeries]) -> None:
    """把对比图的归一化涨跌、区间最大涨幅/回撤、极值点以文字发给 AI。"""
    text = render_text.compare_text(series_list)
    if text:
        ai_return(text)


def _ai_return_cloudmap(snap: BoardSnapshot, market: str, sector: str | None = None) -> None:
    """把云图的领涨领跌与涨跌家数以文字发给 AI。"""
    text = render_text.cloudmap_text(snap, market, sector)
    if text:
        ai_return(text)


async def render_image(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | bytes:
    image_path = await render_image_file(market, sector, start_time, end_time)
    if isinstance(image_path, str):
        return image_path

    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    return await convert_img(image_bytes)
