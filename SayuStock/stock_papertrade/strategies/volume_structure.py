"""量能区位：月K粗筛 + 日K确认 + 进程缓存。

被 ``volume_extremum`` 硬闸和 ``papertrade_volume_scan`` 共用，避免两套算法。
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional
from dataclasses import dataclass

import pandas as pd

from ..indicators import calc_rel_volume, calc_close_percentile

__all__ = [
    "VolumeStructure",
    "measure_from_ohlcv",
    "evaluate_location",
    "load_structure",
    "structure_from_indicators",
]

_MONTH_TTL_SEC = 6 * 3600
_DAY_TTL_SEC = 30 * 60
_CACHE_CAP = 256
_kline_cache: dict[str, tuple[float, object]] = {}
_struct_cache: dict[str, tuple[float, object]] = {}


def _last_finite_float(series: pd.Series) -> float | None:
    raw = series.iloc[-1]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value:
        return None
    return value


@dataclass(frozen=True, slots=True)
class VolumeStructure:
    rel_volume: Optional[float]
    close_percentile: Optional[float]
    month_percentile: Optional[float]
    bullish_close: bool
    month_passed: bool
    used_daily: bool
    lookback_m: int
    vol_ma_n: int
    month_lookback_years: int

    def as_indicators(self) -> dict[str, Any]:
        return {
            "rel_volume": self.rel_volume,
            "close_percentile": self.close_percentile,
            "month_percentile": self.month_percentile,
            "bullish_close": self.bullish_close,
            "month_passed": self.month_passed,
            "used_daily": self.used_daily,
            "structure_source": "kline_function",
        }


def measure_from_ohlcv(df: pd.DataFrame, params: Mapping[str, Any]) -> VolumeStructure | str:
    """只用日 K 算放量 + 局部分位。"""
    lookback = int(params["lookback_m"])
    vol_n = int(params["vol_ma_n"])
    years = int(params["month_lookback_years"]) if "month_lookback_years" in params else 5
    if df.empty or len(df) < vol_n:
        return f"⚠️ 日K不足（{len(df)} 根，至少需要 {vol_n} 根）"
    if "close" not in df.columns or "volume" not in df.columns:
        return "⚠️ 日K缺少 close/volume 列"
    close = df["close"].astype(float)
    if not isinstance(close, pd.Series):
        return "⚠️ close 列无法当作序列"
    rel = calc_rel_volume(df, vol_n)
    pct = calc_close_percentile(close, lookback)
    if rel is None or pct is None:
        return "⚠️ 无法计算 rel_volume / close_percentile（窗口内数据无效）"
    last_close = float(close.iloc[-1])
    if "open" in df.columns:
        open_col = df["open"]
        if isinstance(open_col, pd.Series):
            open_num = pd.to_numeric(open_col, errors="coerce")
            last_open = _last_finite_float(open_num) if isinstance(open_num, pd.Series) else None
            bullish = last_open is not None and last_close > last_open
        else:
            bullish = False
    else:
        bullish = False
    return VolumeStructure(
        rel_volume=float(rel),
        close_percentile=float(pct),
        month_percentile=None,
        bullish_close=bullish,
        month_passed=True,
        used_daily=True,
        lookback_m=lookback,
        vol_ma_n=vol_n,
        month_lookback_years=years,
    )


def evaluate_location(
    month_df: pd.DataFrame,
    day_df: Optional[pd.DataFrame],
    params: Mapping[str, Any],
    intent: str,
) -> VolumeStructure | str:
    """月K粗筛；过关且给了日K才做日K确认。intent = buy / sell / scan。"""
    years = int(params["month_lookback_years"])
    if month_df.empty or "close" not in month_df.columns:
        return "⚠️ 月K为空，无法粗筛五年区位"
    month_close = month_df["close"].astype(float)
    if not isinstance(month_close, pd.Series):
        return "⚠️ 月K close 列无法当作序列"
    month_pct = calc_close_percentile(month_close, len(month_close))
    if month_pct is None:
        return "⚠️ 无法计算月K分位"
    m_bot = float(params["month_bottom_pct"])
    m_top = float(params["month_top_pct"])
    if intent == "buy":
        month_ok = month_pct <= m_bot
    elif intent == "sell":
        month_ok = month_pct >= m_top
    else:
        month_ok = month_pct <= m_bot or month_pct >= m_top
    base = VolumeStructure(
        rel_volume=None,
        close_percentile=None,
        month_percentile=float(month_pct),
        bullish_close=False,
        month_passed=month_ok,
        used_daily=False,
        lookback_m=int(params["lookback_m"]),
        vol_ma_n=int(params["vol_ma_n"]),
        month_lookback_years=years,
    )
    if not month_ok or day_df is None:
        return base
    daily = measure_from_ohlcv(day_df, params)
    if isinstance(daily, str):
        return daily
    return VolumeStructure(
        rel_volume=daily.rel_volume,
        close_percentile=daily.close_percentile,
        month_percentile=float(month_pct),
        bullish_close=daily.bullish_close,
        month_passed=True,
        used_daily=True,
        lookback_m=daily.lookback_m,
        vol_ma_n=daily.vol_ma_n,
        month_lookback_years=years,
    )


async def load_structure(
    stock_code: str,
    params: Mapping[str, Any],
    *,
    intent: str = "scan",
) -> VolumeStructure | str:
    """先月K、过关再日K。结果按标的+意图缓存。"""
    import datetime as _dt

    from ...utils.market import KlinePeriod
    from ...utils.market.convert.dataframe import kline_to_df

    key = _struct_key(stock_code, params, intent)
    hit = _cache_get(_struct_cache, key, _DAY_TTL_SEC)
    if hit is not None:
        return hit

    end = _dt.date.today()
    years = int(params["month_lookback_years"])
    month_start = end - _dt.timedelta(days=365 * years + 30)
    month_series = await _cached_kline(stock_code, KlinePeriod.MON1, month_start, end, _MONTH_TTL_SEC)
    if isinstance(month_series, str):
        return month_series
    month_df = kline_to_df(month_series)

    coarse = evaluate_location(month_df, None, params, intent)
    if isinstance(coarse, str):
        return coarse
    if not coarse.month_passed:
        _cache_put(_struct_cache, key, coarse)
        return coarse

    day_start = end - _dt.timedelta(days=int(params["lookback_m"] * 1.5) + 10)
    day_series = await _cached_kline(stock_code, KlinePeriod.D1, day_start, end, _DAY_TTL_SEC)
    if isinstance(day_series, str):
        return day_series
    result = evaluate_location(month_df, kline_to_df(day_series), params, intent)
    if isinstance(result, str):
        return result
    _cache_put(_struct_cache, key, result)
    return result


async def _cached_kline(code: str, period: Any, start: Any, end: Any, ttl: float) -> Any:
    from ...utils.market import get_market, is_market_error

    cache_key = f"{code}:{period}:{start}:{end}"
    cached = _cache_get(_kline_cache, cache_key, ttl)
    if cached is not None:
        return cached
    series = await get_market().kline(code, period, start=start, end=end)
    if is_market_error(series):
        return f"⚠️ 拉K线失败({period}): {series.message}"
    _cache_put(_kline_cache, cache_key, series)
    return series


def _struct_key(code: str, params: Mapping[str, Any], intent: str) -> str:
    import datetime as _dt

    sig = (
        f"{int(params['month_lookback_years'])}:"
        f"{params['month_bottom_pct']}:{params['month_top_pct']}:"
        f"{int(params['lookback_m'])}:{int(params['vol_ma_n'])}"
    )
    return f"{code.strip()}:{intent}:{sig}:{_dt.date.today().isoformat()}"


def _cache_get(store: dict[str, tuple[float, Any]], key: str, ttl: float) -> Any | None:
    if key not in store:
        return None
    ts, value = store[key]
    if time.monotonic() - ts > ttl:
        del store[key]
        return None
    return value


def _cache_put(store: dict[str, tuple[float, Any]], key: str, value: Any) -> None:
    if len(store) >= _CACHE_CAP:
        oldest = min(store.items(), key=lambda kv: kv[1][0])
        store.pop(oldest[0], None)
    store[key] = (time.monotonic(), value)


def structure_from_indicators(ind: Mapping[str, Any]) -> Optional[VolumeStructure]:
    month_pct = _finite(ind["month_percentile"]) if "month_percentile" in ind else None
    month_passed = bool(ind["month_passed"]) if "month_passed" in ind else True
    rel = _finite(ind["rel_volume"]) if "rel_volume" in ind else None
    pct = _finite(ind["close_percentile"]) if "close_percentile" in ind else None
    if month_pct is None and rel is None and pct is None:
        return None
    bull_raw = ind["bullish_close"] if "bullish_close" in ind else False
    if isinstance(bull_raw, str):
        bullish = bull_raw.strip().lower() in ("1", "true", "yes", "是")
    else:
        bullish = bool(bull_raw)
    return VolumeStructure(
        rel_volume=rel,
        close_percentile=pct,
        month_percentile=month_pct,
        bullish_close=bullish,
        month_passed=month_passed,
        used_daily=rel is not None and pct is not None,
        lookback_m=0,
        vol_ma_n=0,
        month_lookback_years=5,
    )


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f
