"""共享指标模块 SayuStock/utils/indicators.py 单测。

这一层是全插件指标的唯一真相源（图表和 AI 决策都读它），所以这里重点测三件事：

1. **口径**：MACD 柱是 2 倍、BOLL 基准是收盘价、RSI 是 Wilder ——
   历史上图表走 mplchart（西方口径）、AI 走手写实现，两边对不上；
2. **一致性**：series 层末值 == papertrade 标量层返回值。
   指标曾有两份复制实现，这条能在改一处忘另一处时立刻炸；
3. **区间涨跌/回撤**：归一化序列直接相减会算出 >100% 的回撤（已修）。
"""

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
        if sub_spec is None:  # utils 是 namespace package，没有 __init__.py
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


# ============================================================
# 夹具
# ============================================================
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


# ============================================================
# 口径：MACD 柱 = (DIF - DEA) × 2
# ============================================================
def test_macd_bar_is_double_the_gap(df: pd.DataFrame) -> None:
    """通达信/东财 BAR = (DIF-DEA)*2。西方口径（mplchart）不乘 2，柱子只有一半高。"""
    dif, dea, bar = ind.macd(df["close"])
    expected = (dif - dea) * 2.0
    pd.testing.assert_series_equal(bar, expected, check_names=False)


def test_macd_dif_is_ema12_minus_ema26(df: pd.DataFrame) -> None:
    close = df["close"]
    dif, dea, _ = ind.macd(close)
    expected_dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    pd.testing.assert_series_equal(dif, expected_dif, check_names=False)
    pd.testing.assert_series_equal(dea, dif.ewm(span=9, adjust=False).mean(), check_names=False)


# ============================================================
# 口径：BOLL 基准价是收盘价，不是典型价
# ============================================================
def test_boll_basis_is_close_not_typical_price(df: pd.DataFrame) -> None:
    """东财/通达信 BOLL = MA(CLOSE,N) ± k*STD(CLOSE,N)。

    mplchart 用典型价 (H+L+C)/3 做基准，画出来和券商软件不是同一条线。
    """
    close = df["close"]
    mid, upper, lower = ind.boll(close, 20, 2.0)
    pd.testing.assert_series_equal(mid, close.rolling(20).mean(), check_names=False)

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    # 中轨必须贴收盘均线、而不是典型价均线
    assert not np.allclose(
        mid.dropna().to_numpy(),
        typical.rolling(20).mean().dropna().to_numpy(),
    )

    std = close.rolling(20).std(ddof=0)
    pd.testing.assert_series_equal(upper, mid + 2.0 * std, check_names=False)
    pd.testing.assert_series_equal(lower, mid - 2.0 * std, check_names=False)


# ============================================================
# 口径：RSI Wilder / KDJ 递归 / BBI
# ============================================================
def test_rsi_is_wilder_smoothing() -> None:
    """Wilder 用 adjust=False 的递归平滑；mplchart 的 adjust=True 在短序列上有偏差。"""
    close = pd.Series([44.0, 44.3, 44.1, 44.2, 44.5, 43.4, 44.3, 44.8, 45.0, 45.9, 46.0, 45.6])
    got = ind.rsi(close, 6)

    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / 6, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / 6, adjust=False).mean()
    expected = 100.0 - 100.0 / (1.0 + gain / loss)
    assert got.iloc[-1] == pytest.approx(expected.iloc[-1])


def test_rsi_all_up_is_100_not_nan() -> None:
    """全涨段 avg_loss=0，必须记 100 而不是 0/0 的 NaN 断点。"""
    close = pd.Series(np.arange(1, 30, dtype=float))
    assert ind.rsi(close, 6).iloc[-1] == 100.0


def test_rsi_all_down_is_zero() -> None:
    close = pd.Series(np.arange(30, 1, -1, dtype=float))
    assert ind.rsi(close, 6).iloc[-1] == pytest.approx(0.0)


def test_kdj_recursion_matches_manual() -> None:
    """K = (RSV + 2K')/3，D = (K + 2D')/3，初值 50；J = 3K - 2D。"""
    n = 20
    rng = np.random.default_rng(3)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + 1.0
    low = close - 1.0

    k, d, j = ind.kdj(high, low, close, 9, 3, 3)

    low_min = low.rolling(9, min_periods=1).min()
    high_max = high.rolling(9, min_periods=1).max()
    rsv = (close - low_min) / (high_max - low_min) * 100.0
    k_prev = d_prev = 50.0
    for i in range(n):
        k_prev = (rsv.iloc[i] + 2 * k_prev) / 3
        d_prev = (k_prev + 2 * d_prev) / 3
    assert k.iloc[-1] == pytest.approx(k_prev)
    assert d.iloc[-1] == pytest.approx(d_prev)
    assert j.iloc[-1] == pytest.approx(3 * k_prev - 2 * d_prev)


def test_kdj_flat_bar_uses_neutral_rsv() -> None:
    """一字板（H==L）时 RSV 记中性 50，不能产生 NaN 断点。"""
    close = pd.Series([10.0] * 15)
    k, d, j = ind.kdj(close.copy(), close.copy(), close)
    assert k.notna().all() and d.notna().all() and j.notna().all()
    assert k.iloc[-1] == pytest.approx(50.0)


def test_bbi_is_mean_of_four_mas(df: pd.DataFrame) -> None:
    close = df["close"]
    expected = (
        close.rolling(3).mean() + close.rolling(6).mean() + close.rolling(12).mean() + close.rolling(24).mean()
    ) / 4.0
    pd.testing.assert_series_equal(ind.bbi(close), expected, check_names=False)


# ============================================================
# 一致性：图表读的 series 末值 == AI 读的标量
# ============================================================
def test_series_last_equals_papertrade_scalar(df: pd.DataFrame) -> None:
    """指标曾有两份实现（图表一份、AI 一份）。这条锁死两边同源。"""
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
    assert dif.iloc[-1] == pytest.approx(p_dif)
    assert dea.iloc[-1] == pytest.approx(p_dea)
    assert bar.iloc[-1] == pytest.approx(p_bar)

    k, d, j = ind.kdj(high, low, close)
    p_k, p_d, p_j, _, _ = pt.calc_kdj(df)
    assert k.iloc[-1] == pytest.approx(p_k)
    assert d.iloc[-1] == pytest.approx(p_d)
    assert j.iloc[-1] == pytest.approx(p_j)

    mid, upper, lower = ind.boll(close, 20, 2.0)
    p_mid, p_up, p_low, _, _ = pt.calc_boll(close, 20, 2.0)
    assert mid.iloc[-1] == pytest.approx(p_mid)
    assert upper.iloc[-1] == pytest.approx(p_up)
    assert lower.iloc[-1] == pytest.approx(p_low)

    support, resistance = ind.support_resistance(high, low, 20)
    p_sup, p_res = pt.calc_support_resistance(df, 20)
    assert support.iloc[-1] == pytest.approx(p_sup)
    assert resistance.iloc[-1] == pytest.approx(p_res)


def test_compute_indicators_matches_series_layer(df: pd.DataFrame) -> None:
    """AI 拿到的 dict 必须和图表画的是同一批数。"""
    out = pt.compute_indicators(df)
    close, high, low = df["close"], df["high"], df["low"]
    assert out["kdj_k"] == pytest.approx(ind.kdj(high, low, close)[0].iloc[-1])
    assert out["macd_bar"] == pytest.approx(ind.macd(close)[2].iloc[-1])
    assert out["bbi"] == pytest.approx(ind.bbi(close).iloc[-1])
    assert out["boll20_upper"] == pytest.approx(ind.boll(close, 20, 2.0)[1].iloc[-1])
    assert out["rsi6"] == pytest.approx(ind.rsi(close, 6).iloc[-1])


# ============================================================
# 叉信号
# ============================================================
def test_cross_signals_detects_golden_and_death() -> None:
    fast = pd.Series([1.0, 1.0, 1.0, 0.0, 2.0])  # 末根上穿
    slow = pd.Series([1.5, 1.5, 1.5, 1.0, 1.0])
    golden, death = ind.cross_signals(fast, slow, days=3)
    assert golden and not death

    golden, death = ind.cross_signals(slow, fast, days=3)
    assert death and not golden


def test_cross_signals_ignores_old_cross() -> None:
    """只看最近 N 根，更早的叉不算。"""
    fast = pd.Series([0.0, 2.0, 2.0, 2.0, 2.0, 2.0])  # 叉发生在第 1 根
    slow = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    golden, _ = ind.cross_signals(fast, slow, days=3)
    assert not golden


# ============================================================
# 归一化
# ============================================================
def test_normalize_pct_starts_at_zero(df: pd.DataFrame) -> None:
    out = ind.normalize_pct(df["close"])
    assert out.iloc[0] == pytest.approx(0.0)
    assert out.iloc[-1] == pytest.approx(df["close"].iloc[-1] / df["close"].iloc[0] - 1)


def test_normalize_pct_zero_base_is_all_nan() -> None:
    assert ind.normalize_pct(pd.Series([0.0, 1.0, 2.0])).isna().all()


def test_normalize_pct_empty() -> None:
    assert ind.normalize_pct(pd.Series([], dtype=float)).empty


# ============================================================
# 区间最大涨幅 / 最大回撤（曾算出 >100% 的回撤）
# ============================================================
def _pct(closes: list[float]) -> pd.Series:
    c = np.asarray(closes, dtype=float)
    return pd.Series((c / c[0] - 1.0) * 100.0)


def test_swing_stats_peak_then_halve() -> None:
    """100→250→125：回撤应为 -50%（125/250-1），而不是百分点相减的 -125%。"""
    runup, drawdown = ind.swing_stats(_pct([100, 250, 125]))
    assert runup == pytest.approx(150.0)
    assert drawdown == pytest.approx(-50.0)


def test_swing_stats_uses_trough_as_gain_denominator() -> None:
    """100→85→130：涨幅是 85→130 的 +52.94%，不是相对首日的 +30%。"""
    runup, drawdown = ind.swing_stats(_pct([100, 85, 130]))
    assert runup == pytest.approx((130 / 85 - 1) * 100)
    assert drawdown == pytest.approx(-15.0)


def test_swing_stats_monotonic_paths() -> None:
    runup, drawdown = ind.swing_stats(_pct([100, 150, 200]))
    assert runup == pytest.approx(100.0)
    assert drawdown == pytest.approx(0.0)  # 单调上涨无回撤

    runup, drawdown = ind.swing_stats(_pct([100, 70, 40]))
    assert runup == pytest.approx(0.0)  # 单调下跌无涨幅
    assert drawdown == pytest.approx(-60.0)


def test_swing_stats_drawdown_respects_peak_before_trough() -> None:
    """V 型后创新高：回撤取 100→60 的 -40%，不能跨过时间顺序拿 200 和 60 配对。"""
    runup, drawdown = ind.swing_stats(_pct([100, 80, 100, 60, 200]))
    assert drawdown == pytest.approx(-40.0)
    assert runup == pytest.approx(200 / 60 * 100 - 100, abs=1e-3)


@pytest.mark.parametrize("seed", range(30))
def test_swing_stats_drawdown_never_exceeds_100pct(seed: int) -> None:
    """性质测试：只要价格恒正，回撤永远在 (-100%, 0]，涨幅永远 >= 0。

    这正是原实现（两点相减）违反的不变量。
    """
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.05, 200))
    runup, drawdown = ind.swing_stats(_pct([float(x) for x in close]))
    assert -100.0 < drawdown <= 0.0
    assert runup >= 0.0


def test_swing_stats_degenerate_inputs() -> None:
    assert ind.swing_stats(pd.Series([], dtype=float)) == (0.0, 0.0)
    assert ind.swing_stats(pd.Series([0.0])) == (0.0, 0.0)
    assert ind.swing_stats(pd.Series([0.0, np.nan, 10.0])) == (0.0, 0.0)
    assert ind.swing_stats(pd.Series([0.0, -100.0])) == (0.0, 0.0)  # 归零：level<=0


def test_swing_stats_flat_series() -> None:
    assert ind.swing_stats(_pct([100, 100, 100])) == (0.0, 0.0)


def test_swing_points_positions() -> None:
    """点位必须是波段自己的谷→峰/峰→谷，而不是全局最高/最低点。

    100→110→70→90→60：全局最高在 1、全局最低在 4；
    最大涨幅是 70→90（2→3），最大回撤是 110→60（1→4）。
    曾把「区间最大涨幅」标注挂在全局最高点上（此例是位置 1），完全错位。
    """
    points = ind.swing_points(_pct([100, 110, 70, 90, 60]))
    assert points.max_runup == pytest.approx((90 / 70 - 1) * 100)
    assert (points.runup_start, points.runup_end) == (2, 3)
    assert points.max_drawdown == pytest.approx((60 / 110 - 1) * 100)
    assert (points.drawdown_start, points.drawdown_end) == (1, 4)


def test_swing_points_monotonic_marks_missing_side() -> None:
    """单边行情：没有反弹/回撤的一侧幅度为 0，位置记 -1。"""
    up = ind.swing_points(_pct([100, 150, 200]))
    assert (up.drawdown_start, up.drawdown_end) == (-1, -1)
    assert (up.runup_start, up.runup_end) == (0, 2)

    down = ind.swing_points(_pct([100, 70, 40]))
    assert (down.runup_start, down.runup_end) == (-1, -1)
    assert (down.drawdown_start, down.drawdown_end) == (0, 2)


def test_swing_points_degenerate_inputs() -> None:
    points = ind.swing_points(pd.Series([0.0, np.nan, 10.0]))
    assert points == ind.SwingPoints(0.0, -1, -1, 0.0, -1, -1)


# ============================================================
# 边界
# ============================================================
def test_indicators_on_short_series_give_nan_not_crash() -> None:
    close = pd.Series([10.0, 11.0, 12.0])
    assert np.isnan(ind.ma(close, 20).iloc[-1])
    assert np.isnan(ind.bbi(close).iloc[-1])
    assert np.isnan(ind.boll(close, 20)[0].iloc[-1])


def test_papertrade_scalars_return_none_on_short_series() -> None:
    """series 层用 NaN 表示数据不足，标量层的契约是 None。"""
    close = pd.Series([10.0, 11.0, 12.0])
    assert pt.calc_ma(close, 20) is None
    assert pt.calc_bbi(close) is None
    assert pt.calc_macd(close) == (None, None, None, False, False)
    assert pt.calc_boll(close, 20) == (None, None, None, None, None)


def test_volume_ratio_zero_avg_volume_is_none() -> None:
    df = _series(30)
    df.loc[df.index[-6:-1], "volume"] = 0.0
    assert pt.calc_volume_ratio(df) is None
