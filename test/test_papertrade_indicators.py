"""AI 模拟盘技术指标核心单测。"""

from __future__ import annotations

import sys
import importlib.util
from types import ModuleType
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_ind_test"


def _ensure_pkg() -> None:
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        PKG_ROOT / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT)],
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
    sub_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade",
        PKG_ROOT / "stock_papertrade" / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT / "stock_papertrade")],
    )
    assert sub_spec is not None
    sub = importlib.util.module_from_spec(sub_spec)
    sub.__path__ = [str(PKG_ROOT / "stock_papertrade")]
    sys.modules[f"{PKG_NAME}.stock_papertrade"] = sub


def _load(name: str, file_name: str) -> ModuleType:
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade.{name}",
        PKG_ROOT / "stock_papertrade" / file_name,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ind = _load("indicators", "indicators.py")
klines_to_df = ind.klines_to_df
compute_indicators = ind.compute_indicators
calc_ma = ind.calc_ma
calc_macd = ind.calc_macd
calc_rsi = ind.calc_rsi
calc_cmf = ind.calc_cmf
calc_volume_ratio = ind.calc_volume_ratio
calc_bias = ind.calc_bias
calc_atr_pct = ind.calc_atr_pct
calc_support_resistance = ind.calc_support_resistance
calc_kdj = ind.calc_kdj


def _make_klines(n: int = 60, base_price: float = 100.0, drift: float = 0.0, seed: int = 42) -> list[str]:
    rng = np.random.default_rng(seed)
    out: list[str] = []
    base = datetime(2025, 1, 1)
    price = base_price
    for i in range(n):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        open_p = price
        close_p = price * (1 + float(rng.normal(drift, 0.02)))
        high_p = max(open_p, close_p) * 1.005
        low_p = min(open_p, close_p) * 0.995
        volume = 1_000_000 + i * 1000
        out.append(
            f"{date},{open_p:.2f},{close_p:.2f},{high_p:.2f},{low_p:.2f},"
            f"{volume:.0f},{close_p * volume:.0f},2.0,0.5,0.0,1.5"
        )
        price = close_p
    return out


def _make_trending_klines(n: int = 60, base_price: float = 100.0, daily_pct: float = 0.02) -> list[str]:
    out: list[str] = []
    base = datetime(2025, 1, 1)
    price = base_price
    for i in range(n):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        open_p = price
        close_p = price * (1 + daily_pct)
        high_p = max(open_p, close_p) * 1.003
        low_p = min(open_p, close_p) * 0.997
        out.append(
            f"{date},{open_p:.2f},{close_p:.2f},{high_p:.2f},{low_p:.2f},"
            f"1000000,{close_p * 1_000_000:.0f},1.0,1.0,0.0,1.5"
        )
        price = close_p
    return out


def test_klines_to_df_parsing_and_garbage() -> None:
    df = klines_to_df(_make_klines(10))
    assert len(df) == 10 and "close" in df.columns and "turnover_rate" in df.columns and df["close"].iloc[-1] > 0
    assert klines_to_df([]).empty
    assert klines_to_df(["not a kline", "123,456", "abc,xyz,def,1,2,3,4,5,6,7,8"]).empty


def test_calc_ma() -> None:
    close = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    assert calc_ma(close, 5) == 8.0
    assert calc_ma(close, 10) == 5.5
    assert calc_ma(pd.Series([1, 2, 3], dtype=float), 5) is None


def test_calc_macd_signals() -> None:
    up = pd.Series(list(np.linspace(100, 50, 60)) + [51, 55, 62], dtype=float)
    dif, dea, _, gold, death = calc_macd(up)
    assert dif is not None and dea is not None and dif > dea and gold and death is False

    down = pd.Series(list(np.linspace(50, 100, 60)) + [99, 95, 88], dtype=float)
    dif2, dea2, _, gold2, death2 = calc_macd(down)
    assert dif2 is not None and dea2 is not None and dif2 < dea2 and death2 and gold2 is False


def test_calc_rsi_range_and_extremes() -> None:
    rng = np.random.default_rng(123)
    close = pd.Series(np.cumsum(rng.normal(0, 1, 30)) + 100, dtype=float)
    for period in (6, 12, 24):
        val = calc_rsi(close, period)
        assert val is not None and 0 <= val <= 100
    assert calc_rsi(pd.Series(np.linspace(100, 200, 30), dtype=float), 6) > 90
    assert calc_rsi(pd.Series(np.linspace(200, 100, 30), dtype=float), 6) < 10


def test_calc_volume_and_volatility_metrics() -> None:
    df = klines_to_df(_make_klines(30, seed=7))
    cmf = calc_cmf(df, period=20)
    atr = calc_atr_pct(df, period=14)
    sup, res = calc_support_resistance(df, period=20)
    assert cmf is not None and -1 <= cmf <= 1
    assert atr is not None and 0 < atr < 0.5
    assert sup is not None and res is not None and sup < res

    vol_df = pd.DataFrame(
        {"open": [10] * 10, "close": [10] * 10, "high": [10] * 10, "low": [10] * 10, "volume": [100] * 9 + [200]}
    )
    assert calc_volume_ratio(vol_df) == 2.0

    bias_close = pd.Series([100.0] * 29 + [110.0])
    bias = calc_bias(bias_close, period=20)
    assert bias is not None and abs(bias - (110 - 100.5) / 100.5) < 1e-3


def test_calc_kdj() -> None:
    df_up = klines_to_df(_make_trending_klines(60, daily_pct=0.02))
    k, d, j, gold, death = calc_kdj(df_up)
    assert k is not None and d is not None and j is not None
    assert 0 <= k <= 100 and 0 <= d <= 100 and k > 70 and d > 70
    assert isinstance(gold, bool) and isinstance(death, bool)

    df = klines_to_df(_make_klines(80, seed=11))
    k2, d2, j2, _, _ = calc_kdj(df)
    low_min = df["low"].astype(float).rolling(9, min_periods=1).min()
    high_max = df["high"].astype(float).rolling(9, min_periods=1).max()
    rsv = (df["close"].astype(float) - low_min) / (high_max - low_min) * 100.0
    kp = dp = 50.0
    for r in rsv:
        val = 50.0 if not np.isfinite(r) else float(r)
        kp = (val + 2 * kp) / 3
        dp = (kp + 2 * dp) / 3
    assert abs(k2 - kp) < 1e-9 and abs(d2 - dp) < 1e-9 and abs(j2 - (3 * kp - 2 * dp)) < 1e-9
    assert calc_kdj(klines_to_df(_make_klines(5))) == (None, None, None, False, False)


def test_compute_indicators_full_empty_short() -> None:
    full = compute_indicators(klines_to_df(_make_klines(60, seed=2024)))
    for key in ("ma5", "ma20", "macd_dif", "rsi6", "cmf20", "last_close", "kdj_k", "kdj_d", "kdj_j"):
        assert full[key] is not None
    assert "kdj_golden_cross_in_3d" in full and "kdj_overbought" in full

    empty = compute_indicators(pd.DataFrame())
    assert empty["ma5"] is None and empty["ma_bull_alignment"] is False

    short = compute_indicators(klines_to_df(_make_klines(20)))
    assert short["ma5"] is not None and short["ma20"] is not None
    assert short["ma60"] is None and short["macd_dif"] is None


def test_compute_indicators_bull_alignment() -> None:
    out = compute_indicators(klines_to_df(_make_trending_klines(60, daily_pct=0.02)))
    assert out["ma5"] is not None and out["ma10"] is not None and out["ma20"] is not None
    assert out["ma_bull_alignment"] is True and out["close_above_ma20"] is True
