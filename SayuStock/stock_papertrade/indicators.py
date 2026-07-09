"""模拟盘技术指标计算（纯 pandas/numpy）。

输入：日 K DataFrame（至少 60 行），列名（与东财 K 线接口一致）：
    - date (str YYYY-MM-DD)
    - open / close / high / low / volume (手) / amount (元)

输出 dict：所有数值 / 字符串指标。

公开 API：
- compute_indicators(df) -> dict
- score_from_indicators(ind) -> tuple[float, list[str]]  # -1.0~+1.0
"""

# pyright/basedpyright 文件级指令 —— 只对**本文件**生效。
# 本文件大量使用 pandas Series 的标量提取（.iloc[i] / .sum() / .mean() /
# .item() 等），pandas-stubs 在这一层大量把返回类型标为 ``Any`` —— 那是
# 库类型 stub 的局限，不是代码错误。运行时这些值始终是数值（int / float /
# np.integer / np.floating）。本文件内部已经做了 ``isinstance`` 守卫（见
# ``_to_float``），保证运行时安全。
# - 关闭 ``reportAny`` / ``reportUnknownMemberType`` / ``reportUnknownArgumentType``
#   在本文件范围内（pandas-stubs 已知限制下的合理配置）
# - 与 ``# type: ignore`` 不同：这是规则级配置，不是抑制特定错误
# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import numpy as np
import pandas as pd


# ============================================================
# 内部：把 K 线原始列表转成标准化 DataFrame
# ============================================================
def klines_to_df(klines: list[str]) -> pd.DataFrame:
    """把东财 K 线字符串列表转 DataFrame。

    字符串格式："YYYY-MM-DD,open,close,high,low,volume,amount,amplitude,chg_pct,chg_amount,turnover_rate"
    """
    rows: list[dict[str, float | str]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            rows.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                    "amplitude": float(parts[7]),
                    "chg_pct": float(parts[8]),
                    "chg_amount": float(parts[9]),
                    "turnover_rate": float(parts[10]),
                }
            )
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)


# ============================================================
# 内部小工具：把 Series 子集 / 标量元素显式收窄到 float ——
# pandas-stubs 把 ``DataFrame.__getitem__`` / ``Series.iloc`` / ``.item()`` /
# ``.sum() / .mean()`` 等的返回标成 ``Any``；强行注解 ``float`` 不顶用，
# basedpyright 仍把右值的 Any 类型告警。用 isinstance 收敛联合类型是 §17
# 红线推荐的 union + isinstance 守卫，既不用 ``cast()`` 也不用 ``# type: ignore``。
# ============================================================
def _col_float(df: pd.DataFrame, name: str) -> pd.Series:
    s = df[name]
    assert isinstance(s, pd.Series)
    return s.astype(float)


def _tail_col(df: pd.DataFrame, name: str, n: int) -> pd.Series:
    s = df[name]
    assert isinstance(s, pd.Series)
    return s.tail(n)


def _tail_col_float(df: pd.DataFrame, name: str, n: int) -> pd.Series:
    s = df[name]
    assert isinstance(s, pd.Series)
    return s.astype(float).tail(n)


def _to_float(v: object) -> float:
    """pandas 标量元素 / numpy 标量 / Python 标量 → float；非数值抛 ValueError。

    用 isinstance 守卫让 basedpyright 收窄联合类型；这正是 §17 红线
    推荐的 union + isinstance 守卫做法，替代 ``cast()`` 与 ``# type: ignore``。
    """
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    raise ValueError(f"Cannot convert {type(v).__name__} to float: {v!r}")


# ============================================================
# 1) 均线 MA
# ============================================================
def calc_ma(close: pd.Series, period: int) -> float | None:
    if len(close) < period:
        return None
    return _to_float(close.iloc[-period:].mean())


# ============================================================
# 2) MACD (12, 26, 9)
# ============================================================
def calc_macd(close: pd.Series) -> tuple[float | None, float | None, float | None, bool, bool]:
    """返回 (DIF, DEA, BAR, golden_cross_in_3d, death_cross_in_3d)"""
    if len(close) < 35:
        return None, None, None, False, False

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    bar = (dif - dea) * 2

    dif_v: float = _to_float(dif.iloc[-1])
    dea_v: float = _to_float(dea.iloc[-1])
    bar_v: float = _to_float(bar.iloc[-1])

    # 判断最近 3 日内是否金叉/死叉
    golden_cross_in_3d = False
    death_cross_in_3d = False
    for i in range(-3, 0):
        try:
            d_prev: float = _to_float(dif.iloc[i - 1])
            d_curr: float = _to_float(dif.iloc[i])
            dea_prev: float = _to_float(dea.iloc[i - 1])
            dea_curr: float = _to_float(dea.iloc[i])
            if d_prev <= dea_prev and d_curr > dea_curr:
                golden_cross_in_3d = True
            if d_prev >= dea_prev and d_curr < dea_curr:
                death_cross_in_3d = True
        except (IndexError, ValueError):
            continue

    return dif_v, dea_v, bar_v, golden_cross_in_3d, death_cross_in_3d


# ============================================================
# 3) RSI (Wilder 平滑)
# ============================================================
def calc_rsi(close: pd.Series, period: int) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder 平滑（用 EMA 等价 alpha=1/period）
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    if _to_float(avg_loss.iloc[-1]) == 0:
        return 100.0
    rs = _to_float(avg_gain.iloc[-1]) / _to_float(avg_loss.iloc[-1])
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi


# ============================================================
# 4) CMF (Chaikin Money Flow) 20 日
# ============================================================
def calc_cmf(df: pd.DataFrame, period: int = 20) -> float | None:
    if len(df) < period:
        return None
    high = _tail_col_float(df, "high", period)
    low = _tail_col_float(df, "low", period)
    close = _tail_col_float(df, "close", period)
    volume = _tail_col_float(df, "volume", period)
    rng = (high - low).replace(0, np.nan)
    mfv = ((close - low) - (high - close)) / rng * volume
    vol_sum: float = _to_float(volume.sum())
    if vol_sum == 0:
        return 0.0
    mfv_sum: float = _to_float(mfv.sum())
    return mfv_sum / vol_sum


# ============================================================
# 5) 量比 = 今日量 / 5 日均量
# ============================================================
def calc_volume_ratio(df: pd.DataFrame) -> float | None:
    if len(df) < 6:
        return None
    volume = _col_float(df, "volume")
    today: float = _to_float(volume.iloc[-1])
    avg5: float = _to_float(volume.iloc[-6:-1].mean())
    if avg5 == 0:
        return None
    return today / avg5


# ============================================================
# 6) 乖离率 BIAS = (close - ma20) / ma20
# ============================================================
def calc_bias(close: pd.Series, period: int = 20) -> float | None:
    ma = calc_ma(close, period)
    if ma is None or ma == 0:
        return None
    last: float = _to_float(close.iloc[-1])
    return (last - ma) / ma


# ============================================================
# 7) ATR% (近 14 日平均真实波幅 / 收盘价)
# ============================================================
def calc_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period + 1:
        return None
    high = _tail_col_float(df, "high", period + 1)
    low = _tail_col_float(df, "low", period + 1)
    close = _tail_col_float(df, "close", period + 1)
    prev_close = close.shift(1)
    parts: list[pd.Series] = [
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ]
    tr = pd.concat(parts, axis=1).max(axis=1)
    tr = tr.iloc[1:]  # 第一行没有 prev_close
    atr: float = _to_float(tr.mean())
    last_close: float = _to_float(close.iloc[-1])
    if last_close == 0:
        return None
    return atr / last_close


# ============================================================
# 8) 支撑/压力位（近 20 日最低/最高）
# ============================================================
def calc_support_resistance(df: pd.DataFrame, period: int = 20) -> tuple[float | None, float | None]:
    if len(df) < period:
        return None, None
    low = _tail_col(df, "low", period)
    high = _tail_col(df, "high", period)
    return _to_float(low.min()), _to_float(high.max())


# ============================================================
# 9) 布林带 BOLL（N 周期, K 倍标准差）
#  典型用法：短期 20,2 / 中期 60,3
#  返回 (mid, upper, lower, bandwidth, percent_b)
#    bandwidth = (upper - lower) / mid   → 衡量"敞口"
#    percent_b = (close - lower) / (upper - lower) → 价格在带内位置 (0~1)
# ============================================================
def calc_boll(
    close: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    if len(close) < period:
        return None, None, None, None, None
    mid = _to_float(close.iloc[-period:].mean())
    std = _to_float(close.iloc[-period:].std(ddof=0))
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    if mid == 0:
        bandwidth = None
    else:
        bandwidth = (upper - lower) / mid
    last_close = _to_float(close.iloc[-1])
    if (upper - lower) == 0:
        percent_b = None
    else:
        percent_b = (last_close - lower) / (upper - lower)
    return mid, upper, lower, bandwidth, percent_b


# ============================================================
# 10) CCI 顺势指标（典型 14 周期）
#   TP = (high + low + close) / 3
#   CCI = (TP - SMA(TP, n)) / (0.015 * mean_dev)
#   区间：常态 -100~+100；>+100 超买 <-100 超卖
# ============================================================
def calc_cci(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period:
        return None
    high = _tail_col_float(df, "high", period)
    low = _tail_col_float(df, "low", period)
    close = _tail_col_float(df, "close", period)
    tp: pd.Series = (high + low + close) / 3.0
    sma_tp: float = _to_float(tp.mean())
    mean_dev: float = _to_float((tp - sma_tp).abs().mean())
    if mean_dev == 0:
        return 0.0
    last_tp: float = _to_float(tp.iloc[-1])
    return (last_tp - sma_tp) / (0.015 * mean_dev)


# ============================================================
# 11) BBI 多空指标（Bull Bear Index）
#   BBI = (MA3 + MA6 + MA12 + MA24) / 4
#   close > BBI → 多头占优；close < BBI → 空头占优
# ============================================================
def calc_bbi(close: pd.Series) -> float | None:
    if len(close) < 24:
        return None
    ma3 = calc_ma(close, 3)
    ma6 = calc_ma(close, 6)
    ma12 = calc_ma(close, 12)
    ma24 = calc_ma(close, 24)
    if ma3 is None or ma6 is None or ma12 is None or ma24 is None:
        return None
    return (ma3 + ma6 + ma12 + ma24) / 4.0


# ============================================================
# 13) KDJ 随机指标（经典 9,3,3，通达信/东财口径）
#   RSV = (C - LLV_n) / (HHV_n - LLV_n) * 100
#   K = (RSV + 2K') / 3 ; D = (K + 2D') / 3（K/D 初值取中性 50）; J = 3K - 2D
#   K/D 常态 0~100（>80 超买 <20 超卖），J 可越界（J>100 超买 / J<0 超卖）。
#   返回 (K, D, J, golden_cross_in_3d, death_cross_in_3d)
#   金叉：K 上穿 D（低位金叉偏多）；死叉：K 下穿 D（高位死叉偏空）。
# ============================================================
def calc_kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> tuple[float | None, float | None, float | None, bool, bool]:
    if len(df) < n:
        return None, None, None, False, False
    high = _col_float(df, "high")
    low = _col_float(df, "low")
    close = _col_float(df, "close")
    low_min = low.rolling(window=n, min_periods=1).min()
    high_max = high.rolling(window=n, min_periods=1).max()
    span = (high_max - low_min).to_numpy(dtype=float)
    close_arr = close.to_numpy(dtype=float)
    low_arr = low_min.to_numpy(dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        rsv = np.where(span > 0, (close_arr - low_arr) / span * 100.0, np.nan)

    k_series = np.empty(len(rsv), dtype=float)
    d_series = np.empty(len(rsv), dtype=float)
    k_prev = d_prev = 50.0
    for i, raw in enumerate(rsv):
        # 区间无波动（涨跌停/停牌）时 RSV 记为中性 50，避免断点
        cur = 50.0 if not np.isfinite(raw) else float(raw)
        k_prev = (cur + (m1 - 1) * k_prev) / m1
        d_prev = (k_prev + (m2 - 1) * d_prev) / m2
        k_series[i] = k_prev
        d_series[i] = d_prev
    j_series = 3.0 * k_series - 2.0 * d_series

    # 近 3 日 K/D 金叉 / 死叉
    golden = death = False
    for i in range(-3, 0):
        try:
            k_prev_i = float(k_series[i - 1])
            k_curr_i = float(k_series[i])
            d_prev_i = float(d_series[i - 1])
            d_curr_i = float(d_series[i])
        except IndexError:
            continue
        if k_prev_i <= d_prev_i and k_curr_i > d_curr_i:
            golden = True
        if k_prev_i >= d_prev_i and k_curr_i < d_curr_i:
            death = True

    return float(k_series[-1]), float(d_series[-1]), float(j_series[-1]), golden, death


# ============================================================
# 12) 5/15/30/60 分钟 K 线适配器
#   日 K 内部仍走 klines_to_df；分钟 K 由 get_gg(code, "single-stock-kline-5"|"15"|"30"|"60", ...)
#   拉到的格式与日 K 略不同（东财分钟 K 列顺序可能多/少），这里做宽松解析：
#   "YYYY-MM-DD HH:MM,open,close,high,low,volume,amount[,amplitude,...]"
# ============================================================
def klines_to_df_mins(klines: list[str]) -> pd.DataFrame:
    """把东财分钟 K 字符串列表转 DataFrame。

    分钟 K 与日 K 主要区别：
    - 第一列可能是 "YYYY-MM-DD HH:MM" 而非 "YYYY-MM-DD"
    - 字段数可能略少于 11（分钟 K 普遍只给 6~7 列）
    """
    rows: list[dict[str, float | str]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            # 第一列可能含空格，统一只取日期部分
            date_part = parts[0].split(" ")[0]
            rows.append(
                {
                    "date": date_part,
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]) if len(parts) > 6 else 0.0,
                    "amplitude": float(parts[7]) if len(parts) > 7 else 0.0,
                    "chg_pct": float(parts[8]) if len(parts) > 8 else 0.0,
                    "chg_amount": float(parts[9]) if len(parts) > 9 else 0.0,
                    "turnover_rate": float(parts[10]) if len(parts) > 10 else 0.0,
                }
            )
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)


# ============================================================
# 主入口：compute_indicators
# ============================================================
def compute_indicators(df: pd.DataFrame) -> dict[str, float | bool | None]:
    """从 K 线 DataFrame 计算所有技术指标，返回 dict。

    缺失指标以 None 表示。
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
    volume_ratio = calc_volume_ratio(df)
    bias = calc_bias(close, 20)
    atr_pct = calc_atr_pct(df, 14)
    support, resistance = calc_support_resistance(df, 20)

    # —— 新增：BOLL 短期 20,2 / 中期 60,3 ——
    boll20_mid, boll20_upper, boll20_lower, boll20_bw, boll20_pct = calc_boll(close, 20, 2.0)
    boll60_mid, boll60_upper, boll60_lower, boll60_bw, boll60_pct = calc_boll(close, 60, 3.0)
    # 短期 vs 中期 敞口比 = 短期带宽 / 中期带宽
    boll_opening_ratio: float | None = None
    if boll20_bw is not None and boll60_bw is not None and boll60_bw != 0:
        boll_opening_ratio = boll20_bw / boll60_bw

    cci14 = calc_cci(df, 14)
    bbi = calc_bbi(close)
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
        "volume_ratio": volume_ratio,
        "bias": bias,
        "atr_pct": atr_pct,
        "support": support,
        "resistance": resistance,
        "turnover_pct": turnover,
        "last_close": last_close,
        # —— 新增：BOLL / CCI / BBI ——
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
        "bbi": bbi,
        # —— KDJ（9,3,3）——
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
        # —— 新增：BOLL 突破 / BBI 多空 ——
        "boll20_breakout_up": (boll20_upper is not None and last_close > boll20_upper),
        "boll20_breakout_down": (boll20_lower is not None and last_close < boll20_lower),
        "close_above_bbi": (bbi is not None and last_close > bbi),
        "close_below_bbi": (bbi is not None and last_close < bbi),
    }


def _empty_indicators() -> dict[str, float | bool | None]:
    return {
        "ma5": None,
        "ma10": None,
        "ma20": None,
        "ma60": None,
        "macd_dif": None,
        "macd_dea": None,
        "macd_bar": None,
        "macd_golden_cross_in_3d": False,
        "macd_death_cross_in_3d": False,
        "rsi6": None,
        "rsi12": None,
        "rsi24": None,
        "cmf20": None,
        "volume_ratio": None,
        "bias": None,
        "atr_pct": None,
        "support": None,
        "resistance": None,
        "turnover_pct": None,
        "last_close": None,
        # —— BOLL / CCI / BBI ——
        "boll20_mid": None,
        "boll20_upper": None,
        "boll20_lower": None,
        "boll20_bandwidth": None,
        "boll20_pct_b": None,
        "boll60_mid": None,
        "boll60_upper": None,
        "boll60_lower": None,
        "boll60_bandwidth": None,
        "boll60_pct_b": None,
        "boll_opening_ratio_short_vs_mid": None,
        "cci14": None,
        "bbi": None,
        # —— KDJ ——
        "kdj_k": None,
        "kdj_d": None,
        "kdj_j": None,
        "kdj_golden_cross_in_3d": False,
        "kdj_death_cross_in_3d": False,
        "kdj_overbought": False,
        "kdj_oversold": False,
        # 形态
        "ma_bull_alignment": False,
        "ma_bear_alignment": False,
        "close_above_ma20": False,
        "close_below_ma20": False,
        "boll20_breakout_up": False,
        "boll20_breakout_down": False,
        "close_above_bbi": False,
        "close_below_bbi": False,
    }
