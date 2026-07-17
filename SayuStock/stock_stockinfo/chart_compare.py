"""个股对比图（多标的按首日收盘归一化的涨跌幅）。"""

from ..utils import indicators as ind
from .chart_base import (
    BG_COLOR,
    FG_COLOR,
    UP_COLOR,
    AXIS_COLOR,
    DOWN_COLOR,
    GRID_COLOR,
    MPL_COLORS,
    Chart,
    HLine,
    Price,
    Figure,
    HPacker,
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
    _numeric_series,
    _datetime_series,
    _apply_month_ticks,
    _axes_top_to_bottom,
    _format_percent_axis,
)
from .render_data import build_compare_render_data
from ..utils.constant import ErroText


async def to_compare_fig(raw_datas: list[JsonDict]) -> DrawResult:
    return await _draw_in_thread(draw_compare_chart, raw_datas)


def draw_compare_chart(raw_datas: list[JsonDict]) -> DrawResult:
    _setup_mpl()
    data = build_compare_render_data(raw_datas)
    if isinstance(data, str):
        return data
    compare = data

    price_frames: list[pd.DataFrame] = []
    compare_columns: list[str] = []
    compare_labels: list[str] = []
    for index, item in enumerate(compare.items):
        column_name = f"compare_{index}"
        compare_columns.append(column_name)
        compare_labels.append(item.name)
        dates = _datetime_series(item.df["日期"])
        values = _numeric_series(item.df["归一化"]) * 100
        valid_mask = dates.notna() & values.notna()
        price_frames.append(
            pd.DataFrame(
                {column_name: np.asarray(values[valid_mask])},
                index=pd.DatetimeIndex(np.asarray(dates[valid_mask]), name="date"),
            )
        )
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
            label_offsets: dict[int, int] = {}
            for compare_index, column_name in enumerate(compare_columns):
                series = _frame_column(prices, column_name).dropna()
                if series.empty:
                    continue
                last_timestamp = series.index[-1]
                last_positions = np.flatnonzero(prices.index == last_timestamp)
                last_position = int(last_positions[-1]) if len(last_positions) > 0 else len(prices) - 1
                last_value = float(series.iloc[-1])
                stock_color = MPL_COLORS[compare_index % len(MPL_COLORS)]
                bucket = int(round(last_value * 2))
                offset_count = label_offsets.get(bucket, 0)
                label_offsets[bucket] = offset_count + 1
                y_offset = (offset_count - 1) * 13 if offset_count > 0 else 0
                ax.scatter(
                    [last_position],
                    [last_value],
                    color=stock_color,
                    edgecolor=BG_COLOR,
                    s=34,
                    zorder=5,
                )
                name_area = TextArea(
                    compare_labels[compare_index],
                    textprops={"color": stock_color, "fontsize": 11, "fontweight": "bold"},
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
                    bboxprops={"facecolor": BG_COLOR, "edgecolor": stock_color, "alpha": 0.70},
                    arrowprops={"arrowstyle": "-", "color": stock_color, "alpha": 0.75, "linewidth": 0.8},
                    zorder=6,
                )
                ax.add_artist(label_artist)

            # 标注每条对比序列的最高点、最低点，并显示区间最大涨幅/回撤。
            # 区间涨幅/回撤的终点是波段自己的峰/谷，不一定是全局极值点，
            # 点位一律取 swing_points 的结果；与全局极值重合时合并成一个标签。
            def _index_position(timestamp: object) -> int:
                positions = np.flatnonzero(prices.index == timestamp)
                return int(positions[-1]) if len(positions) > 0 else 0

            def _annotate_extreme(
                text: str,
                position: int,
                value: float,
                color: str,
                above: bool,
            ) -> None:
                ax.scatter(
                    [position],
                    [value],
                    color=color,
                    edgecolor=BG_COLOR,
                    s=42,
                    zorder=5,
                )
                # 极值点 tag 向左侧偏移，避免与右侧的“最后一点”标签重叠
                ax.annotate(
                    text,
                    xy=(position, value),
                    xytext=(-14, 14 if above else -14),
                    textcoords="offset points",
                    color=color,
                    fontsize=10,
                    fontweight="bold",
                    ha="center",
                    va="bottom" if above else "top",
                    bbox={"facecolor": BG_COLOR, "edgecolor": color, "alpha": 0.72, "pad": 2.5},
                    arrowprops={"arrowstyle": "-", "color": color, "alpha": 0.75, "linewidth": 0.8},
                    zorder=6,
                )

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
                _annotate_extreme(max_label, max_position, max_value, stock_color, above=True)

                # 最低点（区间回撤恰好在此见底时并入同一标签）
                min_label = f"{compare_labels[compare_index]}\n跌幅 {min_value:+.2f}%"
                if drawdown_label is not None and drawdown_position == min_position:
                    min_label += f"\n{drawdown_label}"
                    drawdown_label = None
                _annotate_extreme(min_label, min_position, min_value, stock_color, above=False)

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
                    _annotate_extreme(
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
                    _annotate_extreme(
                        f"{compare_labels[compare_index]}\n{drawdown_label}",
                        drawdown_position,
                        float(series.iloc[swing.drawdown_end]),
                        stock_color,
                        above=False,
                    )

            ax.set_xlim(-1, x_right + 7)
            _apply_month_ticks(ax, prices.index)
            ax.tick_params(axis="x", rotation=20, labelbottom=True)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(BG_COLOR)
            legend.get_frame().set_edgecolor(AXIS_COLOR)
            for text, label in zip(legend.get_texts(), compare_labels, strict=False):
                text.set_text(label)
                text.set_color(FG_COLOR)
    if axes:
        axes[0].set_title("对比图", fontsize=24, fontweight="bold", color=FG_COLOR, pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.965, top=0.875, bottom=0.10)
    return _fig_to_image(fig)
