"""大盘、行业和概念云图：共享的 Takumi 树图及独立缓存。"""

import asyncio
from pathlib import Path
from datetime import datetime

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from .data import CLOUDMAP_DATA_SERVICE
from ..utils import render_text
from ..render import RenderError
from ..render.treemap import treemap_html, render_treemap, prepare_treemap
from ..utils.constant import ErroText
from ..utils.market.models import BoardSnapshot
from ..utils.stock.render_cache import cached_render, get_render_file
from ..stock_config.stock_config import STOCK_CONFIG

__all__ = ["render_html", "render_image", "to_fig"]


async def to_fig(snap: BoardSnapshot, market: str, sector: str | None = None, layer: int = 2) -> str | bytes:
    return await render_treemap(snap, market, sector, layer)


async def _artifact(
    market: str, sector: str | None, start_time: datetime | None, end_time: datetime | None, suffix: str
) -> str | Path:
    if sector == "single-stock" and not market:
        return ErroText["notMarket"]
    market = market or "沪深A"
    result = await CLOUDMAP_DATA_SERVICE.fetch(market, sector, start_time, end_time)
    raw_data = result.raw_data
    if isinstance(raw_data, str):
        return raw_data
    if not isinstance(raw_data, BoardSnapshot):
        return ErroText["notData"]
    sector = result.sector
    _ai_return_cloudmap(raw_data, market, sector)

    width = int(STOCK_CONFIG.get_config("mapcloud_viewport").data)
    scale = int(STOCK_CONFIG.get_config("mapcloud_scale").data)
    minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)
    layer = 2 if market == "大盘云图" else 1
    file = get_render_file(
        market, suffix, sector, f"{result.special_cache_key}|{width}|{scale}", namespace="takumi-map-v1"
    )

    async def draw() -> str | bytes:
        if suffix == "html":
            page = await asyncio.to_thread(prepare_treemap, raw_data, market, sector, layer, width=width)
            return page if isinstance(page, str) else treemap_html(page).encode("utf-8")
        try:
            return await render_treemap(raw_data, market, sector, layer, width=width, scale=scale)
        except RenderError as error:
            logger.error(f"[SayuStock] 云图绘制失败: {error}")
            return "云图生成失败，请稍后重试。"

    return await cached_render(file, minutes, draw)


async def render_html(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    """保留返回 HTML 文件路径的调用接口。"""
    return await _artifact(market, sector, start_time, end_time, "html")


def _ai_return_cloudmap(snap: BoardSnapshot, market: str, sector: str | None = None) -> None:
    text = render_text.cloudmap_text(snap, market, sector)
    if text:
        ai_return(text)


async def render_image(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | bytes:
    path = await _artifact(market, sector, start_time, end_time, "png")
    if isinstance(path, str):
        return path
    return await convert_img(await asyncio.to_thread(path.read_bytes))
