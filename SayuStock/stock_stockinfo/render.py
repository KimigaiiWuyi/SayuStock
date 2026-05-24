# pyright: reportMissingTypeStubs=false, reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false, reportExplicitAny=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryCast=false
from typing import Any, cast
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from gsuid_core.logger import logger
from gsuid_core.ai_core.trigger_bridge import ai_return

from .data import CLOUDMAP_DATA_SERVICE
from .render_data import (
    MultiStockItem,
    KlineRenderData,
    CompareRenderData,
    CloudmapRenderData,
    SingleStockRenderData,
    build_kline_render_data,
    build_compare_render_data,
    build_cloudmap_render_data,
    build_multi_stock_render_data,
    build_single_stock_render_data,
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


async def to_single_fig_kline(raw_data: dict[str, Any], sp: str | None = None):
    data = build_kline_render_data(raw_data)
    if isinstance(data, str):
        return data
    kline = cast(KlineRenderData, data)
    df = kline.df

    volume_colors = ["red" if close >= open_price else "green" for close, open_price in zip(df["收盘"], df["开盘"])]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df["日期"],
                open=df["开盘"],
                high=df["最高"],
                low=df["最低"],
                close=df["收盘"],
                increasing_line_color="red",
                decreasing_line_color="green",
                name="K线",
                yaxis="y1",
            ),
            go.Scatter(
                x=df["日期"],
                y=df["换手率"],
                mode="lines",
                line=dict(color="purple", width=4),
                yaxis="y2",
                name="换手率",
            ),
            go.Scatter(
                x=df["日期"],
                y=df["5日均线"],
                mode="lines",
                line=dict(color="orange", width=3),
                name="5日均线",
                yaxis="y1",
            ),
            go.Scatter(
                x=df["日期"],
                y=df["10日均线"],
                mode="lines",
                line=dict(color="blue", width=3),
                name="10日均线",
                yaxis="y1",
            ),
            go.Bar(
                x=df["日期"],
                y=df["成交量"],
                marker_color=volume_colors,
                name="成交量",
                yaxis="y3",
            ),
        ]
    )

    for _, row in kline.max_turnovers.iterrows():
        fig.add_trace(
            go.Scatter(
                x=[row["日期"]],
                y=[row["换手率"]],
                mode="markers+text",
                text=[f"{row['换手率'] * 100:.2f}%"],
                textposition="top center",
                marker=dict(size=10, color="red"),
                showlegend=False,
                yaxis="y2",
            )
        )

    fig.update_layout(
        title=dict(text=kline.title, font=dict(size=80), y=0.98, x=0.5, xanchor="center", yanchor="top"),
        xaxis=dict(title_font=dict(size=40), tickfont=dict(size=40)),
        xaxis2=dict(anchor="y2", matches="x", showticklabels=False),
        xaxis3=dict(anchor="y3", matches="x", showticklabels=True),
        yaxis=dict(title="价格", domain=[0.5, 1], title_font=dict(size=40), tickfont=dict(size=40)),
        yaxis2=dict(
            title="换手率", domain=[0.25, 0.45], title_font=dict(size=40), tickfont=dict(size=40), tickformat=".0%"
        ),
        yaxis3=dict(title="成交量", domain=[0, 0.2], title_font=dict(size=40), tickfont=dict(size=40), side="right"),
        legend=dict(title=dict(font=dict(size=40))),
        font=dict(size=40),
        margin=dict(t=100, b=100, l=100, r=100),
    )
    fig.update_xaxes(
        type="date",
        tickformat=kline.tickformat,
        range=[kline.x_min, kline.x_max],
        rangeslider_visible=False,
        rangebreaks=kline.breaks,
    )
    return fig


async def to_single_fig(raw_data: dict[str, Any]):
    logger.info("[SayuStock] 开始获取图形...")
    data = build_single_stock_render_data(raw_data)
    if isinstance(data, str):
        return data
    stock = cast(SingleStockRenderData, data)
    df = stock.df

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(
        go.Scatter(
            x=df["datetime"],
            y=df["price"],
            mode="lines",
            name="Price",
            line=dict(width=3, color="white"),
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(x=df["datetime"], y=df["money"], name="Volume", marker_color=stock.bar_colors, showlegend=False),
        row=2,
        col=1,
    )
    fig.add_hrect(
        y0=stock.open_price, y1=stock.y_axis_max_price, fillcolor="red", opacity=0.2, layer="below", line_width=0
    )
    fig.add_hrect(
        y0=stock.y_axis_min_price, y1=stock.open_price, fillcolor="green", opacity=0.2, layer="below", line_width=0
    )
    fig.add_hline(y=stock.open_price, line=dict(color="yellow", width=2, dash="dashdot"))

    title_str = (
        f"<b>【{stock.stock_name}  最新价：{stock.new_price}】 开盘价：{stock.open_price} "
        f"涨跌幅：<span style='color:{'red' if stock.gained >= 0 else 'green'};'>{stock.custom_info}</span> "
        f"换手率 {stock.turnover_rate}% 成交额 {stock.total_amount}</b>"
    )
    fig.update_layout(
        title=dict(text=title_str, font=dict(size=60), y=0.99, x=0.5, xanchor="center", yanchor="top"),
        margin=dict(t=80, l=50, r=50, b=50),
        paper_bgcolor="black",
        plot_bgcolor="black",
        font=dict(color="white", size=40),
        showlegend=False,
    )
    fig.update_yaxes(
        title_text="价格",
        range=[stock.y_axis_min_price, stock.y_axis_max_price],
        showgrid=True,
        gridcolor="rgba(255,255,255,0.2)",
        tickvals=stock.tick_values,
        ticktext=stock.tick_texts,
        title_font=dict(size=45),
        tickfont=dict(size=26),
        row=1,
        col=1,
    )
    fig.update_yaxes(title_text="量能", showgrid=False, title_font=dict(size=45), tickfont=dict(size=26), row=2, col=1)
    fig.update_xaxes(dtick=60, title_font=dict(size=45), tickfont=dict(size=26), row=1, col=1)
    fig.update_xaxes(
        title_text="时间", showgrid=False, dtick=15, title_font=dict(size=45), tickfont=dict(size=26), row=2, col=1
    )
    return fig


async def to_multi_fig(raw_data_list: list[dict[str, Any]]):
    logger.info("[SayuStock] Starting to generate multi-stock figure with multi-line title...")
    data = build_multi_stock_render_data(raw_data_list)
    if isinstance(data, str):
        return data
    multi = data
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])

    for index, stock in enumerate(multi.stocks):
        item = cast(MultiStockItem, stock)
        df = item.df
        line_color = PLOTLY_COLORS[index % len(PLOTLY_COLORS)]
        fig.add_trace(
            go.Scatter(
                x=df["datetime"],
                y=df["percentage_change"],
                mode="lines",
                name=item.name,
                line=dict(width=3, color=line_color),
                showlegend=True,
            ),
            row=1,
            col=1,
        )
        last_valid_index = cast(pd.Series, df["percentage_change"]).last_valid_index()
        if last_valid_index is not None:
            fig.add_annotation(
                x=df.loc[last_valid_index, "datetime"],
                y=df.loc[last_valid_index, "percentage_change"],
                text=f"<b>{item.name}</b>",
                showarrow=False,
                xshift=25,
                yshift=10,
                bgcolor=line_color,
                font=dict(color="white", size=18),
                row=1,
                col=1,
            )
        fig.add_trace(
            go.Bar(
                x=df["datetime"],
                y=df["money"].fillna(0),
                name=item.name + " Volume",
                marker_color=line_color,
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    subtitle_parts = []
    for part in multi.subtitle_parts:
        color = "red" if "+" in part else "green"
        subtitle_parts.append(f"<b><span style='color:{color};'>{part}</span></b>")
    final_title = f"<b>分时涨跌幅对比</b><br>{'&nbsp;&nbsp;&nbsp;'.join(subtitle_parts)}"
    fig.add_hrect(
        y0=0,
        y1=multi.y_axis_max,
        fillcolor="red",
        opacity=0.1,
        layer="below",
        line_width=0,
        row=cast(Any, 1),
        col=cast(Any, 1),
    )
    fig.add_hrect(
        y0=multi.y_axis_min,
        y1=0,
        fillcolor="green",
        opacity=0.1,
        layer="below",
        line_width=0,
        row=cast(Any, 1),
        col=cast(Any, 1),
    )
    fig.add_hline(y=0, line=dict(color="yellow", width=1, dash="dash"), row=cast(Any, 1), col=cast(Any, 1))
    fig.update_layout(
        title=dict(text=final_title, font=dict(size=60), y=0.96, x=0.5, xanchor="center", yanchor="top"),
        margin=dict(t=200, l=70, r=70, b=80),
        paper_bgcolor="black",
        plot_bgcolor="black",
        font=dict(color="white", size=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1, font=dict(size=60)),
        barmode="stack",
    )
    fig.update_yaxes(
        title_text="<b>涨跌幅 (%)</b>",
        showgrid=True,
        gridcolor="rgba(255,255,255,0.2)",
        range=[multi.y_axis_min, multi.y_axis_max],
        tickvals=multi.tick_values,
        ticktext=multi.tick_texts,
        row=1,
        col=1,
    )
    fig.update_yaxes(title_text="<b>成交额</b>", showgrid=False, row=2, col=1)
    fig.update_xaxes(
        showticklabels=True, showgrid=True, gridcolor="rgba(255,255,255,0.2)", dtick=60, tickangle=0, row=1, col=1
    )
    fig.update_xaxes(
        title_text="<b>时间</b>", showgrid=True, gridcolor="rgba(255,255,255,0.2)", tickangle=45, dtick=30, row=2, col=1
    )
    return fig


async def to_compare_fig(raw_datas: list[dict[str, Any]]):
    data = build_compare_render_data(raw_datas)
    if isinstance(data, str):
        return data
    compare = cast(CompareRenderData, data)
    fig = go.Figure()
    for index, item in enumerate(compare.items):
        color = PLOTLY_COLORS[index % len(PLOTLY_COLORS)]
        fig.add_trace(
            go.Scatter(
                x=item.df["日期"], y=item.df["归一化"], mode="lines", line=dict(color=color, width=4), name=item.name
            )
        )
    fig.update_layout(
        title=dict(text="对比图", font=dict(size=60), x=0.5, xanchor="center"),
        xaxis=dict(title_font=dict(size=40), tickfont=dict(size=40)),
        yaxis=dict(title_font=dict(size=40), tickfont=dict(size=40), title=""),
        legend=dict(itemsizing="trace", title=dict(font=dict(size=80)), font=dict(size=60)),
        font=dict(size=60),
    )
    fig.update_xaxes(tickformat="%Y.%m")
    fig.update_yaxes(tickformat=".0%")
    return fig


async def to_fig(raw_data: dict[str, Any], market: str, sector: str | None = None, layer: int = 2):
    data = build_cloudmap_render_data(raw_data, market, sector, layer)
    if isinstance(data, str):
        return data
    cloudmap = cast(CloudmapRenderData, data)
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
    raw_datas = data_result.raw_datas
    sector = data_result.sector
    if isinstance(raw_data, str):
        return raw_data

    file = get_file(market, "html", sector, data_result.special_cache_key)
    if file.exists():
        minutes = STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(f"[SayuStock] html文件在{minutes}分钟内，直接返回文件数据。")
            return file

    if sector == "single-stock":
        if raw_datas:
            fig = await to_multi_fig(raw_datas)
            _ai_return_single_stock(raw_datas, is_multi=True)
        else:
            fig = await to_single_fig(raw_data)
            _ai_return_single_stock(raw_data)
    elif sector == "compare-stock":
        fig = await to_compare_fig(raw_datas)
        _ai_return_compare_stock(raw_datas)
    elif sector and sector.startswith("single-stock-kline"):
        fig = await to_single_fig_kline(raw_data)
        _ai_return_kline(raw_data, sector)
    else:
        fig = await to_fig(raw_data, market, sector, 2 if market == "大盘云图" else 1)
        _ai_return_cloudmap(raw_data, market, sector)
    if isinstance(fig, str):
        return fig

    fig.write_html(file)
    return file


def _ai_return_single_stock(raw_data: dict[str, Any] | list[dict[str, Any]], is_multi: bool = False) -> None:
    """从个股数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    if is_multi:
        parts: list[str] = []
        raw_data_list = raw_data if isinstance(raw_data, list) else []
        for rd in raw_data_list:
            if isinstance(rd, str):
                continue
            data = rd["data"]
            parts.append(
                f"【{_dict_value(data, 'f58', 'N/A')}】最新价: {_dict_value(data, 'f43', 'N/A')}  "
                f"涨跌幅: {_dict_value(data, 'f170', 'N/A')}%  "
                f"开盘: {_dict_value(data, 'f60', 'N/A')}  "
                f"最高: {_dict_value(data, 'f44', 'N/A')}  "
                f"最低: {_dict_value(data, 'f45', 'N/A')}  "
                f"换手率: {_dict_value(data, 'f168', 'N/A')}%  "
                f"成交额: {_dict_value(data, 'f48', 'N/A')}"
            )
        if parts:
            ai_return("【多股分时行情对比】\n" + "\n".join(parts))
        return

    data = cast(dict[str, Any], raw_data)["data"]
    result = (
        f"【{_dict_value(data, 'f58', 'N/A')} 分时行情】\n"
        f"最新价: {_dict_value(data, 'f43', 'N/A')}  涨跌幅: {_dict_value(data, 'f170', 'N/A')}%\n"
        f"开盘价: {_dict_value(data, 'f60', 'N/A')}  "
        f"最高价: {_dict_value(data, 'f44', 'N/A')}  最低价: {_dict_value(data, 'f45', 'N/A')}\n"
        f"换手率: {_dict_value(data, 'f168', 'N/A')}%  成交额: {_dict_value(data, 'f48', 'N/A')}"
    )
    ai_return(result)


def _ai_return_kline(raw_data: dict[str, Any], sector: str) -> None:
    """从K线数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    data = raw_data["data"]
    klines = data["klines"]
    if not klines:
        return

    period_map = {
        "5": "5分钟",
        "15": "15分钟",
        "30": "30分钟",
        "60": "60分钟",
        "100": "K线",
        "101": "日K",
        "102": "周K",
        "103": "月K",
        "104": "季K",
        "105": "半年K",
        "106": "年K",
    }
    code = sector.replace("single-stock-kline-", "")
    period_name = period_map[code] if code in period_map else "K线"
    result = f"【{_dict_value(data, 'name', 'N/A')} {period_name}数据】\n"
    result += "日期        开盘    收盘    最高    最低    涨跌幅\n"
    for line in klines[-10:]:
        values = line.split(",")
        if len(values) >= 11:
            result += f"{values[0]} {values[1]:>8} {values[2]:>8} {values[3]:>8} {values[4]:>8} {values[8]:>8}%\n"
    ai_return(result)


def _ai_return_compare_stock(raw_datas: list[dict[str, Any]]) -> None:
    """从对比个股数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    parts: list[str] = []
    for rd in raw_datas:
        data = rd["data"]
        name = data["name"] if "name" in data else _dict_value(data, "f58", "N/A")
        klines = data["klines"]
        if klines:
            last = klines[-1].split(",")
            if len(last) >= 11:
                parts.append(f"{name}: 收盘 {last[2]}  涨跌幅 {last[8]}%")
    if parts:
        ai_return("【个股对比数据】\n" + "\n".join(parts))


def _ai_return_cloudmap(raw_data: dict[str, Any], market: str, sector: str | None = None) -> None:
    """从大盘云图/板块云图/概念云图数据中提取文本信息，通过 ai_return 返回给 AI 分析。"""
    diff = raw_data["data"]["diff"]
    if not diff:
        return
    valid_items = [item for item in diff if item["f3"] != "-" and item["f14"]]
    valid_items.sort(key=lambda x: float(x["f3"]), reverse=True)
    title = market if market else "板块云图"
    if sector and market not in ("大盘云图",):
        title = f"{market} - {sector}"
    result = f"【{title}】\n"
    result += "领涨:\n"
    for item in valid_items[:5]:
        result += f"  {item['f14']}({item['f100']}): {item['f3']}%\n"
    result += "领跌:\n"
    for item in valid_items[-5:]:
        result += f"  {item['f14']}({item['f100']}): {item['f3']}%\n"
    up_count = sum(1 for item in valid_items if float(item["f3"]) > 0)
    down_count = sum(1 for item in valid_items if float(item["f3"]) < 0)
    flat_count = len(valid_items) - up_count - down_count
    result += f"统计: 上涨 {up_count} 家, 下跌 {down_count} 家, 平盘 {flat_count} 家"
    ai_return(result)


async def render_image(
    market: str = "沪深A",
    sector: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    html_path = await render_html(market, sector, start_time, end_time)
    if isinstance(html_path, str):
        return html_path
    if sector and sector.startswith("single-stock-kline") or sector == "compare-stock":
        w = 4600
        h = 3000
        _scale = 1
    elif sector == "single-stock":
        w = 4000
        h = 3000
        _scale = 1
    else:
        w = 0
        h = 0
        _scale = 0
    return await render_image_by_pw(html_path, w, h, _scale)


__all__ = [
    "render_image",
    "render_html",
    "to_fig",
    "to_multi_fig",
    "to_single_fig",
    "to_compare_fig",
    "to_single_fig_kline",
]
