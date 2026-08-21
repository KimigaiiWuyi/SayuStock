"""个股对比图（多标的按首日收盘归一化的涨跌幅）。"""

from ..utils import indicators as ind
from .chart_base import (
    BG_COLOR,
    FG_COLOR,
    UP_COLOR,
    DOWN_COLOR,
    GRID_COLOR,
    MPL_COLORS,
    FONT_W_BOLD,
    FONT_W_LIGHT,
    Chart,
    HLine,
    Price,
    Figure,
    DrawResult,
    FuncFormatter,
    np,
    pd,
    _setup_mpl,
    _pct_change,
    _style_axis,
    _fig_to_image,
    _frame_column,
    _draw_in_thread,
    _numeric_series,
    _datetime_series,
    _apply_month_ticks,
    _axes_top_to_bottom,
    _apply_detail_legend,
    _format_percent_axis,
    _draw_end_point_labels,
    _paint_chart_background,
    _draw_dodged_text_labels,
    _hide_root_x_tick_labels,
    _format_detail_legend_label,
)
from .render_data import build_compare_render_data
from ..utils.constant import ErroText
from ..utils.market.models import KlineSeries


async def to_compare_fig(series_list: list[KlineSeries]) -> DrawResult:
    return await _draw_in_thread(draw_compare_chart, series_list)


def draw_compare_chart(series_list: list[KlineSeries]) -> DrawResult:
    _setup_mpl()
    data = build_compare_render_data(series_list)
    if isinstance(data, str):
        return data
    compare = data

    price_frames: list[pd.DataFrame] = []
    compare_columns: list[str] = []
    compare_labels: list[str] = []
    legend_labels: list[str] = []
    for index, item in enumerate(compare.items):
        column_name = f"compare_{index}"
        compare_columns.append(column_name)
        compare_labels.append(item.name)
        dates = _datetime_series(item.df["日期"])
        values = _numeric_series(item.df["归一化"]) * 100
        closes = _numeric_series(item.df["收盘"])
        valid_mask = dates.notna() & values.notna() & closes.notna()
        price_frames.append(
            pd.DataFrame(
                {column_name: np.asarray(values[valid_mask])},
                index=pd.DatetimeIndex(np.asarray(dates[valid_mask]), name="date"),
            )
        )
        close_vals = np.asarray(closes[valid_mask], dtype=float)
        if close_vals.size > 0:
            start_price = float(close_vals[0])
            end_price = float(close_vals[-1])
            # 完整涨跌幅以收盘价为准；归一化末点应与之接近
            legend_labels.append(
                _format_detail_legend_label(
                    item.name,
                    start_price,
                    end_price,
                    _pct_change(start_price, end_price),
                )
            )
        else:
            legend_labels.append(item.name)
    merged = pd.concat(price_frames, axis=1).sort_index().dropna(how="all")
    if merged.empty:
        return ErroText["notData"]
    first_series: pd.Series = merged.iloc[:, 0].ffill().bfill()
    prices = merged.copy()
    prices["open"] = first_series
    prices["high"] = first_series
    prices["low"] = first_series
    prices["close"] = first_series
    prices["volume"] = 0.0

    chart = Chart(
        prices,
        title="对比图",
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
        HLine(0, color="#f1c40f", linestyle="--"),
        *(
            Price(column_name, width=2.2, color=MPL_COLORS[index % len(MPL_COLORS)])
            for index, column_name in enumerate(compare_columns)
        ),
    )
    chart.add_legends()

    fig: Figure = chart.figure
    fig.set_facecolor(BG_COLOR)
    _paint_chart_background(fig)
    # 先定版面再算 display 坐标避让，避免后续调整改变标签间距
    fig.subplots_adjust(left=0.045, right=0.965, top=0.875, bottom=0.10)
    axes = _axes_top_to_bottom(fig)
    for ax_index, ax in enumerate(axes):
        _style_axis(ax)
        ax.yaxis.set_major_formatter(FuncFormatter(_format_percent_axis))
        ax.tick_params(axis="x", rotation=20)
        if ax_index == 0:
            compare_values = merged[compare_columns]
            data_min = float(compare_values.min(skipna=True).min(skipna=True))
            data_max = float(compare_values.max(skipna=True).max(skipna=True))
            span = max(data_max - data_min, 1.0)
            padding = span * 0.08
            y_min = min(data_min - padding, 0.0)
            y_max = max(data_max + padding, 0.0)
            ax.set_ylim(y_min, y_max)
            ax.patch.set_alpha(0.0)
            ax.axhspan(0, y_max, facecolor=UP_COLOR, alpha=0.16, zorder=0.2)
            ax.axhspan(y_min, 0, facecolor=DOWN_COLOR, alpha=0.16, zorder=0.2)
            ax.set_axisbelow(False)
            for line in ax.lines:
                line.set_zorder(3)
            x_right = max(len(prices) - 1, 0)

            # 曲线末端：点 + 引出线 + 纵向避让
            end_entries: list[tuple[float, float, str, str, str]] = []
            for compare_index, column_name in enumerate(compare_columns):
                series = _frame_column(prices, column_name).dropna()
                if series.empty:
                    continue
                last_timestamp = series.index[-1]
                last_positions = np.flatnonzero(prices.index == last_timestamp)
                last_position = int(last_positions[-1]) if len(last_positions) > 0 else len(prices) - 1
                last_value = float(series.iloc[-1])
                stock_color = MPL_COLORS[compare_index % len(MPL_COLORS)]
                end_entries.append(
                    (
                        float(last_position),
                        last_value,
                        compare_labels[compare_index],
                        f" {last_value:+.2f}%",
                        stock_color,
                    )
                )
            # 右侧多留白：末端标签横向扇出 + 虚线引出
            ax.set_xlim(-1, x_right + max(12, 6 + len(end_entries) * 1.4))

            def _end_value_color(value_text: str) -> str:
                try:
                    return UP_COLOR if float(value_text.strip().rstrip("%")) >= 0 else DOWN_COLOR
                except ValueError:
                    return FG_COLOR

            _draw_end_point_labels(
                ax,
                end_entries,
                min_sep_pts=24.0,
                x_base_pts=20.0,
                x_stagger_pts=10.0,
                value_color_fn=_end_value_color,
            )

            # 标注每条对比序列的最高点、最低点，并显示区间最大涨幅/回撤。
            # 区间涨幅/回撤的终点是波段自己的峰/谷，不一定是全局极值点，
            # 点位一律取 swing_points 的结果；与全局极值重合时合并成一个标签。
            def _index_position(timestamp: object) -> int:
                positions = np.flatnonzero(prices.index == timestamp)
                return int(positions[-1]) if len(positions) > 0 else 0

            # 先收集全部极值标签，统一 2D 避让后再画
            extreme_entries: list[tuple[float, float, str, str, tuple[float, float]]] = []

            def _queue_extreme(
                text: str,
                position: int,
                value: float,
                color: str,
                above: bool,
            ) -> None:
                # 极值点默认向左上/左下；密集时 AABB 避让会再推开，虚线仍连回锚点
                preferred = (-22.0, 22.0 if above else -22.0)
                extreme_entries.append((float(position), float(value), text, color, preferred))

            for compare_index, column_name in enumerate(compare_columns):
                series = _frame_column(prices, column_name).dropna()
                if len(series) < 2:
                    continue
                stock_color = MPL_COLORS[compare_index % len(MPL_COLORS)]

                max_value = float(series.max())
                min_value = float(series.min())
                max_position = _index_position(series.idxmax())
                min_position = _index_position(series.idxmin())

                swing = ind.swing_points(series)

                def _short_date(timestamp: object) -> str:
                    ts = pd.Timestamp(str(timestamp))
                    return ts.strftime("%m-%d") if isinstance(ts, pd.Timestamp) else "--"

                runup_label = None
                runup_position = -1
                if swing.max_runup > 0:
                    runup_span = (
                        f"{_short_date(series.index[swing.runup_start])} → {_short_date(series.index[swing.runup_end])}"
                    )
                    runup_label = f"区间最大涨幅 +{swing.max_runup:.2f}%\n{runup_span}"
                    runup_position = _index_position(series.index[swing.runup_end])
                drawdown_label = None
                drawdown_position = -1
                if swing.max_drawdown < 0:
                    drawdown_span = (
                        f"{_short_date(series.index[swing.drawdown_start])}"
                        f" → {_short_date(series.index[swing.drawdown_end])}"
                    )
                    drawdown_label = f"区间最大回撤 {swing.max_drawdown:.2f}%\n{drawdown_span}"
                    drawdown_position = _index_position(series.index[swing.drawdown_end])

                # 最高点（区间涨幅恰好在此结束时并入同一标签）
                max_label = f"{compare_labels[compare_index]}\n涨幅 {max_value:+.2f}%"
                if runup_label is not None and runup_position == max_position:
                    max_label += f"\n{runup_label}"
                    runup_label = None
                _queue_extreme(max_label, max_position, max_value, stock_color, above=True)

                # 最低点（区间回撤恰好在此见底时并入同一标签）
                min_label = f"{compare_labels[compare_index]}\n跌幅 {min_value:+.2f}%"
                if drawdown_label is not None and drawdown_position == min_position:
                    min_label += f"\n{drawdown_label}"
                    drawdown_label = None
                _queue_extreme(min_label, min_position, min_value, stock_color, above=False)

                # 区间涨幅/回撤终点不与全局极值重合时，标注在真实发生的点位，
                # 并用虚线画出波段起点 → 终点的跨度
                if runup_label is not None:
                    start_position = _index_position(series.index[swing.runup_start])
                    ax.plot(
                        [start_position, runup_position],
                        [float(series.iloc[swing.runup_start]), float(series.iloc[swing.runup_end])],
                        color=stock_color,
                        linestyle="--",
                        linewidth=1.2,
                        alpha=0.55,
                        zorder=4,
                    )
                    _queue_extreme(
                        f"{compare_labels[compare_index]}\n{runup_label}",
                        runup_position,
                        float(series.iloc[swing.runup_end]),
                        stock_color,
                        above=True,
                    )
                if drawdown_label is not None:
                    start_position = _index_position(series.index[swing.drawdown_start])
                    ax.plot(
                        [start_position, drawdown_position],
                        [float(series.iloc[swing.drawdown_start]), float(series.iloc[swing.drawdown_end])],
                        color=stock_color,
                        linestyle="--",
                        linewidth=1.2,
                        alpha=0.55,
                        zorder=4,
                    )
                    _queue_extreme(
                        f"{compare_labels[compare_index]}\n{drawdown_label}",
                        drawdown_position,
                        float(series.iloc[swing.drawdown_end]),
                        stock_color,
                        above=False,
                    )

            _draw_dodged_text_labels(ax, extreme_entries, min_sep_pts=6.0, fontsize=10.0)

            # 末端标签的 xlim 已在上方设定；极值标注完成后保持右侧留白
            _apply_month_ticks(ax, prices.index)
            ax.tick_params(axis="x", rotation=20, labelbottom=True)
        _apply_detail_legend(
            ax,
            legend_labels,
            text_colors=[MPL_COLORS[i % len(MPL_COLORS)] for i in range(len(legend_labels))],
        )
    if axes:
        axes[0].set_title("对比图", fontsize=24, fontweight=FONT_W_BOLD, color=FG_COLOR, pad=24)
    fig.text(
        0.016,
        0.005,
        "数据来源：东方财富 | SayuStock",
        color=FG_COLOR,
        fontsize=9,
        alpha=0.65,
        fontweight=FONT_W_LIGHT,
    )
    _hide_root_x_tick_labels(fig)
    return _fig_to_image(fig)
