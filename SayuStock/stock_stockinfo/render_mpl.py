"""个股/云图渲染对外入口（matplotlib 实现）。

绘图实现已按种类拆到 ``chart_*.py``，本模块只保留：

- ``render_html`` / ``render_image_file`` / ``render_image``：拉数据 → 选图 → 出图
- ``_ai_return_*``：把图上的数据以**文字**发给 AI —— 部分模型看不到图，
  这段文字是它拿到的全部信息，实现在 ``utils/render_text.py``

历史调用方按 ``from .render_mpl import draw_xxx`` 导入，故此处 re-export 绘图函数。
"""

import asyncio
from pathlib import Path
from datetime import datetime, timedelta

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from .data import CLOUDMAP_DATA_SERVICE
from ..utils import render_text
from .chart_base import JsonDict, BotSendContent
from .chart_kline import to_single_fig_kline, draw_single_kline_chart
from .chart_compare import to_compare_fig, draw_compare_chart
from .chart_cloudmap import to_fig, draw_cloudmap_chart
from .chart_intraday import (
    to_multi_fig,
    to_single_fig,
    draw_multi_stock_chart,
    draw_single_stock_chart,
)
from ..utils.constant import ErroText
from ..utils.stock.utils import get_file
from ..stock_config.stock_config import STOCK_CONFIG

__all__ = [
    "draw_cloudmap_chart",
    "draw_compare_chart",
    "draw_multi_stock_chart",
    "draw_single_kline_chart",
    "draw_single_stock_chart",
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
    """兼容旧入口名：mpl 版本不生成 HTML，实际缓存 PNG 图片。"""
    return await render_image_file(market, sector, start_time, end_time)


async def render_image_file(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    logger.info(f"[SayuStock] market: {market} sector: {sector}")
    if sector == "single-stock" and not market:
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

    file = get_file(market, "png", sector, data_result.special_cache_key)
    if file.exists():
        minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(f"[SayuStock] png文件在{minutes}分钟内，直接返回文件数据。")
            return file

    if sector == "single-stock":
        fig = await to_multi_fig(raw_datas) if raw_datas else await to_single_fig(raw_data)
    elif sector == "compare-stock":
        fig = await to_compare_fig(raw_datas)
    elif sector and sector.startswith("single-stock-kline"):
        fig = await to_single_fig_kline(raw_data)
    else:
        fig = await to_fig(raw_data, market, sector, 2 if market == "大盘云图" else 1)

    if isinstance(fig, str):
        return fig

    await asyncio.to_thread(fig.save, file, format="PNG")
    return file


def _emit_ai_text(
    market: str,
    sector: str | None,
    raw_data: JsonDict,
    raw_datas: list[JsonDict],
) -> None:
    """按图表种类把图上的数据以文字发给 AI。

    与绘图分支分开、且先于缓存判断执行，保证「有图必有文字」——
    分支条件必须与下面的绘图分发保持一致。
    """
    if sector == "single-stock":
        if raw_datas:
            _ai_return_single_stock(raw_datas, is_multi=True)
        else:
            _ai_return_single_stock(raw_data)
    elif sector == "compare-stock":
        _ai_return_compare_stock(raw_datas)
    elif sector and sector.startswith("single-stock-kline"):
        _ai_return_kline(raw_data, sector)
    else:
        _ai_return_cloudmap(raw_data, market, sector)


def _ai_return_single_stock(raw_data: JsonDict | list[JsonDict], is_multi: bool = False) -> None:
    """把分时图的数据以文字发给 AI（部分模型看不到图，文字是它唯一的输入）。"""
    text = render_text.single_stock_text(raw_data, is_multi)
    if text:
        ai_return(text)


def _ai_return_kline(raw_data: JsonDict, sector: str) -> None:
    """把 K 线图上的全部指标以文字发给 AI。"""
    text = render_text.kline_text(raw_data, sector)
    if text:
        ai_return(text)


def _ai_return_compare_stock(raw_datas: list[JsonDict]) -> None:
    """把对比图的归一化涨跌、区间最大涨幅/回撤、极值点以文字发给 AI。"""
    text = render_text.compare_text(raw_datas)
    if text:
        ai_return(text)


def _ai_return_cloudmap(raw_data: JsonDict, market: str, sector: str | None = None) -> None:
    """把云图的领涨领跌与涨跌家数以文字发给 AI。"""
    text = render_text.cloudmap_text(raw_data, market, sector)
    if text:
        ai_return(text)


async def render_image(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> BotSendContent:
    image_path = await render_image_file(market, sector, start_time, end_time)
    if isinstance(image_path, str):
        return image_path

    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    return await convert_img(image_bytes)
