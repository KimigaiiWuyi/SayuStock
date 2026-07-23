"""分时图与多股分时对比图。"""

import re

from gsuid_core.logger import logger
from matplotlib import patheffects

from .chart_base import (
    BG_COLOR,
    FG_COLOR,
    UP_COLOR,
    DOWN_COLOR,
    GRID_COLOR,
    MPL_COLORS,
    Axes,
    Pane,
    Chart,
    HLine,
    Price,
    Figure,
    HPacker,
    NDArray,
    JsonDict,
    TextArea,
    DrawResult,
    FuncFormatter,
    AnnotationBbox,
    np,
    pd,
    _setup_mpl,
    _style_axis,
    _fig_to_image,
    _frame_column,
    _draw_in_thread,
    _mpl_bar_colors,
    _numeric_series,
    _datetime_series,
    _format_money_axis,
    _axes_top_to_bottom,
    _apply_intraday_10min_ticks,
)
from .render_data import (
    SingleStockRenderData,
    build_multi_stock_render_data,
    build_single_stock_render_data,
)
from ..utils.constant import ErroText


def _clean_stock_display_name(name: str) -> str:
    """去掉名称尾部类型后缀，如「韩国KOSPI (指数)」→「韩国KOSPI」。"""
    cleaned = re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", name).strip()
    return cleaned or name


def _format_price_display(price: object) -> str:
    if price is None or price == "" or price == "-":
        return "—"
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        value = float(price)
        if abs(value) >= 1000:
            return f"{value:,.2f}"
        if abs(value) >= 1:
            return f"{value:.2f}"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    text = str(price).strip()
    try:
        return _format_price_display(float(text))
    except (TypeError, ValueError):
        return text or "—"


def _draw_single_stock_bg_watermark(ax: Axes, stock: SingleStockRenderData) -> None:
    """透明大字两行水印：名称·代码 / 现价 涨跌。

    - 上涨：贴主图底部（曲线多在上方时更易读）
    - 下跌：贴主图顶部
    无底框，低 zorder，曲线压在文字之上。
    """
    is_up = stock.gained >= 0
    accent = UP_COLOR if is_up else DOWN_COLOR
    name = _clean_stock_display_name(str(stock.stock_name or "").strip() or "—")
    code = str(stock.stock_code or "").strip()
    price_text = _format_price_display(stock.new_price)
    change_text = str(stock.custom_info or "").strip() or "—"
    line1 = f"{name}  ·  {code}" if code else name
    line2 = f"{price_text}   {change_text}"

    # 涨 → 主图底部两行；跌 → 主图顶部两行（避开曲线通常聚集的一侧）
    if is_up:
        y1, y2 = 0.16, 0.07
    else:
        y1, y2 = 0.90, 0.81

    stroke = [patheffects.withStroke(linewidth=4.0, foreground="#050505", alpha=0.55)]
    base = {
        "transform": ax.transAxes,
        "ha": "center",
        "va": "center",
        "fontweight": "bold",
        "zorder": 0.15,
        "clip_on": True,
        "path_effects": stroke,
    }
    # 半透明：名称稍淡，现价+涨跌更醒目但仍透出网格
    ax.text(0.5, y1, line1, fontsize=40, color=FG_COLOR, alpha=0.42, **base)
    ax.text(0.5, y2, line2, fontsize=58, color=accent, alpha=0.50, **base)


async def to_single_fig(raw_data: JsonDict) -> DrawResult:
    return await _draw_in_thread(draw_single_stock_chart, raw_data)


def draw_single_stock_chart(raw_data: JsonDict) -> DrawResult:
    _setup_mpl()
    logger.info("[SayuStock] 开始获取图形...")
    data = build_single_stock_render_data(raw_data)
    if isinstance(data, str):
        return data
    stock = data
    df = stock.df

    datetimes = _datetime_series(df["dt"])
    percent_series = _numeric_series(df["percentage_change"])
    valid_time_mask = datetimes.notna()
    valid_data_mask = valid_time_mask & percent_series.notna()
    if not bool(valid_data_mask.any()):
        return ErroText["notData"]

    valid_percents = np.asarray(percent_series[valid_time_mask])
    valid_bar_colors = np.asarray(_mpl_bar_colors(stock.bar_colors), dtype=object)[np.asarray(valid_time_mask)]
    prices = pd.DataFrame(
        {
            "open": valid_percents,
            "high": valid_percents,
            "low": valid_percents,
            "close": valid_percents,
            "volume": np.asarray(_numeric_series(df["money"], fill_value=0)[valid_time_mask]),
            "bar_color": valid_bar_colors,
        },
        index=pd.DatetimeIndex(np.asarray(datetimes[valid_time_mask]), name="date"),
    )
    prices = prices.sort_index()

    title_color = UP_COLOR if stock.gained >= 0 else DOWN_COLOR
    chart = Chart(
        prices,
        title=stock.title_text,
        figsize=(22.2, 16.7),
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
        Price("close", width=2.2, color="white"),
        HLine(0, color="#f1c40f", linestyle="-."),
        Pane("below", height_ratio=0.28),
    )
    chart.add_legends()

    fig: Figure = chart.figure
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for index, ax in enumerate(axes):
        _style_axis(ax)
        ax.tick_params(axis="x", rotation=20)
        if index == 0:
            percent_limit = max(stock.max_fluctuation * 100 + 1.0, 1.0)
            ax.set_ylabel("涨跌幅")
            ax.set_ylim(-percent_limit, percent_limit)
            ax.patch.set_alpha(0.0)
            ax.axhspan(0, percent_limit, facecolor=UP_COLOR, alpha=0.16, zorder=0.2)
            ax.axhspan(-percent_limit, 0, facecolor=DOWN_COLOR, alpha=0.16, zorder=0.2)
            ax.set_axisbelow(False)
            tick_step = 2 if percent_limit > 8 else 1
            tick_start = int(np.floor(-percent_limit))
            tick_end = int(np.ceil(percent_limit))
            tick_values = [value for value in range(tick_start, tick_end + 1) if value % tick_step == 0]
            ax.set_yticks(tick_values)
            ax.set_yticklabels([f"{value}%" for value in tick_values])
            ax.tick_params(labelbottom=False)
        else:
            ax.clear()
            _style_axis(ax)
            ax.set_ylabel("量能")
            ax.tick_params(axis="x", rotation=20)
            ax.tick_params(labelbottom=True)
            ax.yaxis.set_major_formatter(FuncFormatter(_format_money_axis))
            _apply_intraday_10min_ticks(ax, prices.index)
            bar_colors = [str(value) for value in prices["bar_color"]]
            for line in list(ax.lines):
                line.remove()
            for collection in list(ax.collections):
                collection.remove()
            for container in list(ax.containers):
                container.remove()
            for patch in list(ax.patches):
                patch.remove()
            volume_values = np.asarray(prices["volume"], dtype=float)
            max_height = float(np.nanmax(volume_values)) if len(volume_values) > 0 else 0.0
            if max_height > 0:
                volume_top = max_height * 1.18
                ax.set_ylim(0, volume_top)
                ax.set_ybound(0, volume_top)
                ax.margins(y=0.0)
                ax.set_autoscale_on(False)
                bars = ax.bar(
                    np.arange(len(volume_values)),
                    np.minimum(volume_values, volume_top),
                    color=bar_colors,
                    edgecolor=bar_colors,
                    alpha=0.72,
                    width=0.82,
                    label="量能",
                    clip_on=True,
                    zorder=1,
                )
                for bar in bars:
                    bar.set_clip_on(True)
                    bar.set_clip_box(ax.bbox)
                    bar.set_clip_path(ax.patch)
                ax.set_ylim(0, volume_top)
                ax.set_ybound(0, volume_top)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(BG_COLOR)
            legend.get_frame().set_edgecolor(GRID_COLOR)
            for text in legend.get_texts():
                text.set_color(FG_COLOR)

    if axes:
        # 背景水印：主图区大字标的信息；曲线提到更高 zorder，避免被挡住
        _draw_single_stock_bg_watermark(axes[0], stock)
        for line in list(axes[0].lines):
            line.set_zorder(4)
        for collection in list(axes[0].collections):
            collection.set_zorder(4)
        axes[0].set_title(stock.title_text, color=title_color, fontsize=24, fontweight="bold", pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.988, top=0.88, bottom=0.10, hspace=0.04)
    return _fig_to_image(fig)


async def to_multi_fig(raw_data_list: list[JsonDict]) -> DrawResult:
    return await _draw_in_thread(draw_multi_stock_chart, raw_data_list)


def draw_multi_stock_chart(raw_data_list: list[JsonDict]) -> DrawResult:
    _setup_mpl()
    logger.info("[SayuStock] Starting to generate multi-stock figure with multi-line title...")
    data = build_multi_stock_render_data(raw_data_list)
    if isinstance(data, str):
        return data
    multi = data

    stock_frames: list[pd.DataFrame] = []
    all_datetimes: list[pd.Timestamp] = []
    for item in multi.stocks:
        item_datetimes = _datetime_series(item.df["dt"])
        item_valid_time = item_datetimes.notna()
        item_frame = pd.DataFrame(
            {
                "percentage_change": np.asarray(_numeric_series(item.df["percentage_change"])[item_valid_time]),
                "money": np.asarray(_numeric_series(item.df["money"], fill_value=0)[item_valid_time]),
            },
            index=pd.DatetimeIndex(np.asarray(item_datetimes[item_valid_time]), name="date"),
        )
        item_frame = item_frame.sort_index()
        stock_frames.append(item_frame)
        all_datetimes.extend(item_frame.index.to_list())

    if not all_datetimes:
        return ErroText["notData"]

    full_index = pd.DatetimeIndex(sorted(set(all_datetimes)), name="date")
    price_columns: dict[str, NDArray[np.float64]] = {}
    volume_columns: dict[str, NDArray[np.float64]] = {}
    stock_labels: list[str] = []
    stock_colors: list[str] = []
    volume_total = pd.Series(0.0, index=full_index)
    has_valid_price = False
    for stock_index, item in enumerate(multi.stocks):
        item_frame = stock_frames[stock_index].reindex(full_index)
        col_name = f"stock_{stock_index}"
        vol_name = f"vol_{stock_index}"
        item_change = _frame_column(item_frame, "percentage_change")
        item_volume_series = _frame_column(item_frame, "money").fillna(0)
        has_valid_price = has_valid_price or bool(item_change.notna().any())
        price_columns[col_name] = np.asarray(item_change, dtype=float)
        volume_columns[vol_name] = np.asarray(item_volume_series, dtype=float)
        stock_labels.append(item.name)
        stock_colors.append(MPL_COLORS[stock_index % len(MPL_COLORS)])
        volume_total = volume_total.add(item_volume_series, fill_value=0)

    if not has_valid_price:
        return ErroText["notData"]

    first_col = next(iter(price_columns))
    base_close = price_columns[first_col]
    prices = pd.DataFrame(
        {
            "open": base_close,
            "high": base_close,
            "low": base_close,
            "close": base_close,
            "volume": np.asarray(volume_total, dtype=float),
            **price_columns,
            **volume_columns,
        },
        index=full_index,
    )
    prices = prices.sort_index()
    stock_volumes: list[NDArray[np.float64]] = []
    for stock_index in range(len(multi.stocks)):
        vol_name = f"vol_{stock_index}"
        stock_volumes.append(np.asarray(prices[vol_name], dtype=float))

    chart = Chart(
        prices,
        title="分时涨跌幅对比",
        figsize=(22.2, 16.7),
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
    plot_items: list[object] = [HLine(0, color="#f1c40f", linestyle="--")]
    for stock_index, col_name in enumerate(price_columns):
        plot_items.append(Price(col_name, width=2.0, color=stock_colors[stock_index]))
    chart.plot(
        *plot_items,
        Pane("below", height_ratio=0.35),
    )
    chart.add_legends()

    fig: Figure = chart.figure
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for ax_index, ax in enumerate(axes):
        _style_axis(ax)
        ax.tick_params(axis="x", rotation=20)
        if ax_index == 0:
            percent_limit = max(abs(multi.y_axis_min), abs(multi.y_axis_max), 1.0)
            tick_step = 2 if percent_limit > 8 else 1
            tick_start = int(np.floor(-percent_limit))
            tick_end = int(np.ceil(percent_limit))
            tick_values = [value for value in range(tick_start, tick_end + 1) if value % tick_step == 0]
            ax.set_ylim(-percent_limit, percent_limit)
            ax.patch.set_alpha(0.0)
            ax.axhspan(0, percent_limit, facecolor=UP_COLOR, alpha=0.16, zorder=0.2)
            ax.axhspan(-percent_limit, 0, facecolor=DOWN_COLOR, alpha=0.16, zorder=0.2)
            ax.set_axisbelow(False)
            ax.set_yticks(tick_values)
            ax.set_yticklabels([f"{value}%" for value in tick_values])
            ax.set_ylabel("涨跌幅")
            ax.tick_params(labelbottom=False)
            ax.grid(True, axis="y", color=GRID_COLOR, alpha=0.42, linewidth=0.8)
            x_right = max(len(prices) - 1, 0)
            label_offsets: dict[int, int] = {}
            for stock_index, col_name in enumerate(price_columns):
                series = _frame_column(prices, col_name).dropna()
                if series.empty:
                    continue
                last_timestamp = series.index[-1]
                last_positions = np.flatnonzero(prices.index == last_timestamp)
                last_position = int(last_positions[-1]) if len(last_positions) > 0 else len(prices) - 1
                last_value = float(series.iloc[-1])
                bucket = int(round(last_value * 2))
                offset_count = label_offsets.get(bucket, 0)
                label_offsets[bucket] = offset_count + 1
                y_offset = (offset_count - 1) * 13 if offset_count > 0 else 0
                ax.scatter(
                    [last_position],
                    [last_value],
                    color=stock_colors[stock_index],
                    edgecolor=BG_COLOR,
                    s=34,
                    zorder=5,
                )
                name_area = TextArea(
                    stock_labels[stock_index],
                    textprops={"color": stock_colors[stock_index], "fontsize": 11, "fontweight": "bold"},
                )
                pct_area = TextArea(
                    f" {last_value:+.2f}%",
                    textprops={
                        "color": UP_COLOR if last_value >= 0 else DOWN_COLOR,
                        "fontsize": 11,
                        "fontweight": "bold",
                    },
                )
                label_box = HPacker(children=[name_area, pct_area], align="center", pad=0, sep=1)
                label_artist = AnnotationBbox(
                    label_box,
                    (last_position, last_value),
                    xybox=(10, y_offset),
                    xycoords="data",
                    boxcoords="offset points",
                    box_alignment=(0, 0.5),
                    frameon=True,
                    pad=0.25,
                    bboxprops={"facecolor": BG_COLOR, "edgecolor": stock_colors[stock_index], "alpha": 0.70},
                    arrowprops={"arrowstyle": "-", "color": stock_colors[stock_index], "alpha": 0.75, "linewidth": 0.8},
                    zorder=6,
                )
                ax.add_artist(label_artist)
            ax.set_xlim(-1, x_right + 7)
            legend = ax.get_legend()
            if legend is not None:
                for text, label in zip(legend.get_texts(), stock_labels, strict=False):
                    text.set_text(label)
                    text.set_color(FG_COLOR)
                legend.get_frame().set_facecolor(BG_COLOR)
                legend.get_frame().set_edgecolor(GRID_COLOR)
        else:
            ax.clear()
            _style_axis(ax)
            ax.set_ylabel("成交额")
            ax.tick_params(axis="x", rotation=20)
            ax.yaxis.set_major_formatter(FuncFormatter(_format_money_axis))
            _apply_intraday_10min_ticks(ax, prices.index)
            ax.tick_params(labelbottom=True)
            for line in list(ax.lines):
                line.remove()
            for collection in list(ax.collections):
                collection.remove()
            for container in list(ax.containers):
                container.remove()
            for patch in list(ax.patches):
                patch.remove()
            sorted_indices = sorted(range(len(multi.stocks)), key=lambda i: multi.stocks[i].total_volume)
            num_bars = len(prices)
            x_positions = np.arange(num_bars)
            cumulative_bottom = np.zeros(num_bars)
            max_cumulative = float(np.nansum(stock_volumes, axis=0).max()) if stock_volumes else 0.0
            volume_top = max(max_cumulative * 1.18, 1.0)
            ax.set_ylim(0, volume_top)
            ax.set_ybound(0, volume_top)
            ax.margins(y=0.0)
            ax.set_autoscale_on(False)
            for vol_idx in sorted_indices:
                bars = ax.bar(
                    x_positions,
                    np.minimum(stock_volumes[vol_idx], volume_top),
                    bottom=np.minimum(cumulative_bottom, volume_top),
                    color=stock_colors[vol_idx],
                    alpha=0.72,
                    width=0.82,
                    label=stock_labels[vol_idx],
                    clip_on=True,
                    zorder=1,
                )
                for bar in bars:
                    bar.set_clip_on(True)
                    bar.set_clip_box(ax.bbox)
                    bar.set_clip_path(ax.patch)
                cumulative_bottom = cumulative_bottom + stock_volumes[vol_idx]
            ax.set_ylim(0, volume_top)
            ax.set_ybound(0, volume_top)
            volume_legend = ax.get_legend()
            if volume_legend is not None:
                volume_legend.get_frame().set_facecolor(BG_COLOR)
                volume_legend.get_frame().set_edgecolor(GRID_COLOR)
                for text in volume_legend.get_texts():
                    text.set_color(FG_COLOR)

    if axes:
        axes[0].set_title(
            "分时涨跌幅对比",
            color=FG_COLOR,
            fontsize=22,
            fontweight="bold",
            pad=24,
        )
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.965, top=0.855, bottom=0.10, hspace=0.04)
    return _fig_to_image(fig)
