"""AI 模拟盘技术指标计算（纯 pandas/numpy）。

输入：日 K DataFrame（至少 60 行），列名（与东财 K 线接口一致）：
    - date (str YYYY-MM-DD)
    - open / close / high / low / volume (手) / amount (元)

输出 dict：所有数值 / 字符串指标。

公开 API：
- compute_indicators(df) -> dict
- score_from_indicators(ind) -> tuple[float, list[str]]  # -1.0~+1.0
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ============================================================
# 内部：把 K 线原始列表转成标准化 DataFrame
# ============================================================
def klines_to_df(klines: List[str]) -> pd.DataFrame:
    """把东财 K 线字符串列表转 DataFrame。

    字符串格式："YYYY-MM-DD,open,close,high,low,volume,amount,amplitude,chg_pct,chg_amount,turnover_rate"
    """
    rows: List[Dict[str, Any]] = []
    for line in klines:
        if not isinstance(line, str):
            continue
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
# 1) 均线 MA
# ============================================================
def calc_ma(close: pd.Series, period: int) -> Optional[float]:
    if len(close) < period:
        return None
    return float(close.iloc[-period:].mean())


# ============================================================
# 2) MACD (12, 26, 9)
# ============================================================
def calc_macd(
    close: pd.Series,
) -> Tuple[Optional[float], Optional[float], Optional[float], bool, bool]:
    """返回 (DIF, DEA, BAR, golden_cross_in_3d, death_cross_in_3d)"""
    if len(close) < 35:
        return None, None, None, False, False

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    bar = (dif - dea) * 2

    dif_v = float(dif.iloc[-1])
    dea_v = float(dea.iloc[-1])
    bar_v = float(bar.iloc[-1])

    # 判断最近 3 日内是否金叉/死叉
    golden_cross_in_3d = False
    death_cross_in_3d = False
    for i in range(-3, 0):
        try:
            d_prev = float(dif.iloc[i - 1])
            d_curr = float(dif.iloc[i])
            dea_prev = float(dea.iloc[i - 1])
            dea_curr = float(dea.iloc[i])
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
def calc_rsi(close: pd.Series, period: int) -> Optional[float]:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder 平滑（用 EMA 等价 alpha=1/period）
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    if float(avg_loss.iloc[-1]) == 0:
        return 100.0
    rs = float(avg_gain.iloc[-1]) / float(avg_loss.iloc[-1])
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi


# ============================================================
# 4) CMF (Chaikin Money Flow) 20 日
# ============================================================
def calc_cmf(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    if len(df) < period:
        return None
    sub = df.tail(period).copy()
    high = sub["high"].astype(float)
    low = sub["low"].astype(float)
    close = sub["close"].astype(float)
    volume = sub["volume"].astype(float)
    rng = (high - low).replace(0, np.nan)
    mfv = ((close - low) - (high - close)) / rng * volume
    cmf = float(mfv.sum() / volume.sum()) if volume.sum() != 0 else 0.0
    return cmf


# ============================================================
# 5) 量比 = 今日量 / 5 日均量
# ============================================================
def calc_volume_ratio(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 6:
        return None
    today = float(df["volume"].iloc[-1])
    avg5 = float(df["volume"].iloc[-6:-1].mean())
    if avg5 == 0:
        return None
    return today / avg5


# ============================================================
# 6) 乖离率 BIAS = (close - ma20) / ma20
# ============================================================
def calc_bias(close: pd.Series, period: int = 20) -> Optional[float]:
    ma = calc_ma(close, period)
    if ma is None or ma == 0:
        return None
    return float((close.iloc[-1] - ma) / ma)


# ============================================================
# 7) ATR% (近 14 日平均真实波幅 / 收盘价)
# ============================================================
def calc_atr_pct(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 1:
        return None
    sub = df.tail(period + 1).copy()
    high = sub["high"].astype(float)
    low = sub["low"].astype(float)
    close = sub["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr = tr.iloc[1:]  # 第一行没有 prev_close
    atr = float(tr.mean())
    last_close = float(close.iloc[-1])
    if last_close == 0:
        return None
    return atr / last_close


# ============================================================
# 8) 支撑/压力位（近 20 日最低/最高）
# ============================================================
def calc_support_resistance(df: pd.DataFrame, period: int = 20) -> Tuple[Optional[float], Optional[float]]:
    if len(df) < period:
        return None, None
    sub = df.tail(period)
    return float(sub["low"].min()), float(sub["high"].max())


# ============================================================
# 主入口：compute_indicators
# ============================================================
def compute_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """从 K 线 DataFrame 计算所有技术指标，返回 dict。

    缺失指标以 None 表示。
    """
    if df.empty:
        return _empty_indicators()

    df = df.copy()
    df["close"] = df["close"].astype(float)
    close = df["close"]

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

    turnover = float(df["turnover_rate"].iloc[-1]) if "turnover_rate" in df.columns else None
    last_close = float(close.iloc[-1])

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
        # 形态特征
        "ma_bull_alignment": (
            ma5 is not None and ma10 is not None and ma20 is not None
            and ma5 > ma10 > ma20
        ),
        "ma_bear_alignment": (
            ma5 is not None and ma10 is not None and ma20 is not None
            and ma5 < ma10 < ma20
        ),
        "close_above_ma20": (
            ma20 is not None and last_close > ma20
        ),
        "close_below_ma20": (
            ma20 is not None and last_close < ma20
        ),
    }


def _empty_indicators() -> Dict[str, Any]:
    return {
        "ma5": None, "ma10": None, "ma20": None, "ma60": None,
        "macd_dif": None, "macd_dea": None, "macd_bar": None,
        "macd_golden_cross_in_3d": False, "macd_death_cross_in_3d": False,
        "rsi6": None, "rsi12": None, "rsi24": None,
        "cmf20": None, "volume_ratio": None, "bias": None, "atr_pct": None,
        "support": None, "resistance": None,
        "turnover_pct": None, "last_close": None,
        "ma_bull_alignment": False, "ma_bear_alignment": False,
        "close_above_ma20": False, "close_below_ma20": False,
    }
