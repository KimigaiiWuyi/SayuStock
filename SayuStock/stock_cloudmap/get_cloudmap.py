"""市场云图的 Takumi 渲染入口；数据请求在 data.py 中编排。"""

from .render import to_fig, render_html, render_image

__all__ = [
    "render_html",
    "render_image",
    "to_fig",
]
