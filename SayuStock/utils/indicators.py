"""技术指标计算 —— 全插件唯一真相源（纯 pandas/numpy）。

分两层，**数学只写一次**：

- **series 层**（``ma`` / ``macd`` / ``kdj`` …）：series 进 series 出，图表直接拿去画；
- **标量层**（``calc_*`` / ``compute_indicators``）：取 ``.iloc[-1]`` 收敛成标量 + 叉信号，
  喂给 LLM 和文字输出（``utils/render_text.py``）。标量层只做取值和 None 契约，不写数学。

``stock_papertrade/indicators.py`` 现在只是本模块的兼容 re-export。

## 为什么有这个模块

在此之前同一个指标在仓库里存在多份实现：

- KDJ / BBI：``stock_papertrade/indicators.py``（AI 决策用）和
  ``stock_stockinfo/render_mpl.py``（画图用）各写了一份逐字复制的代码；
- MACD / RSI / BOLL：AI 用手写实现，图表却直接用 ``mplchart.indicators``。
  两者**口径并不一致**（见下方「口径」一节），导致 AI 说"MACD 柱转红"时，
  用户在图上看到的柱子高度只有 AI 读数的一半。

本模块把数学收敛到一处：**series 进、series 出**。
上层各取所需：

- 图表（``render_mpl``）直接画返回的 Series；
- AI（``stock_papertrade/indicators.py``）取 ``.iloc[-1]`` 变成标量 + 叉信号。

新增指标一律加在这里，不要再在上层内联计算。

## 口径

统一采用**通达信 / 东方财富**口径（用户券商软件看到的那条线），而非
mplchart 默认的西方口径：

- ``macd``：BAR = (DIF - DEA) × **2**；mplchart 是 ``hist = DIF - DEA``（少了 2 倍）。
- ``boll``：基准价为**收盘价**；mplchart 用典型价 ``(H+L+C)/3``。
- ``rsi``：Wilder 平滑（``adjust=False``）；mplchart 用 ``adjust=True``
  （长序列会收敛到同一值，短序列有偏差）。
- ``kdj``：RSV 递归平滑，K/D 初值取中性 50。
"""

from typing import NamedTuple

import numpy as np
import pandas as pd

__all__ = [
    # series 层
    "atr_pct",
    "bbi",
    "bias",
    "boll",
    "cci",
    "cmf",
    "cross_signals",
    "ema",
    "kdj",
    "ma",
    "macd",
    "normalize_pct",
    "rsi",
    "support_resistance",
    "swing_points",
    "swing_stats",
    "SwingPoints",
    "true_range",
    "volume_ratio",
    # 标量层
    "calc_atr_pct",
    "calc_bbi",
    "calc_bias",
    "calc_boll",
    "calc_cci",
    "calc_cmf",
    "calc_kdj",
    "calc_ma",
    "calc_macd",
    "calc_rsi",
    "calc_support_resistance",
    "calc_volume_ratio",
    "compute_indicators",
]


# ============================================================
# 均线
# ============================================================
def ma(close: pd.Series, period: int) -> pd.Series:
    """简单移动平均 MA(N)。"""
    return close.rolling(window=period).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    """指数移动平均 EMA(N)（递归口径，adjust=False）。"""
    return close.ewm(span=span, adjust=False).mean()


# ============================================================
# MACD (12, 26, 9)
# ============================================================
def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD，返回 (DIF, DEA, BAR)。

    BAR = (DIF - DEA) × 2 —— 通达信/东财口径。西方口径（含 mplchart）不乘 2，
    柱子只有这里的一半高，务必不要混用。
    """
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    bar = (dif - dea) * 2.0
    return dif, dea, bar


# ============================================================
# RSI（Wilder 平滑）
# ============================================================
def rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI(N)，Wilder 平滑（EMA alpha=1/N, adjust=False）。

    全跌段 avg_gain=0 → RSI=0；全涨段 avg_loss=0 → RSI=100（此处按 100 处理，
    避免 0/0 产生 NaN 断点）。
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0（含 avg_gain 也为 0 的横盘）时 rs 为 inf/NaN，统一记 100
    return out.where(avg_loss != 0, 100.0)


# ============================================================
# KDJ（9, 3, 3，通达信/东财口径）
# ============================================================
def kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ 随机指标，返回 (K, D, J)。

    RSV = (C - LLV_n) / (HHV_n - LLV_n) × 100，
    K = (RSV + 2K') / 3，D = (K + 2D') / 3（K/D 初值取中性 50），J = 3K - 2D。
    K/D 常态 0~100（>80 超买 <20 超卖），J 可越界。
    """
    low_min = low.rolling(window=n, min_periods=1).min()
    high_max = high.rolling(window=n, min_periods=1).max()
    span = (high_max - low_min).to_numpy(dtype=float)
    close_arr = close.to_numpy(dtype=float)
    low_arr = low_min.to_numpy(dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        rsv = np.where(span > 0, (close_arr - low_arr) / span * 100.0, np.nan)

    k_vals = np.empty(len(rsv), dtype=float)
    d_vals = np.empty(len(rsv), dtype=float)
    k_prev = d_prev = 50.0
    for i, raw_rsv in enumerate(rsv):
        # 区间无波动（涨跌停/停牌）时 RSV 记为中性 50，避免出现断点
        cur_rsv = 50.0 if not np.isfinite(raw_rsv) else float(raw_rsv)
        k_prev = (cur_rsv + (m1 - 1) * k_prev) / m1
        d_prev = (k_prev + (m2 - 1) * d_prev) / m2
        k_vals[i] = k_prev
        d_vals[i] = d_prev

    k = pd.Series(k_vals, index=close.index)
    d = pd.Series(d_vals, index=close.index)
    j = 3.0 * k - 2.0 * d
    return k, d, j


# ============================================================
# BBI 多空指数
# ============================================================
def bbi(close: pd.Series) -> pd.Series:
    """BBI = (MA3 + MA6 + MA12 + MA24) / 4。close > BBI 多头占优。"""
    return (ma(close, 3) + ma(close, 6) + ma(close, 12) + ma(close, 24)) / 4.0


# ============================================================
# 布林带 BOLL
# ============================================================
def boll(
    close: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带，返回 (中轨, 上轨, 下轨)。

    基准价为**收盘价**（通达信/东财口径）。mplchart 用典型价 (H+L+C)/3，
    画出来与券商软件的 BOLL 不是同一条线。
    """
    mid = close.rolling(window=period).mean()
    std = close.rolling(window=period).std(ddof=0)
    return mid, mid + std_mult * std, mid - std_mult * std


# ============================================================
# CCI 顺势指标
# ============================================================
def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """CCI(N) = (TP - MA(TP,N)) / (0.015 × 平均绝对离差)。

    TP = (H + L + C) / 3。常态 -100~+100，>+100 超买 <-100 超卖。
    平均绝对离差为 0（完全无波动）时记 0，避免除零。
    """
    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(window=period).mean()
    mean_dev = tp.rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    out = (tp - sma_tp) / (0.015 * mean_dev)
    return out.where(mean_dev != 0, 0.0)


# ============================================================
# CMF 蔡金资金流
# ============================================================
def cmf(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 20,
) -> pd.Series:
    """CMF(N) = Σ(资金流量) / Σ(成交量)。

    资金流量 = ((C-L) - (H-C)) / (H-L) × V。H==L（一字板/停牌）时该根没有方向，
    ``fillna(0)`` 让它对分子不贡献，但成交量仍计入分母 —— 与「跳过该根」等价。
    """
    rng = (high - low).replace(0, np.nan)
    mfv = (((close - low) - (high - close)) / rng * volume).fillna(0.0)
    vol_sum = volume.rolling(window=period).sum()
    out = mfv.rolling(window=period).sum() / vol_sum
    return out.where(vol_sum != 0, 0.0)


# ============================================================
# 量比 / 乖离率 / ATR / 支撑压力
# ============================================================
def volume_ratio(volume: pd.Series, period: int = 5) -> pd.Series:
    """量比 = 当日量 / 前 N 日均量（不含当日）。"""
    avg = volume.shift(1).rolling(window=period).mean()
    return (volume / avg).where(avg != 0)


def bias(close: pd.Series, period: int = 20) -> pd.Series:
    """乖离率 BIAS = (C - MA_N) / MA_N。"""
    m = ma(close, period)
    return ((close - m) / m).where(m != 0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """真实波幅 TR = max(H-L, |H-C'|, |L-C'|)。"""
    prev_close = close.shift(1)
    parts: list[pd.Series] = [
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ]
    return pd.concat(parts, axis=1).max(axis=1)


def atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR% = MA(TR, N) / C —— 用收盘价归一后的波动率，便于跨股比较。"""
    atr = true_range(high, low, close).rolling(window=period).mean()
    return (atr / close).where(close != 0)


def support_resistance(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series]:
    """近 N 日支撑（最低价）/ 压力（最高价），返回 (支撑, 压力)。"""
    return low.rolling(window=period).min(), high.rolling(window=period).max()


# ============================================================
# 叉信号
# ============================================================
def cross_signals(fast: pd.Series, slow: pd.Series, days: int = 3) -> tuple[bool, bool]:
    """近 N 根内 fast 是否上穿/下穿 slow，返回 (金叉, 死叉)。

    金叉：前一根 fast <= slow 且当根 fast > slow；死叉反之。
    """
    golden = death = False
    for i in range(-days, 0):
        try:
            f_prev = float(fast.iloc[i - 1])
            f_curr = float(fast.iloc[i])
            s_prev = float(slow.iloc[i - 1])
            s_curr = float(slow.iloc[i])
        except (IndexError, ValueError):
            continue
        if not (np.isfinite(f_prev) and np.isfinite(f_curr) and np.isfinite(s_prev) and np.isfinite(s_curr)):
            continue
        if f_prev <= s_prev and f_curr > s_curr:
            golden = True
        if f_prev >= s_prev and f_curr < s_curr:
            death = True
    return golden, death


# ============================================================
# 归一化 / 区间涨跌幅
# ============================================================
def normalize_pct(close: pd.Series) -> pd.Series:
    """归一化到首日收盘的累计涨跌幅（小数，非百分数）：C / C[0] - 1。

    多标的对比时把不同价位的股票拉到同一起点。基准价为 0 或缺失时返回全 NaN。
    """
    if close.empty:
        return close.astype(float)
    base = float(close.iloc[0])
    if not np.isfinite(base) or base == 0:
        return pd.Series(np.nan, index=close.index, dtype=float)
    return close / base - 1.0


class SwingPoints(NamedTuple):
    """区间最大涨幅/回撤及其起止位置（``pct_series`` 内的整数位置，iloc 语义）。

    无有效波段（数据不足/非法值/单边无反弹）时对应幅度为 0，位置为 -1。
    """

    max_runup: float
    runup_start: int
    runup_end: int
    max_drawdown: float
    drawdown_start: int
    drawdown_end: int


def swing_points(pct_series: pd.Series) -> SwingPoints:
    """由归一化涨跌幅序列（单位 %）算区间最大涨幅/最大回撤及其发生点位。

    归一化序列是相对首日的累计涨跌幅，两点相减只是**百分点之差**，分母始终
    是首日价格而非峰值 —— 那样算出的回撤会超过 100%。这里先还原成相对价格
    ``level = 1 + pct/100``，再以历史极值为分母：

    - 最大回撤 = min(level / 历史最高 - 1)，每点对比它**之前**的最高峰；
    - 最大涨幅 = max(level / 历史最低 - 1)，每点对比它**之前**的最低谷。

    用累计极值天然满足「先见峰后见谷」「先见谷后见峰」的顺序约束。

    注意：涨幅/回撤的终点是波段自己的峰/谷，**不一定**是全序列的全局最高/
    最低点 —— 标注时必须用这里返回的位置，挂到全局极值点上就是错的。
    """
    level = 1.0 + pct_series.to_numpy(dtype=float) / 100.0
    if len(level) < 2 or not np.all(np.isfinite(level)) or np.any(level <= 0):
        return SwingPoints(0.0, -1, -1, 0.0, -1, -1)
    running_max = np.maximum.accumulate(level)
    running_min = np.minimum.accumulate(level)
    runup = level / running_min - 1.0
    drawdown = level / running_max - 1.0

    runup_end = int(np.argmax(runup))
    max_runup = float(runup[runup_end]) * 100.0
    if max_runup > 0:
        runup_start = int(np.argmin(level[: runup_end + 1]))
    else:
        runup_start = runup_end = -1

    drawdown_end = int(np.argmin(drawdown))
    max_drawdown = float(drawdown[drawdown_end]) * 100.0
    if max_drawdown < 0:
        drawdown_start = int(np.argmax(level[: drawdown_end + 1]))
    else:
        drawdown_start = drawdown_end = -1

    return SwingPoints(max_runup, runup_start, runup_end, max_drawdown, drawdown_start, drawdown_end)


def swing_stats(pct_series: pd.Series) -> tuple[float, float]:
    """``swing_points`` 的标量视图：返回 (最大涨幅 >= 0, 最大回撤 <= 0)。"""
    points = swing_points(pct_series)
    return points.max_runup, points.max_drawdown


# ============================================================
# 标量层 —— 取 series 末值给 LLM / 文字输出
#
# pandas-stubs 把 ``DataFrame.__getitem__`` / ``Series.iloc`` 等的返回标成 Any，
# 强行注解 float 不顶用。用 isinstance 收敛联合类型（见 ``_to_float``），
# 既不用 cast() 也不用 # type: ignore。
# ============================================================
def _col_float(df: pd.DataFrame, name: str) -> pd.Series:
    s = df[name]
    assert isinstance(s, pd.Series)
    return s.astype(float)


def _to_float(v: object) -> float:
    """pandas / numpy / Python 标量 → float；非数值抛 ValueError。"""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    raise ValueError(f"Cannot convert {type(v).__name__} to float: {v!r}")


def _last(s: pd.Series) -> float | None:
    """取 series 末值收敛成标量；空序列 / NaN / inf 一律记 None（"无此指标"）。

    series 层用 NaN 表示"数据不足"（rolling 窗口没填满），标量层的契约是 None。
    """
    if len(s) == 0:
        return None
    try:
        v = _to_float(s.iloc[-1])
    except ValueError:
        return None
    return v if np.isfinite(v) else None


def calc_ma(close: pd.Series, period: int) -> float | None:
    if len(close) < period:
        return None
    return _last(ma(close, period))


def calc_macd(close: pd.Series) -> tuple[float | None, float | None, float | None, bool, bool]:
    """返回 (DIF, DEA, BAR, 近3日金叉, 近3日死叉)。"""
    if len(close) < 35:
        return None, None, None, False, False
    dif, dea, bar = macd(close)
    golden, death = cross_signals(dif, dea, days=3)
    return _last(dif), _last(dea), _last(bar), golden, death


def calc_rsi(close: pd.Series, period: int) -> float | None:
    if len(close) < period + 1:
        return None
    return _last(rsi(close, period))


def calc_cmf(df: pd.DataFrame, period: int = 20) -> float | None:
    if len(df) < period:
        return None
    return _last(
        cmf(
            _col_float(df, "high"),
            _col_float(df, "low"),
            _col_float(df, "close"),
            _col_float(df, "volume"),
            period,
        )
    )


def calc_volume_ratio(df: pd.DataFrame) -> float | None:
    if len(df) < 6:
        return None
    return _last(volume_ratio(_col_float(df, "volume"), 5))


def calc_bias(close: pd.Series, period: int = 20) -> float | None:
    if len(close) < period:
        return None
    return _last(bias(close, period))


def calc_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period + 1:
        return None
    return _last(
        atr_pct(
            _col_float(df, "high"),
            _col_float(df, "low"),
            _col_float(df, "close"),
            period,
        )
    )


def calc_support_resistance(df: pd.DataFrame, period: int = 20) -> tuple[float | None, float | None]:
    if len(df) < period:
        return None, None
    support, resistance = support_resistance(
        _col_float(df, "high"),
        _col_float(df, "low"),
        period,
    )
    return _last(support), _last(resistance)


def calc_boll(
    close: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """返回 (中轨, 上轨, 下轨, 带宽, %B)。

    带宽 = (上轨-下轨)/中轨，衡量敞口；%B = (C-下轨)/(上轨-下轨)，价格在带内位置。
    """
    if len(close) < period:
        return None, None, None, None, None
    mid_s, upper_s, lower_s = boll(close, period, std_mult)
    mid = _last(mid_s)
    upper = _last(upper_s)
    lower = _last(lower_s)
    if mid is None or upper is None or lower is None:
        return None, None, None, None, None
    bandwidth = None if mid == 0 else (upper - lower) / mid
    last_close = _to_float(close.iloc[-1])
    percent_b = None if (upper - lower) == 0 else (last_close - lower) / (upper - lower)
    return mid, upper, lower, bandwidth, percent_b


def calc_cci(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period:
        return None
    return _last(
        cci(
            _col_float(df, "high"),
            _col_float(df, "low"),
            _col_float(df, "close"),
            period,
        )
    )


def calc_bbi(close: pd.Series) -> float | None:
    if len(close) < 24:
        return None
    return _last(bbi(close))


def calc_kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> tuple[float | None, float | None, float | None, bool, bool]:
    """返回 (K, D, J, 近3日金叉, 近3日死叉)。金叉 = K 上穿 D。"""
    if len(df) < n:
        return None, None, None, False, False
    k, d, j = kdj(
        _col_float(df, "high"),
        _col_float(df, "low"),
        _col_float(df, "close"),
        n,
        m1,
        m2,
    )
    golden, death = cross_signals(k, d, days=3)
    return _last(k), _last(d), _last(j), golden, death


def compute_indicators(df: pd.DataFrame) -> dict[str, float | bool | None]:
    """从 K 线 DataFrame（英文列名，见 utils/kline.klines_to_df）算全部指标。

    缺失指标以 None 表示。这是 LLM 和文字输出读指标的统一入口 ——
    图表画的是同一批数（都走上面的 series 层），两边不会漂。
    """
    if df.empty:
        return _empty_indicators()

    df = df.copy()
    df["close"] = df["close"].astype(float)
    close = df["close"]
    assert isinstance(close, pd.Series)

    ma5 = calc_ma(close, 5)
    ma10 = calc_ma(close, 10)
    ma20 = calc_ma(close, 20)
    ma60 = calc_ma(close, 60)

    dif, dea, bar, gold, death = calc_macd(close)
    rsi6 = calc_rsi(close, 6)
    rsi12 = calc_rsi(close, 12)
    rsi24 = calc_rsi(close, 24)
    cmf20 = calc_cmf(df, 20)
    vol_ratio = calc_volume_ratio(df)
    bias20 = calc_bias(close, 20)
    atr = calc_atr_pct(df, 14)
    support, resistance = calc_support_resistance(df, 20)

    boll20_mid, boll20_upper, boll20_lower, boll20_bw, boll20_pct = calc_boll(close, 20, 2.0)
    boll60_mid, boll60_upper, boll60_lower, boll60_bw, boll60_pct = calc_boll(close, 60, 3.0)
    boll_opening_ratio: float | None = None
    if boll20_bw is not None and boll60_bw is not None and boll60_bw != 0:
        boll_opening_ratio = boll20_bw / boll60_bw

    cci14 = calc_cci(df, 14)
    bbi_v = calc_bbi(close)
    kdj_k, kdj_d, kdj_j, kdj_gold, kdj_death = calc_kdj(df)

    turnover: float | None = None
    if "turnover_rate" in df.columns:
        tr_col = df["turnover_rate"]
        assert isinstance(tr_col, pd.Series)
        turnover = _to_float(tr_col.iloc[-1])
    last_close: float = _to_float(close.iloc[-1])

    return {
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "macd_dif": dif,
        "macd_dea": dea,
        "macd_bar": bar,
        "macd_golden_cross_in_3d": gold,
        "macd_death_cross_in_3d": death,
        "rsi6": rsi6,
        "rsi12": rsi12,
        "rsi24": rsi24,
        "cmf20": cmf20,
        "volume_ratio": vol_ratio,
        "bias": bias20,
        "atr_pct": atr,
        "support": support,
        "resistance": resistance,
        "turnover_pct": turnover,
        "last_close": last_close,
        "boll20_mid": boll20_mid,
        "boll20_upper": boll20_upper,
        "boll20_lower": boll20_lower,
        "boll20_bandwidth": boll20_bw,
        "boll20_pct_b": boll20_pct,
        "boll60_mid": boll60_mid,
        "boll60_upper": boll60_upper,
        "boll60_lower": boll60_lower,
        "boll60_bandwidth": boll60_bw,
        "boll60_pct_b": boll60_pct,
        "boll_opening_ratio_short_vs_mid": boll_opening_ratio,
        "cci14": cci14,
        "bbi": bbi_v,
        "kdj_k": kdj_k,
        "kdj_d": kdj_d,
        "kdj_j": kdj_j,
        "kdj_golden_cross_in_3d": kdj_gold,
        "kdj_death_cross_in_3d": kdj_death,
        "kdj_overbought": (kdj_j is not None and kdj_j > 100) or (kdj_k is not None and kdj_k > 80),
        "kdj_oversold": (kdj_j is not None and kdj_j < 0) or (kdj_k is not None and kdj_k < 20),
        # 形态特征
        "ma_bull_alignment": (ma5 is not None and ma10 is not None and ma20 is not None and ma5 > ma10 > ma20),
        "ma_bear_alignment": (ma5 is not None and ma10 is not None and ma20 is not None and ma5 < ma10 < ma20),
        "close_above_ma20": (ma20 is not None and last_close > ma20),
        "close_below_ma20": (ma20 is not None and last_close < ma20),
        "boll20_breakout_up": (boll20_upper is not None and last_close > boll20_upper),
        "boll20_breakout_down": (boll20_lower is not None and last_close < boll20_lower),
        "close_above_bbi": (bbi_v is not None and last_close > bbi_v),
        "close_below_bbi": (bbi_v is not None and last_close < bbi_v),
    }


def _empty_indicators() -> dict[str, float | bool | None]:
    """df 为空时的全 None 骨架 —— key 集合必须与 compute_indicators 完全一致。"""
    numeric_keys = [
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "macd_dif",
        "macd_dea",
        "macd_bar",
        "rsi6",
        "rsi12",
        "rsi24",
        "cmf20",
        "volume_ratio",
        "bias",
        "atr_pct",
        "support",
        "resistance",
        "turnover_pct",
        "last_close",
        "boll20_mid",
        "boll20_upper",
        "boll20_lower",
        "boll20_bandwidth",
        "boll20_pct_b",
        "boll60_mid",
        "boll60_upper",
        "boll60_lower",
        "boll60_bandwidth",
        "boll60_pct_b",
        "boll_opening_ratio_short_vs_mid",
        "cci14",
        "bbi",
        "kdj_k",
        "kdj_d",
        "kdj_j",
    ]
    bool_keys = [
        "macd_golden_cross_in_3d",
        "macd_death_cross_in_3d",
        "kdj_golden_cross_in_3d",
        "kdj_death_cross_in_3d",
        "kdj_overbought",
        "kdj_oversold",
        "ma_bull_alignment",
        "ma_bear_alignment",
        "close_above_ma20",
        "close_below_ma20",
        "boll20_breakout_up",
        "boll20_breakout_down",
        "close_above_bbi",
        "close_below_bbi",
    ]
    out: dict[str, float | bool | None] = dict.fromkeys(numeric_keys, None)
    out.update(dict.fromkeys(bool_keys, False))
    return out
