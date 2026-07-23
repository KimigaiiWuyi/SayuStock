"""渲染前的数据计算层 —— 全插件唯一真相源。

只负责把接口原始数据转换成图表渲染所需的稳定结构，不引入 plotly /
matplotlib / mplchart，好让 plotly 版（``stock_cloudmap/render.py``，云图）
和 mpl 版（``stock_stockinfo/render_mpl.py``，个股）共享同一套 pandas 逻辑。

这份共享一直是设计意图，但此前是靠**复制**实现的：两个包各存一份，然后
不出意外地分叉了 —— 换手率、跨天时间轴（BJT）等修复只进了其中一份。现在
两边都从这里 re-export。指标数学见 ``utils/indicators.py``。
"""

import math
from typing import Any, Dict, List
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gsuid_core.logger import logger

from .kline import fill_kline
from .utils import int_to_percentage, number_to_chinese
from .constant import ErroText
from .time_range import is_market_active_now, get_trading_datetimes_bjt

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
    stock_code: str
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
    return df.sort_values(by=by, ascending=ascending, inplace=False)


def _sort_series(series: pd.Series, *, ascending: bool = True) -> pd.Series:
    return series.sort_values(ascending=ascending, inplace=False)


def _reset_df(df: pd.DataFrame, *, drop: bool = True) -> pd.DataFrame:
    return df.reset_index(drop=drop)


def _frame_column(df: pd.DataFrame, key: str) -> pd.Series:
    column = df[key]
    assert isinstance(column, pd.Series), f"列 {key} 存在重复标签"
    return column


def _numeric_series(data: object, *, fill_value: float | None = None) -> pd.Series:
    source = data if isinstance(data, pd.Series) else pd.Series(data)
    series = pd.to_numeric(source, errors="coerce")
    assert isinstance(series, pd.Series), "to_numeric(Series) 恒返回 Series"
    if fill_value is None:
        return series
    return series.fillna(fill_value)


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
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return ""
        return value.strftime("%H:%M")
    return str(value)


def _parse_trend_datetime_value(value: object) -> pd.Timestamp | None:
    """解析分时点时间：支持 ``HH:MM`` / ``YYYY-MM-DD HH:MM`` / datetime。"""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    # 纯 HH:MM —— 无日期，交由上层补日期
    if len(text) <= 5 and ":" in text and "-" not in text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    assert isinstance(parsed, pd.Timestamp)
    return parsed


def _resolve_trend_absolute_datetimes(
    trends: list[dict[str, Any]],
    *,
    now_bjt: Any | None = None,
) -> list[tuple[dict[str, Any], pd.Timestamp]]:
    """把 trends 解析为 (原始点, 绝对时间) 列表。

    - 已带完整日期（``YYYY-MM-DD HH:MM``）时直接使用；
    - 仅 ``HH:MM``（旧缓存）时按顺序检测跨天回绕，并把最后一个点锚定到
      ``now_bjt`` 所在会话，避免夜盘/美期把上半天数据贴到「次日」。
    """
    import datetime as _dt

    if not trends:
        return []

    if now_bjt is None:
        now_bjt_dt = _dt.datetime.now()
    elif isinstance(now_bjt, pd.Timestamp):
        now_bjt_dt = now_bjt.to_pydatetime().replace(tzinfo=None)
    elif isinstance(now_bjt, _dt.datetime):
        now_bjt_dt = now_bjt.replace(tzinfo=None) if now_bjt.tzinfo else now_bjt
    else:
        now_bjt_dt = _dt.datetime.now()

    # 路径 1：全部（或绝大多数）点已带完整日期
    full_parsed: list[tuple[dict[str, Any], pd.Timestamp | None]] = []
    full_count = 0
    for item in trends:
        parsed = _parse_trend_datetime_value(item.get("datetime") if isinstance(item, dict) else None)
        full_parsed.append((item, parsed))
        if parsed is not None:
            full_count += 1
    if full_count >= max(1, len(trends) // 2):
        resolved: list[tuple[dict[str, Any], pd.Timestamp]] = []
        for item, parsed in full_parsed:
            if parsed is not None:
                resolved.append((item, parsed))
        return resolved

    # 路径 2：仅 HH:MM —— 顺序回绕 + 锚定最后一点
    clock_times: list[_dt.time] = []
    for item in trends:
        key = _trend_minute_key(item.get("datetime") if isinstance(item, dict) else item)
        try:
            clock_times.append(_dt.datetime.strptime(key, "%H:%M").time())
        except ValueError:
            clock_times.append(_dt.time(0, 0))

    day_offsets: list[int] = []
    day_offset = 0
    prev_mins = -1
    for clock in clock_times:
        mins = clock.hour * 60 + clock.minute
        # 明显回绕（如 23:59 → 00:00）；允许小幅乱序不抬日
        if prev_mins >= 0 and mins + 60 < prev_mins:
            day_offset += 1
        day_offsets.append(day_offset)
        prev_mins = mins

    last_clock = clock_times[-1]
    if last_clock <= now_bjt_dt.time():
        last_date = now_bjt_dt.date()
    else:
        # 例如现在 01:00、最后分时 14:30 → 数据属于昨天收盘
        last_date = now_bjt_dt.date() - _dt.timedelta(days=1)

    first_date = last_date - _dt.timedelta(days=day_offsets[-1])
    resolved = []
    for item, clock, offset in zip(trends, clock_times, day_offsets, strict=False):
        abs_dt = _dt.datetime.combine(first_date + _dt.timedelta(days=offset), clock)
        resolved.append((item, pd.Timestamp(abs_dt)))
    return resolved


def _infer_kline_freq(df: pd.DataFrame) -> tuple[str, str, str, pd.Timedelta]:
    date_series = _frame_column(df, "日期")
    sorted_dates = _sort_series(date_series, ascending=True).reset_index(drop=True)
    deltas = sorted_dates.diff().dropna()
    if deltas.empty:
        median_delta = pd.Timedelta(days=1)
    else:
        median_seconds = float(deltas.dt.total_seconds().median())
        median_delta = pd.Timedelta(seconds=median_seconds)
    # 构造器静态返回 Timedelta | NaTType；deltas 非空时中位秒数恒有限，不会产生 NaT
    assert isinstance(median_delta, pd.Timedelta)

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
    df = df.dropna(subset=["日期"])
    df = _reset_df(_sort_df(df, "日期"), drop=True)
    if df.empty:
        return ErroText["notData"]

    _, freq_label, tickformat, median_delta = _infer_kline_freq(df)
    date_column = _frame_column(df, "日期")
    x_min: pd.Timestamp = date_column.min()
    x_max: pd.Timestamp = date_column.max()

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
    chart_df = chart_df.dropna(subset=["open", "high", "low", "close"])
    if chart_df.empty:
        return ErroText["notData"]

    turnover = _numeric_series(df["换手率"]) if "换手率" in df else pd.Series(dtype=float)
    df["is_max"] = turnover == turnover.rolling(window=3, center=True).max()
    max_turnovers = df[df["is_max"] & (turnover > 0)]
    assert isinstance(max_turnovers, pd.DataFrame), "布尔掩码索引恒返回 DataFrame"

    dates = _frame_column(df, "日期")
    diffs = dates.diff()
    threshold = pd.Timedelta(seconds=median_delta.total_seconds() * 1.5)
    breaks: list[dict[str, list[pd.Timestamp]]] = []
    for index in range(1, len(dates)):
        if pd.notna(diffs.iloc[index]) and diffs.iloc[index] > threshold:
            breaks.append(dict(bounds=[dates.iloc[index - 1], dates.iloc[index]]))
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


def _empty_trend_row(dt_obj: object) -> dict[str, Any]:
    return {
        "datetime": dt_obj,
        "price": None,
        "open": None,
        "high": None,
        "low": None,
        "amount": None,
        "money": None,
        "avg_price": None,
    }


def _rows_from_resolved_trends(
    resolved: list[tuple[dict[str, Any], pd.Timestamp]],
    *,
    code_id: str,
    now_bjt: Any,
    fill_session_future: bool,
    fill_session_gaps: bool,
) -> list[dict[str, Any]]:
    """以 trends 绝对时间为真相源构图。

    会话模板（``get_trading_datetimes_bjt``）**只**用于：
    - ``fill_session_future``：盘中尚未走到的未来分钟占位；
    - ``fill_session_gaps``：已有数据区间内的空分钟（单股分时轴更完整）。

    绝不把数据点按 HH:MM 重新贴进模板——那才会把跨天品种甩到「次日」。
    """
    if not resolved:
        return []

    existing_by_ts: dict[pd.Timestamp, dict[str, Any]] = {}
    for item, ts in resolved:
        minute_ts = pd.Timestamp(ts).floor("min")
        existing_by_ts[minute_ts] = {**item, "datetime": minute_ts}

    data_times = sorted(existing_by_ts.keys())
    rows: list[dict[str, Any]] = [existing_by_ts[t] for t in data_times]

    if not fill_session_future and not fill_session_gaps:
        return rows

    session_times = [
        pd.Timestamp(t).floor("min") for t in get_trading_datetimes_bjt(code_id, now_bjt=now_bjt)
    ]
    if not session_times:
        return rows

    data_min, data_max = data_times[0], data_times[-1]
    have = set(data_times)
    extra: list[dict[str, Any]] = []

    if fill_session_gaps:
        # 只在「已有数据覆盖的时间范围内」补洞，不外推到错误的一天
        for minute_ts in session_times:
            if minute_ts < data_min or minute_ts > data_max:
                continue
            if minute_ts not in have:
                extra.append(_empty_trend_row(minute_ts.to_pydatetime()))

    if fill_session_future and is_market_active_now(code_id, now_bjt=now_bjt):
        for minute_ts in session_times:
            if minute_ts <= data_max:
                continue
            if minute_ts not in have:
                extra.append(_empty_trend_row(minute_ts.to_pydatetime()))

    if not extra:
        return rows
    rows.extend(extra)
    rows.sort(
        key=lambda row: pd.Timestamp(row["datetime"])
        if row.get("datetime") is not None
        else pd.Timestamp.min
    )
    return rows


def _full_single_trends(raw_data: RawDict) -> list[dict[str, Any]]:
    import datetime as _dt

    code_id = str(raw_data["file_name"] if "file_name" in raw_data else "").split("_")[0]
    trends = list(raw_data["trends"]) if "trends" in raw_data and raw_data["trends"] else []
    if not trends:
        return []

    now_bjt = _dt.datetime.now()
    resolved = _resolve_trend_absolute_datetimes(trends, now_bjt=now_bjt)
    if not resolved:
        return trends

    return _rows_from_resolved_trends(
        resolved,
        code_id=code_id,
        now_bjt=now_bjt,
        fill_session_future=True,
        fill_session_gaps=True,
    )


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

    price_column = _frame_column(price_history_pd, "price")
    max_price = float(price_column.max())
    min_price = float(price_column.min())
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

    prices = price_column
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
    # 代码：优先 f57；否则从 file_name 取 secid（如 100.KS11）
    stock_code = ""
    if "f57" in raw and raw["f57"] not in (None, "", "-"):
        stock_code = str(raw["f57"])
    else:
        file_name = str(raw_data["file_name"] if "file_name" in raw_data else "")
        secid = file_name.split("_")[0]
        if "." in secid:
            stock_code = secid.split(".", 1)[1]
        elif secid:
            stock_code = secid
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
        stock_code=stock_code,
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
    import datetime as _dt

    max_fluctuation = 0.0
    processed_stocks: list[MultiStockItem] = []
    now_bjt = _dt.datetime.now()

    for raw_data in raw_data_list:
        if not isinstance(raw_data, dict):
            continue
        raw = raw_data["data"]
        raw_open = raw["f60"] if "f60" in raw else None
        open_price = _as_optional_float(raw_open)
        if open_price is None:
            stock_name = raw["f58"] if "f58" in raw else "Unknown"
            logger.warning(f"[SayuStock] Skipping {stock_name} due to invalid open price: {raw_open}.")
            continue
        code_id = str(raw_data["file_name"] if "file_name" in raw_data else "").split("_")[0]
        trends = list(raw_data["trends"]) if "trends" in raw_data and raw_data["trends"] else []

        # 数据绝对时间为准；会话模板仅在盘中补未来占位，不重贴历史点
        resolved = _resolve_trend_absolute_datetimes(trends, now_bjt=now_bjt)
        rows = _rows_from_resolved_trends(
            resolved,
            code_id=code_id,
            now_bjt=now_bjt,
            fill_session_future=True,
            fill_session_gaps=False,
        )
        if not rows:
            continue

        price_history_pd = pd.DataFrame(rows)
        price_history_pd["dt"] = pd.to_datetime(price_history_pd["datetime"], errors="coerce")
        price_history_pd = price_history_pd.sort_values("dt", kind="mergesort")
        price_history_pd["price"] = _numeric_series(price_history_pd["price"])
        price_history_pd["money"] = _numeric_series(price_history_pd["money"], fill_value=0)
        price_history_pd["percentage_change"] = ((price_history_pd["price"] / open_price) - 1) * 100

        change_column = _frame_column(price_history_pd, "percentage_change")
        current_max = float(change_column.max())
        current_min = float(change_column.min())
        if not np.isnan(current_max):
            max_fluctuation = max(max_fluctuation, abs(current_max))
        if not np.isnan(current_min):
            max_fluctuation = max(max_fluctuation, abs(current_min))
        processed_stocks.append(
            MultiStockItem(
                name=str(raw["f58"]),
                df=price_history_pd,
                total_volume=float(_frame_column(price_history_pd, "money").sum()),
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
        last_change_series = _frame_column(stock.df, "percentage_change").dropna()
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
        df = df.dropna(subset=["日期"])
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
    elif sector is not None and sector in grouped_by_category:
        categories_to_process = [sector]
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
