"""stock_stockinfo 对外渲染入口。

个股分时、K 线和个股对比默认使用 matplotlib/mplchart 渲染实现。
"""

from .render_mpl import (
    render_image,
    to_multi_fig,
    to_single_fig,
    to_compare_fig,
    render_image_file,
    to_single_fig_kline,
)

__all__ = [
    "render_image",
    "render_image_file",
    "to_multi_fig",
    "to_single_fig",
    "to_compare_fig",
    "to_single_fig_kline",
]
