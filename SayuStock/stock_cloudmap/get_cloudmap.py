"""stock_cloudmap 对外兼容入口。

命令层只需要导入 `render_image`；实际渲染实现已迁移到
`render.py`，数据请求编排位于 `data.py`。
"""

from typing import Union, Optional
from pathlib import Path
from datetime import datetime

from .render import (
    to_fig,
    render_html,
    render_image,
    to_multi_fig,
    to_single_fig,
    to_compare_fig,
    to_single_fig_kline,
)

__all__ = [
    "render_image",
    "render_html",
    "to_fig",
    "to_multi_fig",
    "to_single_fig",
    "to_compare_fig",
    "to_single_fig_kline",
]


async def render_cloudmap_html(
    market: str = "沪深A",
    sector: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Union[str, Path]:
    """兼容性包装：渲染云图 HTML。

    Args:
        market: 市场、板块或标的输入。
        sector: 渲染类型或筛选板块。
        start_time: 可选开始时间。
        end_time: 可选结束时间。

    Returns:
        成功时返回 HTML 文件路径，业务失败时返回错误文本。
    """
    return await render_html(market, sector, start_time, end_time)
