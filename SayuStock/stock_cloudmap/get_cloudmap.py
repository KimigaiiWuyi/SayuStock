"""stock_cloudmap 对外入口。

命令层（``__init__.py``）只需要 ``render_image``；渲染实现在 ``render.py``（plotly），
数据请求编排在 ``data.py``。

本模块曾 re-export ``to_single_fig`` / ``to_multi_fig`` / ``to_compare_fig`` /
``to_single_fig_kline``，并提供一个无人调用的 ``render_cloudmap_html`` 包装 —— 都是
从 ``stock_stockinfo`` 拷贝时带过来的死代码（云图命令到不了那些分支，见 ``render.py``
模块注释），已随实现一并删除。个股 / 分时 / 对比请走 ``stock_stockinfo``。
"""

from .render import to_fig, render_html, render_image

__all__ = [
    "render_html",
    "render_image",
    "to_fig",
]
