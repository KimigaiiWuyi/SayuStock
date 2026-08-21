"""分时图与多股分时对比图。"""

import re

from matplotlib import patheffects

from gsuid_core.logger import logger

from .chart_base import (
    BG_COLOR,
    FG_COLOR,
    UP_COLOR,
    DOWN_COLOR,
    FONT_W_MED,
    GRID_COLOR,
    MPL_COLORS,
    FONT_W_BOLD,
    FONT_W_LIGHT,
    FONT_W_SEMIBOLD,
    Axes,
    Pane,
    Chart,
    HLine,
    Price,
    Figure,
    NDArray,
    DrawResult,
    FuncFormatter,
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
    _apply_detail_legend,
    _draw_end_point_labels,
    _paint_chart_background,
    _hide_root_x_tick_labels,
    _apply_intraday_10min_ticks,
    _format_detail_legend_label,
)
from .render_data import (
    SingleStockRenderData,
    build_multi_stock_render_data,
    build_single_stock_render_data,
)
from ..utils.constant import ErroText
from ..utils.market.models import IntradaySeries


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
        "zorder": 0.15,
        "clip_on": True,
        "path_effects": stroke,
    }
    # 半透明水印：名称用细字、现价用半粗，层次更清楚
    ax.text(0.5, y1, line1, fontsize=40, color=FG_COLOR, alpha=0.42, fontweight=FONT_W_LIGHT, **base)
    ax.text(0.5, y2, line2, fontsize=58, color=accent, alpha=0.50, fontweight=FONT_W_SEMIBOLD, **base)


async def to_single_fig(series: IntradaySeries) -> DrawResult:
    return await _draw_in_thread(draw_single_stock_chart, series)


def draw_single_stock_chart(series: IntradaySeries) -> DrawResult:
    _setup_mpl()
    logger.info("[SayuStock] 开始获取图形...")
    data = build_single_stock_render_data(series)
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
    _paint_chart_background(fig)
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
                text.set_fontweight(FONT_W_MED)

    if axes:
        # 背景水印：主图区大字标的信息；曲线提到更高 zorder，避免被挡住
        _draw_single_stock_bg_watermark(axes[0], stock)
        for line in list(axes[0].lines):
            line.set_zorder(4)
        for collection in list(axes[0].collections):
            collection.set_zorder(4)
        axes[0].set_title(stock.title_text, color=title_color, fontsize=24, fontweight=FONT_W_BOLD, pad=24)
    fig.text(
        0.016,
        0.005,
        "数据来源：东方财富 | SayuStock",
        color=FG_COLOR,
        fontsize=9,
        alpha=0.65,
        fontweight=FONT_W_LIGHT,
    )
    fig.subplots_adjust(left=0.045, right=0.988, top=0.88, bottom=0.10, hspace=0.04)
    _hide_root_x_tick_labels(fig)
    return _fig_to_image(fig)


async def to_multi_fig(series_list: list[IntradaySeries]) -> DrawResult:
    return await _draw_in_thread(draw_multi_stock_chart, series_list)


def draw_multi_stock_chart(series_list: list[IntradaySeries]) -> DrawResult:
    _setup_mpl()
    logger.info("[SayuStock] Starting to generate multi-stock figure with multi-line title...")
    data = build_multi_stock_render_data(series_list)
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
                "price": np.asarray(_numeric_series(item.df["price"])[item_valid_time]),
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
    legend_labels: list[str] = []
    volume_total = pd.Series(0.0, index=full_index)
    has_valid_price = False
    for stock_index, item in enumerate(multi.stocks):
        item_frame = stock_frames[stock_index].reindex(full_index)
        col_name = f"stock_{stock_index}"
        vol_name = f"vol_{stock_index}"
        item_change = _frame_column(item_frame, "percentage_change")
        item_price = _frame_column(item_frame, "price")
        item_volume_series = _frame_column(item_frame, "money").fillna(0)
        has_valid_price = has_valid_price or bool(item_change.notna().any())
        price_columns[col_name] = np.asarray(item_change, dtype=float)
        volume_columns[vol_name] = np.asarray(item_volume_series, dtype=float)
        stock_labels.append(item.name)
        stock_colors.append(MPL_COLORS[stock_index % len(MPL_COLORS)])
        volume_total = volume_total.add(item_volume_series, fill_value=0)
        # 图例：起始价（开盘参考）、末价、完整涨跌幅
        price_valid = item_price.dropna()
        change_valid = item_change.dropna()
        if not price_valid.empty and not change_valid.empty:
            end_price = float(price_valid.iloc[-1])
            end_pct = float(change_valid.iloc[-1])
            # percentage_change = (price/open - 1)*100 → open = price / (1 + pct/100)
            open_price = end_price / (1.0 + end_pct / 100.0) if abs(end_pct + 100.0) > 1e-9 else end_price
            # 若序列首点更接近开盘，优先用反推的 open 与首价对照
            first_price = float(price_valid.iloc[0])
            first_pct = float(change_valid.iloc[0]) if len(change_valid) else 0.0
            start_from_first = first_price / (1.0 + first_pct / 100.0) if abs(first_pct + 100.0) > 1e-9 else first_price
            start_price = start_from_first if np.isfinite(start_from_first) else open_price
            legend_labels.append(_format_detail_legend_label(item.name, start_price, end_price, end_pct))
        else:
            legend_labels.append(item.name)

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
    _paint_chart_background(fig)
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
            end_entries: list[tuple[float, float, str, str, str]] = []
            for stock_index, col_name in enumerate(price_columns):
                series = _frame_column(prices, col_name).dropna()
                if series.empty:
                    continue
                last_timestamp = series.index[-1]
                last_positions = np.flatnonzero(prices.index == last_timestamp)
                last_position = int(last_positions[-1]) if len(last_positions) > 0 else len(prices) - 1
                last_value = float(series.iloc[-1])
                end_entries.append(
                    (
                        float(last_position),
                        last_value,
                        stock_labels[stock_index],
                        f" {last_value:+.2f}%",
                        stock_colors[stock_index],
                    )
                )
            ax.set_xlim(-1, x_right + max(9, 5 + len(end_entries)))

            def _end_value_color(value_text: str) -> str:
                try:
                    return UP_COLOR if float(value_text.strip().rstrip("%")) >= 0 else DOWN_COLOR
                except ValueError:
                    return FG_COLOR

            _draw_end_point_labels(ax, end_entries, value_color_fn=_end_value_color)
            _apply_detail_legend(ax, legend_labels, text_colors=stock_colors)
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
                    text.set_fontweight(FONT_W_MED)

    if axes:
        axes[0].set_title(
            "分时涨跌幅对比",
            color=FG_COLOR,
            fontsize=22,
            fontweight=FONT_W_BOLD,
            pad=24,
        )
    fig.text(
        0.016,
        0.005,
        "数据来源：东方财富 | SayuStock",
        color=FG_COLOR,
        fontsize=9,
        alpha=0.65,
        fontweight=FONT_W_LIGHT,
    )
    fig.subplots_adjust(left=0.045, right=0.965, top=0.855, bottom=0.10, hspace=0.04)
    _hide_root_x_tick_labels(fig)
    return _fig_to_image(fig)
