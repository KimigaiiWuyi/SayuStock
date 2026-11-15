"""分时图入口。"""

import asyncio

from ..render.intraday import prepare_multi_intraday, prepare_single_intraday
from ..render.chart_page import render_chart
from ..utils.market.models import IntradaySeries


async def to_single_fig(series: IntradaySeries) -> str | bytes:
    return await render_chart(await asyncio.to_thread(prepare_single_intraday, series))


async def to_multi_fig(series_list: list[IntradaySeries], *, failures: tuple[str, ...] = ()) -> str | bytes:
    page = await asyncio.to_thread(prepare_multi_intraday, series_list)
    if not isinstance(page, str) and failures:
        page.notes.extend(f"未获取 · {failure}" for failure in failures)
    return await render_chart(page)
