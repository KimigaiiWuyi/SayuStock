"""个股分时、K 线和对比图的 Takumi 渲染入口。"""

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
