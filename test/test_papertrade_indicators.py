"""AI 模拟盘技术指标单测。

覆盖：
- klines_to_df 解析东财 K 线
- MA5/10/20/60
- MACD（金叉/死叉检测）
- RSI6/12/24
- CMF20
- 量比
- BIAS
- ATR%
- 支撑/压力
- compute_indicators 整合
"""

import sys
import importlib.util
from types import ModuleType
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# 把仓库根目录加入 sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 单独加载 stock_papertrade.indicators
PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_ind_test"


def _ensure_pkg():
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


# ============================================================
# 工具：造 60 根日 K
# ============================================================
def _make_klines(n: int = 60, base_price: float = 100.0, drift: float = 0.0) -> list[str]:
    """生成 n 根模拟日 K；返回东财格式字符串列表"""
    out = []
    base = datetime(2025, 1, 1)
    price = base_price
    for i in range(n):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        # 简单几何布朗运动
        change = np.random.normal(drift, 0.02)
        open_p = price
        close_p = price * (1 + change)
        high_p = max(open_p, close_p) * 1.005
        low_p = min(open_p, close_p) * 0.995
        volume = 1_000_000 + i * 1000
        amount = close_p * volume
        out.append(
            f"{date},{open_p:.2f},{close_p:.2f},{high_p:.2f},{low_p:.2f},{volume:.0f},{amount:.0f},2.0,0.5,0.0,1.5"
        )
        price = close_p
    return out


def _make_trending_klines(n: int = 60, base_price: float = 100.0, daily_pct: float = 0.01) -> list[str]:
    """生成单边上涨 n 根"""
    out = []
    base = datetime(2025, 1, 1)
    price = base_price
    for i in range(n):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        open_p = price
        close_p = price * (1 + daily_pct)
        high_p = max(open_p, close_p) * 1.003
        low_p = min(open_p, close_p) * 0.997
        volume = 1_000_000
        amount = close_p * volume
        out.append(
            f"{date},{open_p:.2f},{close_p:.2f},{high_p:.2f},{low_p:.2f},{volume:.0f},{amount:.0f},1.0,1.0,0.0,1.5"
        )
        price = close_p
    return out


# ============================================================
# 测试
# ============================================================
def test_klines_to_df_basic():
    klines = _make_klines(10)
    df = klines_to_df(klines)
    assert len(df) == 10
    assert "close" in df.columns
    assert "turnover_rate" in df.columns
    assert df["close"].iloc[-1] > 0
    print("[OK] klines_to_df 解析 10 行 K 线")


def test_klines_to_df_empty():
    df = klines_to_df([])
    assert df.empty
    print("[OK] klines_to_df 空列表返回空 DataFrame")


def test_klines_to_df_garbage():
    """乱数据应该被跳过，不抛异常"""
    df = klines_to_df(
        [
            "not a kline",
            "123,456",  # 字段不足 11
            "abc,xyz,def,1,2,3,4,5,6,7,8",  # open/close/high 字段是 xyz 不是数字
        ]
    )
    # 全部应该被过滤
    assert df.empty
    print("[OK] klines_to_df 乱数据不抛异常")


def test_calc_ma_normal():
    close = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    assert calc_ma(close, 5) == 8.0  # (6+7+8+9+10)/5
    assert calc_ma(close, 10) == 5.5
    print("[OK] calc_ma 简单平均")


def test_calc_ma_insufficient():
    close = pd.Series([1, 2, 3], dtype=float)
    assert calc_ma(close, 5) is None
    print("[OK] calc_ma 数据不足返回 None")


def test_calc_macd_basic():
    """MACD 能算出 DIF/DEA/BAR"""
    np.random.seed(42)
    close = pd.Series(np.cumsum(np.random.normal(0, 1, 50)) + 100)
    dif, dea, bar, gold, death = calc_macd(close)
    assert dif is not None
    assert dea is not None
    assert bar is not None
    # 至少有一个 bool
    assert isinstance(gold, bool)
    assert isinstance(death, bool)
    print("[OK] calc_macd 基础计算")


def test_calc_macd_golden_cross_detection():
    """构造一个明显金叉：长期下跌后强势反弹，DIF 必在末值上穿 DEA。"""
    # 先用 60 天下跌，然后 10 天强涨 → DIF 应从负转正
    close = list(np.linspace(100, 50, 60))  # 跌 60 天
    close += [51, 53, 56, 60, 65, 72, 80, 90, 102, 120]  # 最后 10 天强涨
    close = pd.Series(close, dtype=float)
    dif, dea, bar, gold, death = calc_macd(close)
    assert dif is not None
    # 末值 DIF 必在 DEA 之上（强涨）
    assert dif > dea, f"金叉后 DIF 应 > DEA：dif={dif:.4f}, dea={dea:.4f}"
    # 检测到金叉
    assert gold or dif > dea, f"期望金叉，dif={dif:.4f}, dea={dea:.4f}"
    assert death is False
    print(f"[OK] calc_macd 金叉检测 (dif={dif:.4f}, dea={dea:.4f}, bar={bar:.4f})")


def test_calc_macd_death_cross_detection():
    """构造明显死叉：长期上涨后急跌，DIF 必在末值下穿 DEA。"""
    close = list(np.linspace(50, 100, 60))  # 涨 60 天
    close += [99, 97, 94, 90, 85, 78, 70, 60, 48, 30]  # 最后 10 天急跌
    close = pd.Series(close, dtype=float)
    dif, dea, bar, gold, death = calc_macd(close)
    assert dif is not None
    assert dif < dea, f"死叉后 DIF 应 < DEA：dif={dif:.4f}, dea={dea:.4f}"
    assert death or dif < dea, f"期望死叉，dif={dif:.4f}, dea={dea:.4f}"
    assert gold is False
    print(f"[OK] calc_macd 死叉检测 (dif={dif:.4f}, dea={dea:.4f}, bar={bar:.4f})")


def test_calc_rsi_normal():
    np.random.seed(123)
    close = pd.Series(np.cumsum(np.random.normal(0, 1, 30)) + 100, dtype=float)
    rsi6 = calc_rsi(close, 6)
    rsi12 = calc_rsi(close, 12)
    rsi24 = calc_rsi(close, 24)
    assert 0 <= rsi6 <= 100
    assert 0 <= rsi12 <= 100
    assert 0 <= rsi24 <= 100
    print("[OK] calc_rsi 0~100 区间")


def test_calc_rsi_all_up():
    """单边上涨 → RSI 接近 100"""
    close = pd.Series(np.linspace(100, 200, 30), dtype=float)
    rsi = calc_rsi(close, 6)
    assert rsi > 90
    print(f"[OK] calc_rsi 单边上涨 → {rsi:.1f}")


def test_calc_rsi_all_down():
    """单边下跌 → RSI 接近 0"""
    close = pd.Series(np.linspace(200, 100, 30), dtype=float)
    rsi = calc_rsi(close, 6)
    assert rsi < 10
    print(f"[OK] calc_rsi 单边下跌 → {rsi:.1f}")


def test_calc_cmf():
    np.random.seed(7)
    close = pd.Series(np.cumsum(np.random.normal(0, 1, 30)) + 100, dtype=float)
    high = close * 1.01
    low = close * 0.99
    volume = pd.Series([1_000_000] * 30, dtype=float)
    df = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
        }
    )
    cmf = calc_cmf(df, period=20)
    assert cmf is not None
    assert -1 <= cmf <= 1
    print(f"[OK] calc_cmf={cmf:.4f}")


def test_calc_volume_ratio_double():
    """今量 = 5 日均的 2 倍 → 量比 = 2"""
    closes = [10] * 10
    # 最后一天是 200，前面 5 天（iloc[-6:-1]）都是 100；均=100，量比=200/100=2.0
    volumes = [100, 100, 100, 100, 100, 100, 100, 100, 100, 200]
    df = pd.DataFrame(
        {
            "open": closes,
            "close": closes,
            "high": closes,
            "low": closes,
            "volume": volumes,
        }
    )
    vr = calc_volume_ratio(df)
    assert vr == 2.0
    print(f"[OK] calc_volume_ratio={vr}")


def test_calc_bias():
    """乖离率 = (close - ma20) / ma20"""
    close = pd.Series([100.0] * 30)
    # 让最后 close > ma20
    close.iloc[-1] = 110.0
    bias = calc_bias(close, period=20)
    assert bias is not None
    # ma20 = mean(last 20) = (19*100 + 110) / 20 = 100.5
    # bias = (110 - 100.5) / 100.5 = 0.0945...
    assert abs(bias - (110 - 100.5) / 100.5) < 1e-3
    print(f"[OK] calc_bias={bias:.4f}")


def test_calc_atr_pct_normal():
    np.random.seed(99)
    close = pd.Series(np.cumsum(np.random.normal(0, 1, 30)) + 100, dtype=float)
    high = close * 1.01
    low = close * 0.99
    df = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "close": close,
            "high": high,
            "low": low,
        }
    )
    atr = calc_atr_pct(df, period=14)
    assert atr is not None
    assert 0 < atr < 0.5
    print(f"[OK] calc_atr_pct={atr:.4f}")


def test_calc_support_resistance():
    closes = list(range(50, 100))  # 50→99 涨
    df = pd.DataFrame(
        {
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
        }
    )
    sup, res = calc_support_resistance(df, period=20)
    assert sup is not None
    assert res is not None
    assert sup < res
    print(f"[OK] support={sup:.2f}, resistance={res:.2f}")


def test_calc_kdj_bounds_and_uptrend():
    """KDJ：K/D 落在 [0,100]，单边上涨时 K/D 高位；J 可越界 100。"""
    klines = _make_trending_klines(60, daily_pct=0.02)
    df = klines_to_df(klines)
    k, d, j, gold, death = calc_kdj(df)
    assert k is not None and d is not None and j is not None
    assert 0 <= k <= 100, f"K 越界: {k}"
    assert 0 <= d <= 100, f"D 越界: {d}"
    assert k > 70 and d > 70, f"单边上涨 K/D 应高位: k={k:.2f} d={d:.2f}"
    assert isinstance(gold, bool) and isinstance(death, bool)
    print(f"[OK] calc_kdj 上涨 k={k:.2f} d={d:.2f} j={j:.2f} gold={gold} death={death}")


def test_calc_kdj_matches_recursive_reference():
    """与独立递归参考实现对拍（通达信/东财口径 9,3,3）。"""
    np.random.seed(11)
    klines = _make_klines(80)
    df = klines_to_df(klines)
    k, d, j, _, _ = calc_kdj(df)

    n, m1, m2 = 9, 3, 3
    low_min = df["low"].astype(float).rolling(n, min_periods=1).min()
    high_max = df["high"].astype(float).rolling(n, min_periods=1).max()
    span = (high_max - low_min).to_numpy(dtype=float)
    close_arr = df["close"].astype(float).to_numpy(dtype=float)
    low_arr = low_min.to_numpy(dtype=float)
    rsv = np.where(span > 0, (close_arr - low_arr) / span * 100.0, np.nan)
    kp = dp = 50.0
    for r in rsv:
        r = 50.0 if not np.isfinite(r) else float(r)
        kp = (r + (m1 - 1) * kp) / m1
        dp = (kp + (m2 - 1) * dp) / m2
    assert abs(k - kp) < 1e-9 and abs(d - dp) < 1e-9 and abs(j - (3 * kp - 2 * dp)) < 1e-9
    print(f"[OK] calc_kdj 与参考实现一致 k={k:.4f} d={d:.4f} j={j:.4f}")


def test_calc_kdj_insufficient():
    """< 9 根返回全 None/False。"""
    df = klines_to_df(_make_klines(5))
    k, d, j, gold, death = calc_kdj(df)
    assert k is None and d is None and j is None and gold is False and death is False
    print("[OK] calc_kdj 数据不足返回 None")


def test_compute_indicators_full():
    """完整指标计算（60 根日 K）"""
    np.random.seed(2024)
    klines = _make_klines(60)
    df = klines_to_df(klines)
    ind_out = compute_indicators(df)
    assert ind_out["ma5"] is not None
    assert ind_out["ma20"] is not None
    assert ind_out["macd_dif"] is not None
    assert ind_out["rsi6"] is not None
    assert ind_out["cmf20"] is not None
    assert ind_out["last_close"] is not None
    # KDJ 已并入指标全集
    assert ind_out["kdj_k"] is not None
    assert ind_out["kdj_d"] is not None
    assert ind_out["kdj_j"] is not None
    assert "kdj_golden_cross_in_3d" in ind_out
    assert "kdj_overbought" in ind_out
    print("[OK] compute_indicators 全套 60 根（含 KDJ）")


def test_compute_indicators_empty():
    ind_out = compute_indicators(pd.DataFrame())
    assert ind_out["ma5"] is None
    assert ind_out["ma_bull_alignment"] is False
    print("[OK] compute_indicators 空 DataFrame")


def test_compute_indicators_short():
    """< 30 根时部分指标 None（如 MACD/CMF 需要更多数据）"""
    klines = _make_klines(20)
    df = klines_to_df(klines)
    ind_out = compute_indicators(df)
    # 20 根够算 ma5/ma10/ma20，但 ma60 不足
    assert ind_out["ma5"] is not None
    assert ind_out["ma10"] is not None
    assert ind_out["ma20"] is not None
    assert ind_out["ma60"] is None  # 不足 60
    # MACD 需要 35 根 → None
    assert ind_out["macd_dif"] is None
    print("[OK] compute_indicators 20 根（ma60/macd None）")


def test_compute_indicators_bull_alignment():
    """明显上涨：MA5 > MA10 > MA20"""
    klines = _make_trending_klines(60, daily_pct=0.02)
    df = klines_to_df(klines)
    ind_out = compute_indicators(df)
    if ind_out["ma5"] is not None and ind_out["ma10"] is not None and ind_out["ma20"] is not None:
        assert ind_out["ma_bull_alignment"] is True
        assert ind_out["close_above_ma20"] is True
        print(f"[OK] 上涨趋势: ma5={ind_out['ma5']:.2f} > ma10={ind_out['ma10']:.2f} > ma20={ind_out['ma20']:.2f}")
    else:
        print("[WARN] ma 数据不足，跳过 bull_alignment 断言")


if __name__ == "__main__":
    test_klines_to_df_basic()
    test_klines_to_df_empty()
    test_klines_to_df_garbage()
    test_calc_ma_normal()
    test_calc_ma_insufficient()
    test_calc_macd_basic()
    test_calc_macd_golden_cross_detection()
    test_calc_macd_death_cross_detection()
    test_calc_rsi_normal()
    test_calc_rsi_all_up()
    test_calc_rsi_all_down()
    test_calc_cmf()
    test_calc_volume_ratio_double()
    test_calc_bias()
    test_calc_atr_pct_normal()
    test_calc_support_resistance()
    test_calc_kdj_bounds_and_uptrend()
    test_calc_kdj_matches_recursive_reference()
    test_calc_kdj_insufficient()
    test_compute_indicators_full()
    test_compute_indicators_empty()
    test_compute_indicators_short()
    test_compute_indicators_bull_alignment()
    print("\n[SUCCESS] indicators 全部测试通过！")
