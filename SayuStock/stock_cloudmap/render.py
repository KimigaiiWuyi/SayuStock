"""云图渲染（plotly + playwright 截图）—— 大盘 / 行业 / 概念。

**这是活的那份 plotly 渲染器**：`大盘云图` / `行业云图` / `概念云图` 三个命令走这里。
个股、日K、对比走的是 ``stock_stockinfo/``（matplotlib），与本模块无关。

本模块曾是 ``stock_stockinfo/render.py`` 的整份拷贝，因此长期挂着 kline / 分时 /
对比 三套 plotly 构图与分发分支。它们其实到不了：``CLOUDMAP_DATA_SERVICE.fetch``
先按 ``market == 大盘云图/行业云图/概念云图`` 分流，而本包的命令只会传这三个 market，
所以 ``raw_data`` 永远是云图形状；就算用户硬敲内部 sector 码，喂给那些分支的也是
错数据、必然失败。已于 2026-07-17 删除，个股相关一律去 ``stock_stockinfo``。

渲染前的数据层见 ``utils/render_data.py``，给 AI 的文字见 ``utils/render_text.py``。
"""

from typing import Any
from pathlib import Path
from datetime import datetime, timedelta

import plotly.express as px

from gsuid_core.logger import logger
from gsuid_core.ai_core.trigger_bridge import ai_return

from .data import CLOUDMAP_DATA_SERVICE
from ..utils import render_text
from .render_data import (
    build_cloudmap_render_data,
)
from ..utils.image import render_image_by_pw
from ..utils.constant import ErroText
from ..utils.stock.utils import get_file
from ..stock_config.stock_config import STOCK_CONFIG

PLOTLY_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#17becf", "#e377c2"]


def _dict_value(data: dict[str, Any], key: str, default: Any) -> Any:
    if key in data:
        return data[key]
    return default


async def to_fig(raw_data: dict[str, Any], market: str, sector: str | None = None, layer: int = 2):
    data = build_cloudmap_render_data(raw_data, market, sector, layer)
    if isinstance(data, str):
        return data
    cloudmap = data
    df = cloudmap.df.copy()
    df["Category"] = "<b>" + df["category"].astype(str) + "</b>"
    df["StockName"] = df["name"].astype(str)
    df["Values"] = df["value"]
    df["Diff"] = df["diff_val"]
    df["CustomInfo"] = df["diff_val"].apply(lambda d: f"+{d}%" if d >= 0 else f"{d}%")
    treemap_path = ["sector", "Category", "StockName"] if layer == 1 else ["Category", "StockName"]

    fig = px.treemap(
        df,
        path=treemap_path,
        values="Values",
        color="Diff",
        color_continuous_scale=[[0, "rgba(0, 255, 0, 1)"], [0.5, "rgba(61, 61, 59, 1)"], [1, "rgba(255, 0, 0, 1)"]],
        color_continuous_midpoint=0,
        range_color=[-10, 10],
        custom_data=["CustomInfo"],
        branchvalues="total",
    )
    fig.update_traces(
        marker=dict(cmin=-10, cmax=10),
        marker_pad=dict(l=5, r=5, b=5, t=60),
        textfont=dict(color="white"),
        textfont_family="MiSans",
        textfont_weight=350,
        texttemplate="%{label}<br>%{customdata[0]}",
        textfont_size=50,
        textposition="middle center",
    )
    fig.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        paper_bgcolor="black",
        plot_bgcolor="black",
        font=dict(color="white"),
        coloraxis_showscale=False,
    )
    return fig


async def render_html(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> str | Path:
    logger.info(f"[SayuStock] market: {market} sector: {sector}")
    if sector == "single-stock" and not market:
        return ErroText["notMarket"]
    if not market:
        market = "沪深A"

    logger.info("[SayuStock] 开始获取数据...")
    data_result = await CLOUDMAP_DATA_SERVICE.fetch(market, sector, start_time, end_time)
    raw_data = data_result.raw_data
    sector = data_result.sector
    if isinstance(raw_data, str):
        return raw_data

    # 文字必须在缓存判断**之前**发：部分模型看不到图，ai_return 的文字是它唯一的
    # 输入，而命中 HTML 缓存会直接 return，绕过下面的出图 —— 那样同一命令在刷新
    # 窗口内问第二次，AI 就一个字都收不到。
    _ai_return_cloudmap(raw_data, market, sector)

    file = get_file(market, "html", sector, data_result.special_cache_key)
    if file.exists():
        minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(f"[SayuStock] html文件在{minutes}分钟内，直接返回文件数据。")
            return file

    fig = await to_fig(raw_data, market, sector, 2 if market == "大盘云图" else 1)
    if isinstance(fig, str):
        return fig

    fig.write_html(file)
    return file


def _ai_return_cloudmap(raw_data: dict[str, Any], market: str, sector: str | None = None) -> None:
    """把云图的领涨领跌与涨跌家数以文字发给 AI。"""
    text = render_text.cloudmap_text(raw_data, market, sector)
    if text:
        ai_return(text)


async def render_image(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    html_path = await render_html(market, sector, start_time, end_time)
    if isinstance(html_path, str):
        return html_path
    # w/h/scale 全 0 = 让 playwright 按 HTML 自身尺寸截图（云图是满幅矩形树图）
    return await render_image_by_pw(html_path, 0, 0, 0)


__all__ = [
    "render_html",
    "render_image",
    "to_fig",
]
