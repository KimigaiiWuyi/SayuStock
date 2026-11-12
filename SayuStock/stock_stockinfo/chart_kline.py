"""K 线图入口。"""

import asyncio

from ..render.kline import prepare_kline
from ..render.chart_page import render_chart
from ..utils.market.models import KlineSeries


async def to_single_fig_kline(series: KlineSeries, sp: str | None = None) -> str | bytes:
    return await render_chart(await asyncio.to_thread(prepare_kline, series))
