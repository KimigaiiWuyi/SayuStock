# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import asyncio
from io import BytesIO
from typing import Any, Union, Literal, Optional, TypedDict, cast
from dataclasses import dataclass

import pandas as pd
import matplotlib
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mplchart.chart import Chart  # noqa: E402
from mplchart.primitives import LinePlot  # noqa: E402

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.eastmoney import (
    EASTMONEY_REQUESTER,
    EASTMONEY_VALUE_NAME_MAP,
    EastMoneyStockItem,
    EastMoneyValueSeriesData,
)

ValueType = Literal["pe", "pb"]
BotSendContent = Union[str, bytes]

VALUE_NAME_MAP: dict[ValueType, str] = {
    "pe": EASTMONEY_VALUE_NAME_MAP["pe"],
    "pb": EASTMONEY_VALUE_NAME_MAP["pb"],
}


class StockItem(TypedDict):
    secid: str
    code: str
    name: str
    sec_type: str


class EastmoneyValueResult(TypedDict, total=False):
    data: list[dict[str, Any]]


class EastmoneyValueResponse(TypedDict, total=False):
    result: EastmoneyValueResult


@dataclass(slots=True)
class ValueSeries:
    code: str
    secid: str
    name: str
    sec_type: str
    df: pd.DataFrame

    @property
    def label(self) -> str:
        return f"{self.name}({self.code})"


async def get_eastmoney_pepb_compare(
    _input: str,
    _type: ValueType = "pe",
) -> BotSendContent:
    """获取东方财富PE/PB历史估值数据，并使用mplchart生成对比图。"""
    stock_list = await _parse_stock_input(_input)
    if not stock_list:
        return "❌未识别到有效股票，请输入股票代码或名称，例如：市盈率对比 贵州茅台 五粮液"

    tasks = [fetch_eastmoney_value_series(item, _type) for item in stock_list]
    raw_series = await asyncio.gather(*tasks, return_exceptions=True)

    series_list: list[ValueSeries] = []
    failed: list[str] = []
    for item, result in zip(stock_list, raw_series):
        item_desc = f"{item['name']}({item['code']})"
        if isinstance(result, BaseException):
            logger.warning(f"[SayuStock] 获取{item_desc}估值数据失败: {result}")
            failed.append(item_desc)
            continue
        value_series = cast(Optional[ValueSeries], result)
        if value_series is None or value_series.df.empty:
            failed.append(item_desc)
            continue
        series_list.append(value_series)

    if not series_list:
        return f"❌未获取到{VALUE_NAME_MAP[_type]}历史数据，可能该标的不支持东方财富估值接口。"

    # AI注入必须发生在“数据已获取、图片未生成”的位置，确保AI能获得可分析的结构化文字。
    _ai_return_value_compare(series_list, failed, _type)
    image = await asyncio.to_thread(draw_value_compare_chart, series_list, _type)
    return cast(BotSendContent, await convert_img(image))


async def _parse_stock_input(_input: str) -> list[StockItem]:
    stock_items = await EASTMONEY_REQUESTER.parse_stock_input(_input)
    return [cast(StockItem, item) for item in stock_items]


async def fetch_eastmoney_value_series(
    stock: StockItem,
    _type: ValueType,
) -> Optional[ValueSeries]:
    logger.info(f"[SayuStock] 获取东方财富{VALUE_NAME_MAP[_type]}历史估值: {stock['code']}")
    requester_stock = cast(EastMoneyStockItem, stock)
    if _type == "pe":
        raw_series = await EASTMONEY_REQUESTER.get_pe_series(requester_stock)
    else:
        raw_series = await EASTMONEY_REQUESTER.get_pb_series(requester_stock)
    if isinstance(raw_series, str):
        logger.warning(raw_series)
        return None

    series_data = cast(EastMoneyValueSeriesData, raw_series)
    rows = series_data["rows"]
    if not rows:
        return None

    sorted_items = [(pd.Timestamp(str(row["date"])), float(row["value"])) for row in rows]
    output_df = pd.DataFrame(
        {
            "date": [item[0] for item in sorted_items],
            "value": [item[1] for item in sorted_items],
        }
    )
    return ValueSeries(
        code=stock["code"],
        secid=stock["secid"],
        name=stock["name"],
        sec_type=stock["sec_type"],
        df=output_df,
    )


def draw_value_compare_chart(
    series_list: list[ValueSeries],
    _type: ValueType,
) -> Image.Image:
    merged = _merge_value_series(series_list)
    if merged.empty:
        raise ValueError("没有可绘制的估值数据")

    first_col = _safe_column_name(series_list[0])
    merged["close"] = merged[first_col]
    merged["open"] = merged["close"]
    merged["high"] = merged["close"]
    merged["low"] = merged["close"]
    merged["volume"] = 0

    color_values = plt.rcParams["axes.prop_cycle"].by_key().get("color", [])
    colors = cast(list[str], color_values)
    line_plots: list[Any] = []
    for index, item in enumerate(series_list):
        column = _safe_column_name(item)
        color = colors[index % len(colors)] if colors else None
        line_plots.append(LinePlot(column, label=item.label, width=2.2, color=color))

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    chart_data = cast(pd.DataFrame, merged.set_index("date"))

    title = f"东方财富{VALUE_NAME_MAP[_type]}历史走势对比"
    chart = Chart(
        chart_data,
        title=title,
        max_bars=720,
        figsize=(13.5, 7.5),
        raw_dates=True,
    )
    chart.plot(*line_plots)
    chart.add_legends()

    ax = chart.main_axes()
    ax.set_ylabel(VALUE_NAME_MAP[_type])
    ax.grid(True, alpha=0.25)
    chart.figure.text(
        0.01,
        0.01,
        "数据来源：东方财富 | SayuStock",
        fontsize=9,
        alpha=0.65,
    )

    output = BytesIO()
    chart.figure.savefig(output, format="png", dpi=180, bbox_inches="tight")
    plt.close(chart.figure)
    output.seek(0)
    return Image.open(output).convert("RGB")


def _merge_value_series(series_list: list[ValueSeries]) -> pd.DataFrame:
    merged: Optional[pd.DataFrame] = None
    for item in series_list:
        column = _safe_column_name(item)
        value_df = item.df.rename(columns={"value": column}).copy()
        if merged is None:
            merged = value_df
        else:
            merged = pd.merge(merged, value_df, on="date", how="outer")

    if merged is None:
        return pd.DataFrame()

    merged = cast(pd.DataFrame, merged.sort_values("date").reset_index(drop=True))
    value_columns = [_safe_column_name(item) for item in series_list]
    filled_values = cast(pd.DataFrame, merged[value_columns].ffill().bfill())
    merged[value_columns] = filled_values
    return merged


def _safe_column_name(item: ValueSeries) -> str:
    return f"v_{item.code}"


def _ai_return_value_compare(
    series_list: list[ValueSeries],
    failed: list[str],
    _type: ValueType,
) -> None:
    try:
        value_name = VALUE_NAME_MAP[_type]
        lines = [
            f"【{value_name}历史走势对比】",
            "数据来源：东方财富",
            f"对比标的数量：{len(series_list)}",
        ]
        latest_values: list[tuple[str, float]] = []
        for item in series_list:
            values = item.df["value"]
            latest_row = item.df.iloc[-1]
            first_row = item.df.iloc[0]
            latest_value = float(latest_row["value"])
            first_value = float(first_row["value"])
            change = latest_value - first_value
            change_rate = change / first_value * 100 if first_value else 0.0
            latest_date = pd.Timestamp(latest_row["date"]).date()
            first_date = pd.Timestamp(first_row["date"]).date()
            latest_values.append((item.label, latest_value))
            lines.append(
                f"- {item.label}: 最新{value_name}={latest_value:.2f}({latest_date}), "
                f"区间起点={first_value:.2f}({first_date}), "
                f"区间变化={change:+.2f}({change_rate:+.2f}%), "
                f"区间最低={float(values.min()):.2f}, "
                f"最高={float(values.max()):.2f}, "
                f"均值={float(values.mean()):.2f}, "
                f"样本数={len(values)}"
            )

        if latest_values:
            ranked = sorted(latest_values, key=lambda item: item[1], reverse=True)
            lines.append("最新估值从高到低：" + "、".join(f"{name} {value:.2f}" for name, value in ranked))
            high_name, high_value = ranked[0]
            low_name, low_value = ranked[-1]
            lines.append(
                f"当前最高：{high_name} {high_value:.2f}；"
                f"当前最低：{low_name} {low_value:.2f}；"
                f"高低差：{high_value - low_value:.2f}"
            )

        if failed:
            lines.append(f"未获取到数据: {'、'.join(failed)}")
        ai_return("\n".join(lines))
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return {VALUE_NAME_MAP[_type]}对比数据提取失败: {e}")
