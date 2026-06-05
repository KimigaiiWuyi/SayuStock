# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import asyncio
import datetime
from io import BytesIO
from typing import Any, Union, Literal, Optional, TypedDict, cast
from dataclasses import dataclass

import numpy as np
import pandas as pd
import matplotlib
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from mplchart.chart import Chart  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from mplchart.primitives import Price  # noqa: E402
from matplotlib.offsetbox import HPacker, TextArea, AnnotationBbox  # noqa: E402

from gsuid_core.logger import logger
from gsuid_core.utils.fonts.fonts import FONT_ORIGIN_PATH
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.eastmoney import (
    EASTMONEY_REQUESTER,
    EASTMONEY_VALUE_NAME_MAP,
    EastMoneyStockItem,
    EastMoneyValueSeriesData,
)
from ..utils.stock.request import get_gg

ValueType = Literal["pe", "pb", "dy"]
BotSendContent = Union[str, bytes]

VALUE_NAME_MAP: dict[ValueType, str] = {
    "pe": EASTMONEY_VALUE_NAME_MAP["pe"],
    "pb": EASTMONEY_VALUE_NAME_MAP["pb"],
    "dy": EASTMONEY_VALUE_NAME_MAP["dy"],
}

# 与 stock_stockinfo 的个股/对比图保持一致的暗色主题样式。
BG_COLOR = "#050505"
FG_COLOR = "#f5f5f5"
AXIS_COLOR = "#d8d8d8"
GRID_COLOR = "#777777"
UP_COLOR = "#e74c3c"
DOWN_COLOR = "#00b050"
FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
MPL_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#17becf", "#e377c2"]


def _setup_mpl() -> None:
    font_candidates = FONT_CANDIDATES
    if FONT_ORIGIN_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_ORIGIN_PATH))
        core_font_name = font_manager.FontProperties(fname=str(FONT_ORIGIN_PATH)).get_name()
        font_candidates = [core_font_name, *FONT_CANDIDATES]
    plt.rcParams["font.sans-serif"] = font_candidates
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = BG_COLOR
    plt.rcParams["axes.facecolor"] = BG_COLOR
    plt.rcParams["savefig.facecolor"] = BG_COLOR


def _style_axis(ax: Axes) -> None:
    ax.set_facecolor(BG_COLOR)
    ax.tick_params(colors=AXIS_COLOR, labelsize=12)
    ax.xaxis.label.set_color(AXIS_COLOR)
    ax.yaxis.label.set_color(AXIS_COLOR)
    ax.title.set_color(FG_COLOR)
    for spine in ax.spines.values():
        spine.set_color(AXIS_COLOR)
        spine.set_linewidth(1.1)
    ax.grid(True, color=GRID_COLOR, alpha=0.36, linewidth=0.8)


def _apply_month_ticks(ax: Axes, index: pd.Index) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce"))
    if dates.empty:
        return
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    previous_month: tuple[int, int] | None = None
    for position, timestamp in enumerate(dates):
        if pd.isna(timestamp):
            continue
        current_month = (timestamp.year, timestamp.month)
        if current_month != previous_month:
            tick_positions.append(position)
            tick_labels.append(timestamp.strftime("%Y-%m"))
            previous_month = current_month
    max_ticks = 10
    if len(tick_positions) > max_ticks:
        selected = np.linspace(0, len(tick_positions) - 1, max_ticks, dtype=int)
        tick_positions = [tick_positions[i] for i in selected]
        tick_labels = [tick_labels[i] for i in selected]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)


def _fig_to_image(fig: Figure, *, dpi: int = 180) -> Image.Image:
    output = BytesIO()
    fig.savefig(output, format="png", dpi=dpi, facecolor=fig.get_facecolor(), pad_inches=0.06)
    plt.close(fig)
    output.seek(0)
    return Image.open(output).convert("RGB")


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
    start_time: Optional[datetime.datetime] = None,
    end_time: Optional[datetime.datetime] = None,
) -> BotSendContent:
    """获取东方财富PE/PB/DY历史估值数据，并使用mplchart生成对比图。"""
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

    # 根据时间范围过滤数据
    if start_time is not None or end_time is not None:
        for vs in series_list:
            mask = pd.Series([True] * len(vs.df), index=vs.df.index)
            if start_time is not None:
                mask &= vs.df["date"] >= start_time
            if end_time is not None:
                mask &= vs.df["date"] <= end_time
            vs.df = vs.df.loc[mask].reset_index(drop=True)
            if vs.df.empty:
                failed.append(f"{vs.name}({vs.code})")

        series_list = [vs for vs in series_list if not vs.df.empty]

    if not series_list:
        return f"❌在指定时间范围内未获取到{VALUE_NAME_MAP[_type]}历史数据，请尝试更宽的时间范围或不指定时间。"

    # AI注入必须发生在"数据已获取、图片未生成"的位置，确保AI能获得可分析的结构化文字。
    _ai_return_value_compare(series_list, failed, _type)
    image = await asyncio.to_thread(draw_value_compare_chart, series_list, _type)
    return cast(BotSendContent, await convert_img(image))


def _is_sector(raw_data: dict[str, Any]) -> bool:
    """检测响应是否为板块（而非个股/ETF）数据。"""
    data = raw_data.get("data", {})
    if data.get("f107") == 90:
        return True
    f58 = str(data.get("f58", ""))
    return "(板块)" in f58


async def _fetch_sector_codes(raw_data: dict[str, Any]) -> list[str]:
    """从板块响应中提取成分股代码列表（前13只）。"""
    data = raw_data.get("data", {})
    bk_code = str(data.get("f57", ""))
    if not bk_code:
        return []

    market_list_resp = await EASTMONEY_REQUESTER.get_market_list(bk_code, False, 1, 13)
    if isinstance(market_list_resp, str) or not market_list_resp.get("data"):
        return []

    stocks = market_list_resp["data"].get("diff", [])
    return [str(s.get("f12", "")) for s in stocks if s.get("f12")]


async def _parse_stock_input(_input: str) -> list[StockItem]:
    parts = [p.strip() for p in _input.replace("，", " ").replace(",", " ").split() if p.strip()]
    expanded: list[str] = []
    for part in parts:
        raw_data = await get_gg(part, "single-stock", None, None)
        if isinstance(raw_data, dict) and _is_sector(raw_data):
            codes = await _fetch_sector_codes(raw_data)
            expanded.extend(codes)
        else:
            expanded.append(part)

    stock_items = await EASTMONEY_REQUESTER.parse_stock_input(" ".join(expanded))
    return [cast(StockItem, item) for item in stock_items]


async def fetch_eastmoney_value_series(
    stock: StockItem,
    _type: ValueType,
) -> Optional[ValueSeries]:
    logger.info(f"[SayuStock] 获取东方财富{VALUE_NAME_MAP[_type]}历史估值: {stock['code']}")
    requester_stock = cast(EastMoneyStockItem, stock)
    if _type == "pe":
        raw_series = await EASTMONEY_REQUESTER.get_pe_series(requester_stock)
    elif _type == "pb":
        raw_series = await EASTMONEY_REQUESTER.get_pb_series(requester_stock)
    else:
        raw_series = await EASTMONEY_REQUESTER.get_dy_series(requester_stock)
    if isinstance(raw_series, str):
        logger.warning(raw_series)
        return None

    series_data = cast(EastMoneyValueSeriesData, raw_series)
    rows = series_data["rows"]
    if not rows:
        return None

    sorted_rows = sorted(rows, key=lambda row: pd.Timestamp(str(row["date"])))
    sorted_items: list[tuple[pd.Timestamp, float, list[dict[str, Any]]]] = []
    for row in sorted_rows:
        events_field = row.get("events") if isinstance(row, dict) else None
        events_list: list[dict[str, Any]] = []
        if isinstance(events_field, list):
            for item in events_field:
                if not isinstance(item, dict):
                    continue
                ex_date_raw = item.get("ex_date")
                if not ex_date_raw:
                    continue
                try:
                    ex_ts = pd.Timestamp(str(ex_date_raw)[:10])
                except (ValueError, TypeError):
                    continue
                # 透传额外报告期/原始除权日信息（仅 DY 会用到），便于调试与 AI 摘要。
                raw_report_date = item.get("report_date")
                raw_ex_dates = item.get("ex_dates")
                events_list.append(
                    {
                        "ex_date": ex_ts,
                        "report_date": (pd.Timestamp(str(raw_report_date)[:10]) if raw_report_date else None),
                        "ex_dates": (
                            [pd.Timestamp(str(d)[:10]) for d in raw_ex_dates]
                            if isinstance(raw_ex_dates, list)
                            else None
                        ),
                        "bonus_per_share": float(item.get("bonus_per_share", 0.0) or 0.0),
                        "contribution_pct": float(item.get("contribution_pct", 0.0) or 0.0),
                        "is_planned": bool(item.get("is_planned", False)),
                    }
                )
        sorted_items.append(
            (
                cast(pd.Timestamp, pd.Timestamp(str(row["date"]))),
                float(row["value"]),
                events_list,
            )
        )
    output_df = pd.DataFrame(
        {
            "date": [item[0] for item in sorted_items],
            "value": [item[1] for item in sorted_items],
            "events": [item[2] for item in sorted_items],
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
    _setup_mpl()
    merged = _merge_value_series(series_list)
    if merged.empty:
        raise ValueError("没有可绘制的估值数据")

    value_columns = [_safe_column_name(item) for item in series_list]
    labels = [item.label for item in series_list]

    prices = cast(pd.DataFrame, merged.set_index("date").sort_index())
    # mplchart 需要 OHLC 骨架字段，用首条曲线占位即可，实际绘制依赖各列。
    first_series = cast(pd.Series, prices[value_columns[0]].ffill().bfill())
    prices["open"] = first_series
    prices["high"] = first_series
    prices["low"] = first_series
    prices["close"] = first_series
    prices["volume"] = 0.0

    stock_names = " vs ".join(item.name for item in series_list)
    title = f"{stock_names} {VALUE_NAME_MAP[_type]}历史走势对比"
    chart = Chart(
        prices,
        title=title,
        figsize=(25.5, 16.5),
        bgcolor=BG_COLOR,
        raw_dates=False,
        color_scheme={
            "colorup": UP_COLOR,
            "colordn": DOWN_COLOR,
            "bgcolor": BG_COLOR,
            "text": FG_COLOR,
            "grid": GRID_COLOR,
        },
    )
    chart.plot(
        *(
            Price(column, width=2.2, color=MPL_COLORS[index % len(MPL_COLORS)])
            for index, column in enumerate(value_columns)
        ),
    )
    chart.add_legends()

    fig = cast(Figure, chart.figure)
    fig.set_facecolor(BG_COLOR)
    ax = cast(Axes, chart.main_axes())
    _style_axis(ax)
    ax.set_ylabel(VALUE_NAME_MAP[_type])
    ax.tick_params(axis="x", rotation=20)
    for line in ax.lines:
        line.set_zorder(3)

    value_frame = cast(pd.DataFrame, prices[value_columns])
    data_min = float(cast(float, value_frame.min(skipna=True).min(skipna=True)))
    data_max = float(cast(float, value_frame.max(skipna=True).max(skipna=True)))
    span = max(data_max - data_min, 1.0)
    padding = span * 0.08
    ax.set_ylim(data_min - padding, data_max + padding)

    # 在每条曲线末端标注 名称 + 当前PE/PB，并对靠近的标签做纵向避让。
    x_right = max(len(prices) - 1, 0)
    label_offsets: dict[int, int] = {}
    for index, column in enumerate(value_columns):
        series = cast(pd.Series, prices[column]).dropna()
        if series.empty:
            continue
        last_timestamp = series.index[-1]
        last_positions = np.flatnonzero(prices.index == last_timestamp)
        last_position = int(last_positions[-1]) if len(last_positions) > 0 else len(prices) - 1
        last_value = float(series.iloc[-1])
        color = MPL_COLORS[index % len(MPL_COLORS)]
        bucket = int(round(last_value / max(span, 1.0) * 40))
        offset_count = label_offsets.get(bucket, 0)
        label_offsets[bucket] = offset_count + 1
        y_offset = (offset_count - 1) * 13 if offset_count > 0 else 0
        ax.scatter(
            [last_position],
            [last_value],
            color=color,
            edgecolor=BG_COLOR,
            s=34,
            zorder=5,
        )
        name_area = TextArea(
            labels[index],
            textprops={"color": color, "fontsize": 11, "fontweight": "bold"},
        )
        value_area = TextArea(
            f" {last_value:.2f}",
            textprops={"color": FG_COLOR, "fontsize": 11, "fontweight": "bold"},
        )
        label_box = HPacker(children=[name_area, value_area], align="center", pad=0, sep=1)
        label_artist = AnnotationBbox(
            label_box,
            (last_position, last_value),
            xybox=(10, y_offset),
            xycoords="data",
            boxcoords="offset points",
            box_alignment=(0, 0.5),
            frameon=True,
            pad=0.25,
            bboxprops={"facecolor": BG_COLOR, "edgecolor": color, "alpha": 0.70},
            arrowprops={"arrowstyle": "-", "color": color, "alpha": 0.75, "linewidth": 0.8},
            zorder=6,
        )
        ax.add_artist(label_artist)
    ax.set_xlim(-1, x_right + 7)
    _apply_month_ticks(ax, prices.index)
    ax.tick_params(axis="x", rotation=20, labelbottom=True)

    legend = ax.get_legend()
    if legend is not None:
        legend.get_frame().set_facecolor(BG_COLOR)
        legend.get_frame().set_edgecolor(AXIS_COLOR)
        for text, label in zip(legend.get_texts(), labels):
            text.set_text(label)
            text.set_color(FG_COLOR)

    # 股息率(DY)图：标注每只标的在窗口内发生的分红事件。
    if _type == "dy":
        _annotate_dividend_events(ax, prices, series_list, value_columns, span, data_min, data_max)

    ax.set_title(title, fontsize=24, fontweight="bold", color=FG_COLOR, pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.06, right=0.965, top=0.9, bottom=0.16)
    return _fig_to_image(fig)


def _merge_value_series(series_list: list[ValueSeries]) -> pd.DataFrame:
    merged: Optional[pd.DataFrame] = None
    for item in series_list:
        column = _safe_column_name(item)
        # 只选取 date 和 value 列，避免 events 等其他列在 merge 时产生列名冲突
        value_df = item.df[["date", "value"]].rename(columns={"value": column}).copy()
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


def _annotate_dividend_events(
    ax: Axes,
    prices: pd.DataFrame,
    series_list: list[ValueSeries],
    value_columns: list[str],
    span: float,
    data_min: float,
    data_max: float,
) -> None:
    """在股息率(DY)图上标注每只标的的每次分红事件。

    每个事件以一个竖直短线（除权日对齐曲线 y 位置）、一个散点、以及一行文本标签
    （除权日 + 每股分红 + 贡献股息率比例）表示。同一交易日可能发生多次分红，
    会在纵向错开避免遮挡。同一只标的事件过多时，按贡献比例优先取 Top N。
    """
    max_event_labels_per_series = 8
    index_to_position: dict[pd.Timestamp, int] = {cast(pd.Timestamp, ts): pos for pos, ts in enumerate(prices.index)}

    for series_index, (column, value_series) in enumerate(zip(value_columns, series_list)):
        if "events" not in value_series.df.columns:
            logger.debug(f"[SayuStock][DY-Annotate] {value_series.label} 无 events 列, 跳过标注")
            continue
        event_count = 0
        for _, row in value_series.df.iterrows():
            ev = row.get("events")
            if isinstance(ev, list) and len(ev) > 0:
                event_count += 1
        logger.debug(
            f"[SayuStock][DY-Annotate] {value_series.label} events列存在, 有events的行数={event_count}, 总行数={len(value_series.df)}"
        )
        color = MPL_COLORS[series_index % len(MPL_COLORS)]
        y_values = cast(pd.Series, prices[column])
        y_max = float(y_values.max()) if not y_values.empty else data_max
        y_min = float(y_values.min()) if not y_values.empty else data_min
        vertical_pad = max((y_max - y_min) * 0.18, span * 0.04, 0.05)

        # 为不同标的事件准备纵向偏移阶梯，避免互相重叠。
        label_offsets_y: dict[pd.Timestamp, int] = {}

        # 用 (ex_date, event) 集合去重，避免同一事件在多行重复出现。
        seen_events: set[tuple[str, str]] = set()
        events_unique: list[tuple[pd.Timestamp, dict[str, Any]]] = []
        for _, row in value_series.df.iterrows():
            raw_events = row.get("events")
            if not isinstance(raw_events, list) or len(raw_events) == 0:
                continue
            for event in raw_events:
                if not isinstance(event, dict):
                    continue
                # 跳过预披露/未实施的分红事件，避免在图上标注未除权的方案。
                if event.get("is_planned"):
                    continue
                ex_date = event.get("ex_date")
                if ex_date is None:
                    continue
                try:
                    ex_ts = pd.Timestamp(str(ex_date))
                except (ValueError, TypeError):
                    continue
                dedup_key = (str(ex_date), str(event.get("report_date", "")))
                if dedup_key in seen_events:
                    continue
                seen_events.add(dedup_key)
                events_unique.append((cast(pd.Timestamp, ex_ts), cast(dict[str, Any], event)))

        # 按贡献比例从大到小排序，只标注贡献最大的若干个事件，防止图过密。
        events_unique.sort(
            key=lambda item: abs(float(item[1].get("contribution_pct", 0.0) or 0.0)),
            reverse=True,
        )
        top_events = events_unique[:max_event_labels_per_series]
        # 按时序排列后逐个画标签与竖线。
        top_events.sort(key=lambda item: item[0])

        for ex_ts, event in top_events:
            # 找到该事件日最近的 K 线 x 位置（该日可能无交易）。
            # 使用 index_to_position 映射，它基于 prices.index（已合并+过滤后的日期）。
            position = index_to_position.get(ex_ts)
            if position is None:
                # ex_ts 可能不在 prices.index 中，找最近的 <= ex_ts 的位置
                closest = [pos for ts, pos in index_to_position.items() if ts <= ex_ts]
                if not closest:
                    logger.debug(f"[SayuStock][DY-Annotate] 事件 {ex_ts} 无对应位置, 跳过")
                    continue
                position = closest[-1]
            position = int(position)
            try:
                base_y = float(y_values.iloc[position])
            except (IndexError, ValueError):
                logger.debug(f"[SayuStock][DY-Annotate] 事件 {ex_ts} position={position} 取y值失败, 跳过")
                continue
            if pd.isna(base_y):
                base_y = float(y_values.dropna().iloc[-1]) if not y_values.dropna().empty else data_min

            bonus = float(event.get("bonus_per_share", 0.0) or 0.0)
            contribution = float(event.get("contribution_pct", 0.0) or 0.0)
            ex_label = ex_ts.strftime("%Y-%m-%d")

            # 竖直短线表示除权日对齐位置。
            ax.vlines(
                position,
                base_y - vertical_pad * 0.45,
                base_y + vertical_pad * 0.85,
                color=color,
                alpha=0.55,
                linewidth=1.1,
                linestyles=(0, (3, 2)),
                zorder=4,
            )
            ax.scatter(
                [position],
                [base_y],
                color=color,
                edgecolor=BG_COLOR,
                s=46,
                marker="D",
                zorder=5,
            )

            offset_index = label_offsets_y.get(ex_ts, 0)
            label_offsets_y[ex_ts] = offset_index + 1
            y_text = base_y + vertical_pad * (0.95 + 0.55 * offset_index)

            text = f"{ex_label}\n每股{bonus:.2f}元\n贡献{contribution:.2f}%"
            ax.annotate(
                text,
                xy=(position, base_y),
                xytext=(8, y_text - base_y),
                textcoords="offset points",
                fontsize=8.5,
                color=color,
                fontweight="bold",
                ha="left",
                va="bottom",
                bbox={
                    "boxstyle": "round,pad=0.25",
                    "facecolor": BG_COLOR,
                    "edgecolor": color,
                    "linewidth": 0.9,
                    "alpha": 0.78,
                },
                arrowprops={
                    "arrowstyle": "-",
                    "color": color,
                    "alpha": 0.7,
                    "linewidth": 0.7,
                },
                zorder=7,
            )


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
