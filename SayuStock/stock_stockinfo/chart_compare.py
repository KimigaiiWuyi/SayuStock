"""个股对比图入口。"""

import asyncio

from ..render.compare import prepare_compare
from ..render.chart_page import render_chart
from ..utils.market.models import KlineSeries


async def to_compare_fig(series_list: list[KlineSeries], *, failures: tuple[str, ...] = ()) -> str | bytes:
    page = await asyncio.to_thread(prepare_compare, series_list)
    if not isinstance(page, str) and failures:
        page.notes.extend(f"未获取 · {failure}" for failure in failures)
    return await render_chart(page)
