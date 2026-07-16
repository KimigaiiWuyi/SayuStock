"""渲染数据层 —— 实现已收敛到 ``SayuStock/utils/render_data.py``，这里只做兼容 re-export。

历史上 ``stock_stockinfo`` 与 ``stock_cloudmap`` 各存一份拷贝并已分叉
（换手率、跨天时间轴等修复只进了一份）。改动请去 ``utils/render_data.py``。
"""

from ..utils.render_data import (
    RawDict,
    DataResult,
    KlineRenderData,
    CompareRenderData,
    CloudmapRenderData,
    MultiStockRenderData,
    SingleStockRenderData,
    build_kline_render_data,
    build_compare_render_data,
    build_cloudmap_render_data,
    build_multi_stock_render_data,
    build_single_stock_render_data,
)

__all__ = [
    "CloudmapRenderData",
    "CompareRenderData",
    "DataResult",
    "KlineRenderData",
    "MultiStockRenderData",
    "RawDict",
    "SingleStockRenderData",
    "build_cloudmap_render_data",
    "build_compare_render_data",
    "build_kline_render_data",
    "build_multi_stock_render_data",
    "build_single_stock_render_data",
]
