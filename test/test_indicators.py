"""共享指标模块 SayuStock/utils/indicators.py 核心单测。

重点覆盖：通达信/东财口径、series 与 papertrade 标量同源一致性、区间涨跌/回撤不变量及边界输入。
"""

from __future__ import annotations

import sys
import importlib.util
from types import ModuleType
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_indicators_test"


def _ensure_pkg() -> None:
    """搭出最小包骨架，避免 import SayuStock 触发整条插件注册链。"""
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
    for sub in ("utils", "stock_papertrade"):
        sub_spec = importlib.util.spec_from_file_location(
            f"{PKG_NAME}.{sub}",
            PKG_ROOT / sub / "__init__.py",
            submodule_search_locations=[str(PKG_ROOT / sub)],
        )
        if sub_spec is None:
            mod = ModuleType(f"{PKG_NAME}.{sub}")
            mod.__path__ = [str(PKG_ROOT / sub)]
            sys.modules[f"{PKG_NAME}.{sub}"] = mod
            continue
        mod = importlib.util.module_from_spec(sub_spec)
        mod.__path__ = [str(PKG_ROOT / sub)]
        sys.modules[f"{PKG_NAME}.{sub}"] = mod


def _load(dotted: str, rel_path: str) -> ModuleType:
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG_NAME}.{dotted}", PKG_ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ind = _load("utils.indicators", "utils/indicators.py")
pt = _load("stock_papertrade.indicators", "stock_papertrade/indicators.py")


def _series(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.0005, 0.02, n)))
    high = close * (1 + rng.uniform(0.001, 0.03, n))
    low = close * (1 - rng.uniform(0.001, 0.03, n))
    volume = pd.Series(rng.uniform(1e4, 1e6, n))
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "close": close,
            "high": np.maximum(high, close),
            "low": np.minimum(low, close),
            "volume": volume,
            "amount": volume * close,
            "turnover_rate": rng.uniform(0.1, 5, n),
        }
    )


@pytest.fixture
def df() -> pd.DataFrame:
    return _series()


def test_macd_ema_and_double_bar(df: pd.DataFrame) -> None:
    """通达信/东财 BAR = (DIF-DEA)*2。"""
    close = df["close"]
    dif, dea, bar = ind.macd(close)
    expected_dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    pd.testing.assert_series_equal(dif, expected_dif, check_names=False)
    pd.testing.assert_series_equal(dea, dif.ewm(span=9, adjust=False).mean(), check_names=False)
    pd.testing.assert_series_equal(bar, (dif - dea) * 2.0, check_names=False)


def test_boll_basis_is_close_not_typical_price(df: pd.DataFrame) -> None:
    close = df["close"]
    mid, upper, lower = ind.boll(close, 20, 2.0)
    pd.testing.assert_series_equal(mid, close.rolling(20).mean(), check_names=False)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    assert not np.allclose(mid.dropna().to_numpy(), typical.rolling(20).mean().dropna().to_numpy())
    std = close.rolling(20).std(ddof=0)
    pd.testing.assert_series_equal(upper, mid + 2.0 * std, check_names=False)
    pd.testing.assert_series_equal(lower, mid - 2.0 * std, check_names=False)


def test_rsi_wilder_and_extremes() -> None:
    close = pd.Series([44.0, 44.3, 44.1, 44.2, 44.5, 43.4, 44.3, 44.8, 45.0, 45.9, 46.0, 45.6])
    delta = close.diff()
    gain = pd.Series(delta.clip(lower=0.0).ewm(alpha=1 / 6, adjust=False).mean())
    loss = pd.Series((-delta.clip(upper=0.0)).ewm(alpha=1 / 6, adjust=False).mean())
    expected = pd.Series(100.0 - 100.0 / (1.0 + gain / loss))
    assert ind.rsi(close, 6).iloc[-1] == pytest.approx(expected.iloc[-1])
    assert ind.rsi(pd.Series(np.arange(1, 30, dtype=float)), 6).iloc[-1] == 100.0
    assert ind.rsi(pd.Series(np.arange(30, 1, -1, dtype=float)), 6).iloc[-1] == pytest.approx(0.0)


def test_kdj_recursion_and_flat_bar() -> None:
    rng = np.random.default_rng(3)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 20)))
    high, low = close + 1.0, close - 1.0
    k, d, j = ind.kdj(high, low, close, 9, 3, 3)
    low_min = low.rolling(9, min_periods=1).min()
    high_max = high.rolling(9, min_periods=1).max()
    rsv = (close - low_min) / (high_max - low_min) * 100.0
    kp = dp = 50.0
    for r in rsv:
        kp = (r + 2 * kp) / 3
        dp = (kp + 2 * dp) / 3
    assert k.iloc[-1] == pytest.approx(kp)
    assert d.iloc[-1] == pytest.approx(dp)
    assert j.iloc[-1] == pytest.approx(3 * kp - 2 * dp)

    flat = pd.Series([10.0] * 15)
    fk, fd, fj = ind.kdj(flat.copy(), flat.copy(), flat)
    assert fk.notna().all() and fd.notna().all() and fj.notna().all()
    assert fk.iloc[-1] == pytest.approx(50.0)


def test_bbi_is_mean_of_four_mas(df: pd.DataFrame) -> None:
    close = df["close"]
    expected = (
        close.rolling(3).mean() + close.rolling(6).mean() + close.rolling(12).mean() + close.rolling(24).mean()
    ) / 4.0
    pd.testing.assert_series_equal(ind.bbi(close), expected, check_names=False)


def test_series_last_equals_papertrade_scalar(df: pd.DataFrame) -> None:
    """图表 series 末值与 AI 标量层必须同源。"""
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    assert ind.ma(close, 20).iloc[-1] == pytest.approx(pt.calc_ma(close, 20))
    assert ind.bbi(close).iloc[-1] == pytest.approx(pt.calc_bbi(close))
    assert ind.rsi(close, 6).iloc[-1] == pytest.approx(pt.calc_rsi(close, 6))
    assert ind.cci(high, low, close, 14).iloc[-1] == pytest.approx(pt.calc_cci(df, 14))
    assert ind.cmf(high, low, close, volume, 20).iloc[-1] == pytest.approx(pt.calc_cmf(df, 20))
    assert ind.bias(close, 20).iloc[-1] == pytest.approx(pt.calc_bias(close, 20))
    assert ind.atr_pct(high, low, close, 14).iloc[-1] == pytest.approx(pt.calc_atr_pct(df, 14))
    assert ind.volume_ratio(volume, 5).iloc[-1] == pytest.approx(pt.calc_volume_ratio(df))

    dif, dea, bar = ind.macd(close)
    p_dif, p_dea, p_bar, _, _ = pt.calc_macd(close)
    assert (dif.iloc[-1], dea.iloc[-1], bar.iloc[-1]) == pytest.approx((p_dif, p_dea, p_bar))

    k, d, j = ind.kdj(high, low, close)
    p_k, p_d, p_j, _, _ = pt.calc_kdj(df)
    assert (k.iloc[-1], d.iloc[-1], j.iloc[-1]) == pytest.approx((p_k, p_d, p_j))

    mid, upper, lower = ind.boll(close, 20, 2.0)
    p_mid, p_up, p_low, _, _ = pt.calc_boll(close, 20, 2.0)
    assert (mid.iloc[-1], upper.iloc[-1], lower.iloc[-1]) == pytest.approx((p_mid, p_up, p_low))

    support, resistance = ind.support_resistance(high, low, 20)
    p_sup, p_res = pt.calc_support_resistance(df, 20)
    assert (support.iloc[-1], resistance.iloc[-1]) == pytest.approx((p_sup, p_res))


def test_compute_indicators_matches_series_layer(df: pd.DataFrame) -> None:
    out = pt.compute_indicators(df)
    close, high, low = df["close"], df["high"], df["low"]
    assert out["kdj_k"] == pytest.approx(ind.kdj(high, low, close)[0].iloc[-1])
    assert out["macd_bar"] == pytest.approx(ind.macd(close)[2].iloc[-1])
    assert out["bbi"] == pytest.approx(ind.bbi(close).iloc[-1])
    assert out["boll20_upper"] == pytest.approx(ind.boll(close, 20, 2.0)[1].iloc[-1])
    assert out["rsi6"] == pytest.approx(ind.rsi(close, 6).iloc[-1])


def test_cross_signals() -> None:
    fast = pd.Series([1.0, 1.0, 1.0, 0.0, 2.0])
    slow = pd.Series([1.5, 1.5, 1.5, 1.0, 1.0])
    assert ind.cross_signals(fast, slow, days=3) == (True, False)
    assert ind.cross_signals(slow, fast, days=3) == (False, True)
    old_fast = pd.Series([0.0, 2.0, 2.0, 2.0, 2.0, 2.0])
    old_slow = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    assert ind.cross_signals(old_fast, old_slow, days=3)[0] is False


def test_normalize_pct(df: pd.DataFrame) -> None:
    out = ind.normalize_pct(df["close"])
    assert out.iloc[0] == pytest.approx(0.0)
    assert out.iloc[-1] == pytest.approx(df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    assert ind.normalize_pct(pd.Series([0.0, 1.0, 2.0])).isna().all()
    assert ind.normalize_pct(pd.Series([], dtype=float)).empty


def _pct(closes: list[float]) -> pd.Series:
    c = np.asarray(closes, dtype=float)
    return pd.Series((c / c[0] - 1.0) * 100.0)


def test_swing_stats_core_and_invariants() -> None:
    assert ind.swing_stats(_pct([100, 250, 125])) == pytest.approx((150.0, -50.0))
    assert ind.swing_stats(_pct([100, 85, 130])) == pytest.approx(((130 / 85 - 1) * 100, -15.0))
    assert ind.swing_stats(_pct([100, 150, 200])) == pytest.approx((100.0, 0.0))
    assert ind.swing_stats(_pct([100, 70, 40])) == pytest.approx((0.0, -60.0))
    runup, drawdown = ind.swing_stats(_pct([100, 80, 100, 60, 200]))
    assert drawdown == pytest.approx(-40.0)
    assert runup == pytest.approx(200 / 60 * 100 - 100, abs=1e-3)

    for seed in range(10):
        rng = np.random.default_rng(seed)
        close = 100 * np.cumprod(1 + rng.normal(0, 0.05, 200))
        r, dd = ind.swing_stats(_pct([float(x) for x in close]))
        assert -100.0 < dd <= 0.0 and r >= 0.0

    for bad in (
        pd.Series([], dtype=float),
        pd.Series([0.0]),
        pd.Series([0.0, np.nan, 10.0]),
        pd.Series([0.0, -100.0]),
        _pct([100, 100, 100]),
    ):
        assert ind.swing_stats(bad) == (0.0, 0.0)


def test_swing_points() -> None:
    points = ind.swing_points(_pct([100, 110, 70, 90, 60]))
    assert points.max_runup == pytest.approx((90 / 70 - 1) * 100)
    assert (points.runup_start, points.runup_end) == (2, 3)
    assert points.max_drawdown == pytest.approx((60 / 110 - 1) * 100)
    assert (points.drawdown_start, points.drawdown_end) == (1, 4)

    up = ind.swing_points(_pct([100, 150, 200]))
    assert (up.drawdown_start, up.drawdown_end) == (-1, -1)
    assert (up.runup_start, up.runup_end) == (0, 2)
    assert ind.swing_points(pd.Series([0.0, np.nan, 10.0])) == ind.SwingPoints(0.0, -1, -1, 0.0, -1, -1)


def test_short_series_nan_and_none() -> None:
    close = pd.Series([10.0, 11.0, 12.0])
    assert np.isnan(ind.ma(close, 20).iloc[-1])
    assert np.isnan(ind.bbi(close).iloc[-1])
    assert np.isnan(ind.boll(close, 20)[0].iloc[-1])
    assert pt.calc_ma(close, 20) is None
    assert pt.calc_bbi(close) is None
    assert pt.calc_macd(close) == (None, None, None, False, False)
    assert pt.calc_boll(close, 20) == (None, None, None, None, None)


def test_volume_ratio_and_location_metrics() -> None:
    df = _series(80)
    rel = pt.calc_rel_volume(df, 20)
    pct = pt.calc_close_percentile(df["close"], 60)
    assert rel is not None and rel > 0
    assert pct is not None and 0.0 <= pct <= 1.0
    out = pt.compute_indicators(df)
    assert out["rel_volume"] == pytest.approx(rel)
    assert out["close_percentile"] == pytest.approx(pct)
    assert "bullish_close" in out

    df_zero = _series(30)
    df_zero.loc[df_zero.index[-6:-1], "volume"] = 0.0
    assert pt.calc_volume_ratio(df_zero) is None
