"""日K / 周K / 月K 图。

指标一律走 ``utils/indicators.py``（AI 读的是同一份），不要改用 mplchart 的
MACD/RSI/BBANDS —— 那是西方口径，会和 AI 的读数对不上。
"""

from ..utils import indicators as ind
from .chart_base import (
    SMA,
    BG_COLOR,
    FG_COLOR,
    UP_COLOR,
    AXIS_COLOR,
    DOWN_COLOR,
    GRID_COLOR,
    Pane,
    Chart,
    HLine,
    Figure,
    Volume,
    BarPlot,
    JsonDict,
    LinePlot,
    Indicator,
    Rectangle,
    DrawResult,
    Candlesticks,
    FuncFormatter,
    np,
    pd,
    _as_dict,
    _setup_mpl,
    _dict_value,
    _style_axis,
    _fig_to_image,
    _frame_column,
    _draw_in_thread,
    _numeric_series,
    _datetime_series,
    _apply_month_ticks,
    _axes_top_to_bottom,
    _format_percent_axis,
    _apply_intraday_kline_ticks,
    _format_precise_percent_axis,
)
from .render_data import build_kline_render_data
from ..utils.constant import ErroText

# BOLL 带的填充色（与 mplchart 自带 BBANDS 的观感一致：短期红、中期紫）
BOLL20_COLOR = "#c0392b"
BOLL60_COLOR = "#8e7cc3"


class BOLL(Indicator):
    """通达信/东财口径的布林带（基准价=**收盘价**），渲染成填充带。

    为什么不用 mplchart 自带的 ``BBANDS``：它的基准价是典型价 (H+L+C)/3，
    与 AI 读数和券商软件都对不上（见 utils/indicators.boll 的说明）。但它的
    **渲染**是好的 —— mplchart 的 autoplot 只要看到 ``upperband`` /
    ``lowerband`` 这两个列名就会走 ``_plot_bands``：上下轨点线 + 区域填充。
    所以这里只换算法、不换渲染，把带子原样还回来。

    不返回 ``middleband``：BOLL 中轨按定义就是 MA(N)，而图上已经有 SMA(20) /
    SMA(60) 两条实线（带图例）画的是同一条线，再画一条虚线纯属重叠。
    """

    output_names = ("upperband", "lowerband")

    def __init__(self, period: int = 20, nbdev: float = 2.0):
        self.period = period
        self.nbdev = nbdev
        # get_label 优先取 .label；color_scheme 用它做键来指定填充色
        self.label = f"BOLL({period},{nbdev})"

    # mplchart 的 Indicator.__call__ 是抽象方法且没标返回类型，pyright 只能从空函数体
    # 推断成 None —— 于是任何返回值的实现都算"不兼容重写"，mplchart 自带的每个指标
    # （含 BBANDS）都一样，只是 site-packages 不受检查。这是库的标注缺失，不是本地错误。
    def __call__(self, prices: pd.DataFrame) -> pd.DataFrame:  # pyright: ignore[reportIncompatibleMethodOverride]
        _mid, upper, lower = ind.boll(_frame_column(prices, "close"), self.period, self.nbdev)
        return pd.DataFrame({"upperband": upper, "lowerband": lower})


async def to_single_fig_kline(raw_data: JsonDict, sp: str | None = None) -> DrawResult:
    return await _draw_in_thread(draw_single_kline_chart, raw_data, sp)


def draw_single_kline_chart(raw_data: JsonDict, sp: str | None = None) -> DrawResult:
    _ = sp
    _setup_mpl()
    data = build_kline_render_data(raw_data)
    if isinstance(data, str):
        return data
    kline = data
    chart_df = kline.chart_df.copy().dropna(subset=["date", "open", "high", "low", "close"])
    if chart_df.empty:
        return ErroText["notData"]

    dates = _datetime_series(chart_df["date"])
    valid_mask = dates.notna()
    turnover_source = chart_df["turnover"] if "turnover" in chart_df else pd.Series(0.0, index=chart_df.index)
    prices = pd.DataFrame(
        {
            "open": np.asarray(_numeric_series(chart_df["open"])[valid_mask]),
            "high": np.asarray(_numeric_series(chart_df["high"])[valid_mask]),
            "low": np.asarray(_numeric_series(chart_df["low"])[valid_mask]),
            "close": np.asarray(_numeric_series(chart_df["close"])[valid_mask]),
            "volume": np.asarray(_numeric_series(chart_df["volume"], fill_value=0)[valid_mask]),
            "turnover": np.asarray(_numeric_series(turnover_source, fill_value=0)[valid_mask]),
        },
        index=pd.DatetimeIndex(np.asarray(dates[valid_mask]), name="date"),
    )
    prices = prices.dropna(subset=["open", "high", "low", "close"])
    if prices.empty:
        return ErroText["notData"]
    prices = prices.sort_index()

    high = _frame_column(prices, "high")
    low = _frame_column(prices, "low")
    close = _frame_column(prices, "close")
    volume = _frame_column(prices, "volume")

    # 指标一律走 utils.indicators（与 AI 决策同一份实现、同一套通达信/东财口径）。
    # 不要改用 mplchart.indicators 的 MACD/RSI/BBANDS —— 那是西方口径，
    # MACD 柱少 2 倍、BOLL 基准用典型价，会和 AI 的读数对不上。
    prices["kdj_k"], prices["kdj_d"], prices["kdj_j"] = ind.kdj(high, low, close)
    prices["bbi"] = ind.bbi(close)
    # BOLL 由上面的 BOLL 指标类在绘制时自行计算（要走 mplchart 的填充带渲染）
    prices["cmf20"] = ind.cmf(high, low, close, volume, 20)
    prices["rsi6"] = ind.rsi(close, 6)
    prices["rsi12"] = ind.rsi(close, 12)
    prices["rsi24"] = ind.rsi(close, 24)
    prices["macd_dif"], prices["macd_dea"], prices["macd_bar"] = ind.macd(close)

    raw_info = _as_dict(raw_data["data"])
    raw_title_name = str(_dict_value(raw_info, "name", "")).strip()
    kline_title = f"{raw_title_name} {kline.freq_label}" if raw_title_name else kline.title

    chart = Chart(
        prices,
        title=kline_title,
        figsize=(25.5, 19.5),
        bgcolor=BG_COLOR,
        raw_dates=False,
        color_scheme={
            "colorup": UP_COLOR,
            "colordn": DOWN_COLOR,
            "bgcolor": BG_COLOR,
            "text": FG_COLOR,
            "grid": GRID_COLOR,
            # 指定两条 BOLL 带的填充色（键为 BOLL.label），否则会走颜色循环、
            # 随着主图线条增减而漂移
            "BOLL(20,2.0)": BOLL20_COLOR,
            "BOLL(60,3.0)": BOLL60_COLOR,
        },
    )
    chart.plot(
        Candlesticks(width=0.78, alpha=0.95, colorup=UP_COLOR, colordn=DOWN_COLOR),
        Volume(width=0.76, alpha=0.42, colorup=UP_COLOR, colordn=DOWN_COLOR),
        SMA(60),
        SMA(5),
        SMA(10),
        # MA20（月线）：AI 的多头排列 / close_above_ma20 判断都基于它，图上必须画出来。
        # 它同时就是 BOLL(20,2) 的中轨，所以 BOLL 带不再重复画中轨。
        SMA(20),
        BOLL(20, 2.0),
        BOLL(60, 3.0),
        LinePlot(lambda frame: frame["bbi"], label="BBI", color="#ffd700", width=2.2),
        Pane("below", height_ratio=0.22),
        HLine(0, color=GRID_COLOR, linestyle="--"),
        LinePlot(lambda frame: frame["turnover"], label="换手率", color="#d77cff", width=1.5),
        LinePlot(lambda frame: frame["cmf20"], label="CMF(20)", color="#2ecc71", width=1.5),
        Pane("below", height_ratio=0.15),
        # RSI 取 6/12/24（国内口径，与 AI 读数一致），而非西方默认的 RSI(14)。
        # 80/20 超买超卖线在下面的 axhspan/axhline 里画，这里不要重复加 HLine。
        LinePlot(lambda frame: frame["rsi6"], label="RSI6", color="#4aa3ff", width=1.6),
        LinePlot(lambda frame: frame["rsi12"], label="RSI12", color="#f5b301", width=1.3),
        LinePlot(lambda frame: frame["rsi24"], label="RSI24", color="#ff5da2", width=1.3),
        Pane("below", height_ratio=0.15),
        LinePlot(lambda frame: frame["kdj_k"], label="K", color="#f5b301", width=1.5),
        LinePlot(lambda frame: frame["kdj_d"], label="D", color="#2e9bff", width=1.5),
        LinePlot(lambda frame: frame["kdj_j"], label="J", color="#ff5da2", width=1.4),
        Pane("below", height_ratio=0.18),
        # MACD 柱按正负分红绿两条画 —— BarPlot 只接受单色，靠 where() 掩掉另一半
        BarPlot(
            lambda frame: frame["macd_bar"].where(frame["macd_bar"] >= 0),
            color=UP_COLOR,
            alpha=0.75,
            width=0.76,
            label="MACD柱",
        ),
        BarPlot(
            lambda frame: frame["macd_bar"].where(frame["macd_bar"] < 0),
            color=DOWN_COLOR,
            alpha=0.75,
            width=0.76,
            # 红绿柱同属一条 MACD 柱，图例只留正柱那条；下划线前缀让 matplotlib
            # 跳过它，否则 mplchart 会拿 lambda 的 repr 当图例名
            label="_nolegend_",
        ),
        LinePlot(lambda frame: frame["macd_dif"], label="DIF", color="#f1c40f", width=1.6),
        LinePlot(lambda frame: frame["macd_dea"], label="DEA", color="#4aa3ff", width=1.6),
    )
    chart.add_legends()

    fig: Figure = chart.figure
    fig.set_facecolor(BG_COLOR)
    axes = _axes_top_to_bottom(fig)
    for index, ax in enumerate(axes):
        _style_axis(ax)
        # 副图之间的分隔线用金色实线加粗，提升各 pane 的区分度
        if index > 0:
            ax.spines["top"].set_color("#ffd700")
            ax.spines["top"].set_linewidth(1.6)
        ax.tick_params(axis="x", rotation=16)
        ax.yaxis.label.set_color(AXIS_COLOR)
        if index < len(axes) - 1:
            ax.tick_params(labelbottom=False)
        else:
            if "min" in kline.freq_label or "H" in kline.freq_label:
                _apply_intraday_kline_ticks(ax, prices.index)
            else:
                _apply_month_ticks(ax, prices.index)
            ax.tick_params(labelbottom=True)
        if index == 1:
            ax.set_ylabel("换手率", color="#d77cff")
            ax.yaxis.set_major_formatter(FuncFormatter(_format_precise_percent_axis))
            turnover_values = _frame_column(prices, "turnover").dropna()
            turnover_max = float(turnover_values.max()) if not turnover_values.empty else 0.0
            turnover_limit = max(turnover_max * 1.35, 0.01)
            ax.set_ylim(-turnover_limit, turnover_limit)

            turnover_line = next((line for line in ax.lines if line.get_label() == "换手率"), None)
            if turnover_line is not None:
                turnover_x = np.asarray(turnover_line.get_xdata(), dtype=float)
                turnover_y = np.asarray(turnover_line.get_ydata(), dtype=float)
                finite_turnover = np.isfinite(turnover_y)
                if bool(finite_turnover.any()):
                    turnover_peak_index = int(np.nanargmax(np.where(finite_turnover, turnover_y, np.nan)))
                    turnover_peak_x = float(turnover_x[turnover_peak_index])
                    turnover_peak_y = float(turnover_y[turnover_peak_index])
                    ax.scatter(
                        [turnover_peak_x],
                        [turnover_peak_y],
                        color="#d77cff",
                        edgecolor=BG_COLOR,
                        s=46,
                        zorder=5,
                    )
                    ax.annotate(
                        f"换手率 {turnover_peak_y:.2f}%",
                        xy=(turnover_peak_x, turnover_peak_y),
                        xytext=(8, 10),
                        textcoords="offset points",
                        color="#d77cff",
                        fontsize=11,
                        fontweight="bold",
                        bbox={"facecolor": BG_COLOR, "edgecolor": "#d77cff", "alpha": 0.72, "pad": 3},
                    )

            cmf_line = next((line for line in ax.lines if line.get_label() == "CMF(20)"), None)
            if cmf_line is not None:
                cmf_x = np.asarray(cmf_line.get_xdata(), dtype=float)
                cmf_y = np.asarray(cmf_line.get_ydata(), dtype=float)
                cmf_color = cmf_line.get_color()
                cmf_width = cmf_line.get_linewidth()
                cmf_alpha = cmf_line.get_alpha()
                cmf_line.remove()
                cmf_ax = ax.twinx()
                cmf_ax.set_label("twinx")
                cmf_ax.set_facecolor("none")
                cmf_ax.plot(cmf_x, cmf_y, label="CMF(20)", color=cmf_color, linewidth=cmf_width, alpha=cmf_alpha)
                cmf_ax.axhline(0, color=GRID_COLOR, linestyle="--", alpha=0.55, linewidth=0.9)
                cmf_ax.set_ylabel("CMF(20)", color="#2ecc71")
                cmf_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos=None: f"{value * 100:.2f}%"))
                cmf_ax.tick_params(axis="y", colors=AXIS_COLOR, labelsize=12)
                cmf_ax.spines["right"].set_color(AXIS_COLOR)
                cmf_ax.spines["right"].set_linewidth(1.1)
                cmf_ax.grid(False)
                with np.errstate(invalid="ignore"):
                    finite_cmf = cmf_y[np.isfinite(cmf_y)]
                cmf_abs_max = float(np.nanmax(np.abs(finite_cmf))) if len(finite_cmf) > 0 else 0.2
                cmf_limit = max(cmf_abs_max * 1.35, 0.2)
                cmf_ax.set_ylim(-cmf_limit, cmf_limit)
                if len(finite_cmf) > 0:
                    cmf_peak_index = int(np.nanargmax(np.where(np.isfinite(cmf_y), cmf_y, np.nan)))
                    cmf_peak_x = float(cmf_x[cmf_peak_index])
                    cmf_peak_y = float(cmf_y[cmf_peak_index])
                    cmf_ax.scatter(
                        [cmf_peak_x],
                        [cmf_peak_y],
                        color="#2ecc71",
                        edgecolor=BG_COLOR,
                        s=46,
                        zorder=5,
                    )
                    cmf_ax.annotate(
                        f"CMF {cmf_peak_y * 100:.2f}%",
                        xy=(cmf_peak_x, cmf_peak_y),
                        xytext=(8, -18),
                        textcoords="offset points",
                        color="#2ecc71",
                        fontsize=11,
                        fontweight="bold",
                        bbox={"facecolor": BG_COLOR, "edgecolor": "#2ecc71", "alpha": 0.72, "pad": 3},
                    )
        elif index == 2:
            ax.set_ylabel("RSI")
            ax.set_ylim(0, 100)
            ax.yaxis.set_major_formatter(FuncFormatter(_format_percent_axis))
            ax.axhspan(80, 100, facecolor=UP_COLOR, alpha=0.12, zorder=0.1)
            ax.axhspan(0, 20, facecolor=DOWN_COLOR, alpha=0.12, zorder=0.1)
            ax.axhline(80, color=UP_COLOR, linestyle="--", alpha=0.75, linewidth=1.0)
            ax.axhline(20, color=DOWN_COLOR, linestyle="--", alpha=0.75, linewidth=1.0)
            ax.text(
                0.5,
                0.84,
                "超买区 >80：提防追高/逢高减仓",
                transform=ax.transAxes,
                color=UP_COLOR,
                fontsize=12,
                fontweight="bold",
                ha="center",
                va="center",
                bbox={"facecolor": BG_COLOR, "edgecolor": UP_COLOR, "alpha": 0.70, "pad": 3},
            )
            ax.text(
                0.5,
                0.16,
                "超卖区 <20：避免恐慌割肉/关注反弹",
                transform=ax.transAxes,
                color=DOWN_COLOR,
                fontsize=12,
                fontweight="bold",
                ha="center",
                va="center",
                bbox={"facecolor": BG_COLOR, "edgecolor": DOWN_COLOR, "alpha": 0.70, "pad": 3},
            )
        elif index == 3:
            ax.set_ylabel("KDJ")
            kdj_all = np.concatenate([np.asarray(prices[col], dtype=float) for col in ("kdj_k", "kdj_d", "kdj_j")])
            finite_kdj = kdj_all[np.isfinite(kdj_all)]
            if finite_kdj.size:
                kdj_low = min(0.0, float(np.min(finite_kdj)))
                kdj_high = max(100.0, float(np.max(finite_kdj)))
            else:
                kdj_low, kdj_high = 0.0, 100.0
            kdj_pad = max((kdj_high - kdj_low) * 0.08, 4.0)
            ax.set_ylim(kdj_low - kdj_pad, kdj_high + kdj_pad)
            ax.axhspan(80, 100, facecolor=UP_COLOR, alpha=0.10, zorder=0.1)
            ax.axhspan(0, 20, facecolor=DOWN_COLOR, alpha=0.10, zorder=0.1)
            ax.axhline(80, color=UP_COLOR, linestyle="--", alpha=0.65, linewidth=1.0)
            ax.axhline(50, color=GRID_COLOR, linestyle="--", alpha=0.5, linewidth=0.9)
            ax.axhline(20, color=DOWN_COLOR, linestyle="--", alpha=0.65, linewidth=1.0)

            # KDJ 金叉/死叉区域：按 K/D 相对位置把整个副图切成红绿矩形块
            mapper = chart.mapper
            if mapper is not None:
                xv, k_yv, d_yv = mapper.series_xy(prices["kdj_k"], prices["kdj_d"])
                y_bottom, y_top = ax.get_ylim()
                with np.errstate(invalid="ignore"):
                    k_above_d: list[bool] = list(np.asarray(k_yv >= d_yv, dtype=bool))
                    d_above_k: list[bool] = list(np.asarray(k_yv < d_yv, dtype=bool))
                    ax.fill_between(
                        xv,
                        y_bottom,
                        y_top,
                        where=k_above_d,
                        facecolor=UP_COLOR,
                        alpha=0.18,
                        interpolate=True,
                        zorder=0.05,
                    )
                    ax.fill_between(
                        xv,
                        y_bottom,
                        y_top,
                        where=d_above_k,
                        facecolor=DOWN_COLOR,
                        alpha=0.18,
                        interpolate=True,
                        zorder=0.05,
                    )
        elif index == len(axes) - 1:
            ax.set_ylabel("MACD")
        if index == len(axes) - 1:
            for patch in ax.patches:
                if not isinstance(patch, Rectangle):
                    continue
                height = patch.get_height()
                patch.set_facecolor(UP_COLOR if height >= 0 else DOWN_COLOR)
                patch.set_alpha(0.72)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(BG_COLOR)
            legend.get_frame().set_edgecolor(GRID_COLOR)
            for text in legend.get_texts():
                text.set_color(FG_COLOR)

    if axes:
        axes[0].set_title(kline_title, color=FG_COLOR, fontsize=24, fontweight="bold", pad=24)
    fig.text(0.016, 0.005, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    fig.subplots_adjust(left=0.045, right=0.988, top=0.885, bottom=0.10, hspace=0.055)
    return _fig_to_image(fig)
