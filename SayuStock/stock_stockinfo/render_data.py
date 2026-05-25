# pyright: reportMissingTypeStubs=false, reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false, reportExplicitAny=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryCast=false
"""stock_cloudmap 渲染前的数据计算层。

本模块只负责把接口原始数据转换成图表渲染所需的稳定结构，
不引入 plotly / matplotlib / mplchart。这样 plotly 版和 mpl 版渲染器
可以共享同一套 pandas 计算逻辑，减少重复代码和类型诊断噪音。
"""

import math
from typing import Any, Dict, List, cast
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gsuid_core.logger import logger

from .utils import fill_kline
from ..utils.utils import int_to_percentage, number_to_chinese
from ..utils.constant import ErroText
from ..utils.time_range import get_trading_minutes

RawDict = Dict[str, Any]
DataResult = str


@dataclass(slots=True)
class KlineRenderData:
    df: pd.DataFrame
    chart_df: pd.DataFrame
    title: str
    freq_label: str
    tickformat: str
    median_delta: pd.Timedelta
    x_min: pd.Timestamp
    x_max: pd.Timestamp
    breaks: list[dict[str, list[pd.Timestamp]]]
    max_turnovers: pd.DataFrame


@dataclass(slots=True)
class SingleStockRenderData:
    df: pd.DataFrame
    stock_name: str
    new_price: Any
    gained: float
    custom_info: str
    turnover_rate: Any
    total_amount: Any
    open_price: float
    y_axis_max_price: float
    y_axis_min_price: float
    max_fluctuation: float
    tick_values: list[float]
    tick_texts: list[str]
    bar_colors: list[str]
    title_text: str


@dataclass(slots=True)
class MultiStockItem:
    name: str
    df: pd.DataFrame
    total_volume: float
    line_color: str = ""


@dataclass(slots=True)
class MultiStockRenderData:
    stocks: list[MultiStockItem]
    y_axis_max: float
    y_axis_min: float
    tick_values: list[int]
    tick_texts: list[str]
    subtitle_parts: list[str]


@dataclass(slots=True)
class CompareStockItem:
    name: str
    df: pd.DataFrame
    color: str = ""


@dataclass(slots=True)
class CompareRenderData:
    items: list[CompareStockItem]


@dataclass(slots=True)
class CloudmapRenderData:
    df: pd.DataFrame
    title: str
    treemap_path: list[str]


def _sort_df(df: pd.DataFrame, by: str, *, ascending: bool = True) -> pd.DataFrame:
    return cast(pd.DataFrame, df.sort_values(by=by, ascending=ascending, inplace=False))


def _sort_series(series: pd.Series, *, ascending: bool = True) -> pd.Series:
    return cast(pd.Series, series.sort_values(ascending=ascending, inplace=False))


def _reset_df(df: pd.DataFrame, *, drop: bool = True) -> pd.DataFrame:
    return cast(pd.DataFrame, df.reset_index(drop=drop))


def _numeric_series(data: object, *, fill_value: float | None = None) -> pd.Series:
    source = data if isinstance(data, pd.Series) else pd.Series(data)
    series = cast(pd.Series, pd.to_numeric(source, errors="coerce"))
    if fill_value is None:
        return series
    return cast(pd.Series, series.fillna(fill_value))


def _as_optional_float(value: object) -> float | None:
    if isinstance(value, (int, float, str)):
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        if math.isfinite(result) and result != 0:
            return result
    return None


def _trend_minute_key(value: object) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) >= 5:
            return stripped[-5:]
        return stripped
    return str(value)


def _infer_kline_freq(df: pd.DataFrame) -> tuple[str, str, str, Any]:
    date_series = cast(pd.Series, df["日期"])
    sorted_dates = cast(pd.Series, _sort_series(date_series, ascending=True).reset_index(drop=True))
    deltas = cast(pd.Series, sorted_dates.diff().dropna())
    if deltas.empty:
        median_delta = pd.Timedelta(days=1)
    else:
        median_seconds = cast(float, deltas.dt.total_seconds().median())
        median_delta = pd.Timedelta(seconds=float(median_seconds))

    logger.info(f"[SayuStock] median delta: {median_delta}")
    seconds = median_delta.total_seconds()
    if seconds >= 0.9 * 86400:
        inferred_freq = "D"
        freq_label = "1D"
    elif seconds >= 0.9 * 3600:
        hours = max(1, int(round(seconds / 3600)))
        inferred_freq = f"{hours}H"
        freq_label = inferred_freq
    else:
        minutes = max(1, int(round(seconds / 60)))
        for item in (1, 5, 15, 30, 60):
            if abs(minutes - item) <= (item * 0.25):
                minutes = item
                break
        inferred_freq = f"{minutes}T"
        freq_label = f"{minutes}min"

    tickformat: str
    if "T" in inferred_freq or "H" in inferred_freq:
        tickformat = "%m-%d %H:%M"
    elif inferred_freq == "M":
        tickformat = "%Y.%m"
    else:
        tickformat = "%Y.%m.%d"

    logger.info(f"[SayuStock] 判定周期 inferred_freq={inferred_freq}, freq_label={freq_label}")
    return inferred_freq, freq_label, tickformat, median_delta


def build_kline_render_data(raw_data: RawDict) -> KlineRenderData | DataResult:
    df = fill_kline(raw_data)
    if df is None:
        return ErroText["notData"]

    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = cast(pd.DataFrame, df.dropna(subset=["日期"]))
    df = _reset_df(_sort_df(df, "日期"), drop=True)
    if df.empty:
        return ErroText["notData"]

    _, freq_label, tickformat, median_delta = _infer_kline_freq(df)
    x_min = cast(pd.Timestamp, df["日期"].min())
    x_max = cast(pd.Timestamp, df["日期"].max())

    chart_df = pd.DataFrame(
        {
            "date": df["日期"],
            "open": _numeric_series(df["开盘"]),
            "high": _numeric_series(df["最高"]),
            "low": _numeric_series(df["最低"]),
            "close": _numeric_series(df["收盘"]),
            "volume": _numeric_series(df["成交量"], fill_value=0),
            "turnover": _numeric_series(df["换手率"], fill_value=0) if "换手率" in df else 0.0,
            "ma5": _numeric_series(df["5日均线"]) if "5日均线" in df else np.nan,
            "ma10": _numeric_series(df["10日均线"]) if "10日均线" in df else np.nan,
        }
    )
    chart_df = cast(pd.DataFrame, chart_df.dropna(subset=["open", "high", "low", "close"]))
    if chart_df.empty:
        return ErroText["notData"]

    turnover = _numeric_series(df["换手率"]) if "换手率" in df else pd.Series(dtype=float)
    df["is_max"] = turnover == turnover.rolling(window=3, center=True).max()
    max_turnovers = cast(pd.DataFrame, df[df["is_max"] & (turnover > 0)])

    dates = cast(pd.Series, df["日期"])
    diffs = cast(pd.Series, dates.diff())
    threshold = pd.Timedelta(seconds=median_delta.total_seconds() * 1.5)
    breaks: list[dict[str, list[pd.Timestamp]]] = []
    for index in range(1, len(dates)):
        if pd.notna(diffs.iloc[index]) and diffs.iloc[index] > threshold:
            breaks.append(
                dict(bounds=[cast(pd.Timestamp, dates.iloc[index - 1]), cast(pd.Timestamp, dates.iloc[index])])
            )
    logger.info(f"[SayuStock] 自动检测到 {len(breaks)} 个时间缺口")

    return KlineRenderData(
        df=df,
        chart_df=chart_df,
        title=f"{raw_data['data']['name']} {freq_label}",
        freq_label=freq_label,
        tickformat=tickformat,
        median_delta=median_delta,
        x_min=x_min,
        x_max=x_max,
        breaks=breaks,
        max_turnovers=max_turnovers,
    )


def _full_single_trends(raw_data: RawDict) -> list[dict[str, Any]]:
    code_id = raw_data["file_name"] if "file_name" in raw_data else ""
    existing_map = {item["datetime"]: item for item in raw_data["trends"]}
    full_data: list[dict[str, Any]] = []
    for time in get_trading_minutes(code_id):
        if time in existing_map:
            full_data.append(existing_map[time])
        else:
            full_data.append(
                {
                    "datetime": time,
                    "price": None,
                    "open": None,
                    "high": None,
                    "low": None,
                    "amount": None,
                    "money": None,
                    "avg_price": None,
                }
            )
    return full_data


def build_single_stock_render_data(raw_data: RawDict) -> SingleStockRenderData | DataResult:
    raw = raw_data["data"]
    full_data = _full_single_trends(raw_data)
    price_history_pd = pd.DataFrame(
        {
            "datetime": [item["datetime"] for item in full_data],
            "price": [item["price"] for item in full_data],
            "money": [item["money"] for item in full_data],
        }
    )
    if price_history_pd.empty or price_history_pd["price"].iloc[0] is None:
        return ErroText["notOpen"]

    open_price = float(raw["f60"])
    price_history_pd["dt"] = pd.to_datetime(price_history_pd["datetime"], errors="coerce")
    price_history_pd["price"] = _numeric_series(price_history_pd["price"])
    price_history_pd["money"] = _numeric_series(price_history_pd["money"], fill_value=0)
    price_history_pd["percentage_change"] = ((price_history_pd["price"] / open_price) - 1) * 100

    max_price = cast(float, price_history_pd["price"].max())
    min_price = cast(float, price_history_pd["price"].min())
    max_fluctuation = max((max_price - open_price) / open_price, (open_price - min_price) / open_price)
    if pd.isna(max_fluctuation) or max_fluctuation <= 0:
        max_fluctuation = 0.01
    y_axis_max_price = open_price * (1 + max_fluctuation + 0.01)
    y_axis_min_price = open_price * (1 - max_fluctuation - 0.01)

    tick_values: list[float] = []
    tick_texts: list[str] = []
    max_range_percent = max_fluctuation * 100
    if max_range_percent > 30:
        step = 5
    elif max_range_percent > 15:
        step = 2
    else:
        step = 1
    for item in range(int(-(max_fluctuation + 0.01) * 100), int((max_fluctuation + 0.01) * 100) + 1):
        if item % step == 0:
            price = open_price * (1 + item / 100)
            if y_axis_min_price <= price <= y_axis_max_price:
                tick_values.append(price)
                tick_texts.append(f"{item}%")

    prices = cast(pd.Series, price_history_pd["price"])
    bar_colors: list[str] = []
    for index in range(len(prices)):
        if index == 0:
            bar_colors.append("red" if prices.iloc[index] > open_price else "green")
        else:
            prev = prices.iloc[index - 1]
            curr = prices.iloc[index]
            if pd.isna(curr) or pd.isna(prev):
                bar_colors.append("grey")
            elif curr > prev:
                bar_colors.append("red")
            elif curr < prev:
                bar_colors.append("green")
            else:
                bar_colors.append("grey")

    gained = float(raw["f170"])
    custom_info = int_to_percentage(gained)
    total_amount = number_to_chinese(raw["f48"]) if isinstance(raw["f48"], float) else 0
    stock_name = str(raw["f58"])
    new_price = raw["f43"]
    turnover_rate = raw["f168"]
    title_text = (
        f"【{stock_name} 最新价：{new_price}】 开盘价：{open_price} "
        f"涨跌幅：{custom_info} 换手率 {turnover_rate}% "
        f"成交额 {total_amount}"
    )

    return SingleStockRenderData(
        df=price_history_pd,
        stock_name=stock_name,
        new_price=new_price,
        gained=gained,
        custom_info=custom_info,
        turnover_rate=turnover_rate,
        total_amount=total_amount,
        open_price=open_price,
        y_axis_max_price=y_axis_max_price,
        y_axis_min_price=y_axis_min_price,
        max_fluctuation=max_fluctuation,
        tick_values=tick_values,
        tick_texts=tick_texts,
        bar_colors=bar_colors,
        title_text=title_text,
    )


def build_multi_stock_render_data(raw_data_list: List[RawDict]) -> MultiStockRenderData | DataResult:
    max_fluctuation = 0.0
    processed_stocks: list[MultiStockItem] = []

    for raw_data in raw_data_list:
        if not isinstance(raw_data, dict):
            continue
        raw = raw_data["data"]
        open_price = _as_optional_float(raw.get("f60"))
        if open_price is None:
            stock_name = raw.get("f58", "Unknown")
            logger.warning(f"[SayuStock] Skipping {stock_name} due to invalid open price: {raw.get('f60')}.")
            continue
        code_id = str(raw_data.get("file_name", "")).split("_")[0]
        time_array = get_trading_minutes(code_id)

        existing_map = {_trend_minute_key(item["datetime"]): item for item in raw_data["trends"]}
        full_data = [existing_map.get(time, {"datetime": time, "price": None, "money": 0}) for time in time_array]
        price_history_pd = pd.DataFrame(full_data)
        price_history_pd["dt"] = pd.to_datetime(price_history_pd["datetime"], errors="coerce")
        price_history_pd["price"] = _numeric_series(price_history_pd["price"])
        price_history_pd["money"] = _numeric_series(price_history_pd["money"], fill_value=0)
        price_history_pd["percentage_change"] = ((price_history_pd["price"] / open_price) - 1) * 100

        current_max = cast(float, price_history_pd["percentage_change"].max())
        current_min = cast(float, price_history_pd["percentage_change"].min())
        if not np.isnan(current_max):
            max_fluctuation = max(max_fluctuation, abs(current_max))
        if not np.isnan(current_min):
            max_fluctuation = max(max_fluctuation, abs(current_min))
        processed_stocks.append(
            MultiStockItem(
                name=str(raw["f58"]),
                df=price_history_pd,
                total_volume=float(cast(pd.Series, price_history_pd["money"]).sum()),
            )
        )

    if not processed_stocks:
        return ErroText["notData"]

    processed_stocks.sort(key=lambda item: item.total_volume, reverse=True)
    y_axis_max = (max_fluctuation // 2 + 1) * 2
    y_axis_min = -y_axis_max
    tick_values = [
        p for p in range(int(np.floor(y_axis_min)), int(np.ceil(y_axis_max)) + 1, 2) if y_axis_min <= p <= y_axis_max
    ]
    subtitle_parts: list[str] = []
    for stock in processed_stocks:
        last_change_series = cast(pd.Series, stock.df["percentage_change"]).dropna()
        if not last_change_series.empty:
            last_change = float(last_change_series.iloc[-1])
            sign = "+" if last_change >= 0 else ""
            subtitle_parts.append(f"{stock.name}: {sign}{last_change:.2f}%")

    return MultiStockRenderData(
        stocks=processed_stocks,
        y_axis_max=y_axis_max,
        y_axis_min=y_axis_min,
        tick_values=tick_values,
        tick_texts=[f"{p}%" for p in tick_values],
        subtitle_parts=subtitle_parts,
    )


def build_compare_render_data(raw_datas: List[RawDict]) -> CompareRenderData | DataResult:
    items: list[CompareStockItem] = []
    for index, raw_data in enumerate(raw_datas):
        df = fill_kline(raw_data)
        if df is None:
            continue
        df = df.copy()
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df = cast(pd.DataFrame, df.dropna(subset=["日期"]))
        raw_data_map: RawDict = raw_data["data"]
        trace_name = f"{raw_data_map['name'] if 'name' in raw_data_map else f'Trace {index}'}"
        items.append(CompareStockItem(name=trace_name, df=df))
    if not items:
        return ErroText["notData"]
    return CompareRenderData(items=items)


def build_cloudmap_render_data(
    raw_data: RawDict, market: str, sector: str | None = None, layer: int = 2
) -> CloudmapRenderData | DataResult:
    all_stocks: list[RawDict] = []
    data_map = raw_data["data"]
    for item in data_map["diff"]:
        if item["f20"] == "-" or item["f100"] == "-" or item["f3"] == "-":
            continue
        category_name = item["f100"]
        if item["f14"].startswith(("ST", "*ST")):
            category_name = "ST"
        all_stocks.append(
            {
                "category": category_name,
                "name": item["f14"],
                "value": float(item["f20"]),
                "diff_val": float(item["f3"]),
                "code": item["f12"],
                "sector": sector,
            }
        )

    if not all_stocks:
        return ErroText["notData"]

    grouped_by_category: dict[str, list[RawDict]] = defaultdict(list)
    for stock in all_stocks:
        grouped_by_category[str(stock["category"])].append(stock)

    if market == "大盘云图" or market == "概念云图":
        categories_to_process = list(grouped_by_category.keys())
    elif sector in grouped_by_category:
        categories_to_process = [cast(str, sector)]
    else:
        categories_to_process = []
        for item in grouped_by_category.keys():
            if sector and sector in item:
                categories_to_process = [item]
                break
        if not categories_to_process:
            return ErroText["notData"]

    final_stock_list: list[RawDict] = []
    for cat_name in categories_to_process:
        stock_items = grouped_by_category[cat_name]
        num_items = len(stock_items)
        if layer == 1:
            num_to_extract = num_items
        else:
            if num_items <= 40:
                fit = 0.6
            elif num_items <= 100:
                fit = 0.4
            elif num_items <= 200:
                fit = 0.3
            else:
                fit = 0.2
            ideal_count = math.ceil(num_items * fit)
            clamped_count = max(3, min(ideal_count, 15))
            num_to_extract = min(clamped_count, num_items)
        sorted_stocks = sorted(stock_items, key=lambda x: float(x["value"]), reverse=True)
        final_stock_list.extend(sorted_stocks[:num_to_extract])

    if not final_stock_list:
        return ErroText["notData"]

    df = _sort_df(pd.DataFrame(final_stock_list), "value", ascending=False)
    df = _reset_df(df, drop=True)
    if layer == 1:
        treemap_path = ["sector", "category", "name"]
    else:
        treemap_path = ["category", "name"]

    title = market if market else "板块云图"
    if sector and market not in ("大盘云图",):
        title = f"{market} - {sector}"

    return CloudmapRenderData(df=df, title=title, treemap_path=treemap_path)
