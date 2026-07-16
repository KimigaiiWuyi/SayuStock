"""utils/render_text.py 单测 —— AI 文字输出。

部分模型看不到图，``ai_return`` 的文字是它拿到的**全部**信息。所以这里锁两件事：

1. **完整性**：图上画的每条线（MA/BOLL/BBI/KDJ/RSI/MACD/CMF/量比/支撑压力）
   都必须出现在文字里 —— 历史上这层只吐 OHLC，指标一个都没给；
2. **一致性**：文字里的数必须等于 utils/indicators.py 算出来的数（图表画的同一份）。
"""

import re
import sys
import importlib.util
from types import ModuleType
from pathlib import Path

import pytest
from kline_fixtures import make_klines

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_render_text_test"


def _ensure_pkg() -> None:
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME, PKG_ROOT / "__init__.py", submodule_search_locations=[str(PKG_ROOT)]
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
    utils_mod = ModuleType(f"{PKG_NAME}.utils")
    utils_mod.__path__ = [str(PKG_ROOT / "utils")]
    sys.modules[f"{PKG_NAME}.utils"] = utils_mod


def _load(dotted: str, rel_path: str) -> ModuleType:
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG_NAME}.{dotted}", PKG_ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ind = _load("utils.indicators", "utils/indicators.py")
_load("utils.kline", "utils/kline.py")
rt = _load("utils.render_text", "utils/render_text.py")


def _klines(n: int = 160, seed: int = 3) -> list[str]:
    return make_klines(n, seed)


@pytest.fixture
def raw() -> dict:
    return {"data": {"name": "测试股份", "code": "600000", "klines": _klines()}}


# ============================================================
# 完整性：图上有的，文字里必须有
# ============================================================
@pytest.mark.parametrize(
    "label",
    [
        "MA5",
        "MA10",
        "MA20",  # 图上有 SMA(20)，AI 的多头排列判断也基于它
        "MA60",
        "BBI",
        "BOLL(20,2)",
        "BOLL(60,3)",
        "KDJ(9,3,3)",
        "RSI6",
        "RSI12",
        "RSI24",
        "MACD(12,26,9)",
        "DIF",
        "DEA",
        "BAR",
        "CMF(20)",
        "换手率",
        "量比",
        "乖离率",
        "ATR%",
        "CCI(14)",
        "支撑",
        "压力",
        "区间最大涨幅",
        "区间最大回撤",
    ],
)
def test_kline_text_contains_every_charted_indicator(raw: dict, label: str) -> None:
    assert label in rt.kline_text(raw, "single-stock-kline-101")


def test_kline_text_has_recent_bars_table(raw: dict) -> None:
    text = rt.kline_text(raw, "single-stock-kline-101")
    assert "最近 10 根" in text
    # 表头之后应有 10 行明细
    body = text.split("最近 10 根:")[1].strip().splitlines()
    assert len(body) == 11  # 1 表头 + 10 根


def test_kline_text_period_name(raw: dict) -> None:
    assert "日K" in rt.kline_text(raw, "single-stock-kline-101")
    assert "周K" in rt.kline_text(raw, "single-stock-kline-102")
    assert "60分钟" in rt.kline_text(raw, "single-stock-kline-60")


# ============================================================
# 一致性：文字里的数 == indicators 算出来的数
# ============================================================
def test_kline_text_numbers_match_indicators(raw: dict) -> None:
    from_text = rt.kline_text(raw, "single-stock-kline-101")
    df = rt.klines_to_df(raw["data"]["klines"])
    expected = ind.compute_indicators(df)

    assert _grab(from_text, r"MA20 ([\d.]+)") == pytest.approx(expected["ma20"], abs=0.005)
    assert _grab(from_text, r"BBI: ([\d.]+)") == pytest.approx(expected["bbi"], abs=0.005)
    assert _grab(from_text, r"K ([\-\d.]+)  D") == pytest.approx(expected["kdj_k"], abs=0.005)
    assert _grab(from_text, r"RSI6 ([\d.]+)") == pytest.approx(expected["rsi6"], abs=0.005)
    assert _grab(from_text, r"BAR ([\-\d.]+)") == pytest.approx(expected["macd_bar"], abs=0.005)


def _grab(text: str, pattern: str) -> float:
    m = re.search(pattern, text)
    assert m, f"文字里找不到 {pattern}"
    return float(m.group(1))


def test_kline_text_macd_bar_uses_domestic_convention(raw: dict) -> None:
    """文字里的 BAR 必须是 (DIF-DEA)*2，和图上柱子同高。"""
    text = rt.kline_text(raw, "single-stock-kline-101")
    dif = _grab(text, r"DIF ([\-\d.]+)")
    dea = _grab(text, r"DEA ([\-\d.]+)")
    bar = _grab(text, r"BAR ([\-\d.]+)")
    assert bar == pytest.approx((dif - dea) * 2, abs=0.02)


def test_kline_text_marks_bar_color(raw: dict) -> None:
    text = rt.kline_text(raw, "single-stock-kline-101")
    assert "红柱" in text or "绿柱" in text


# ============================================================
# 对比图文字
# ============================================================
def test_compare_text_has_swing_and_extremes() -> None:
    raws = [
        {"data": {"name": "甲", "klines": _klines(seed=1)}},
        {"data": {"name": "乙", "klines": _klines(seed=2)}},
    ]
    text = rt.compare_text(raws)
    for name in ("甲", "乙"):
        assert name in text
    assert "区间最大涨幅" in text and "区间最大回撤" in text
    assert "最高点" in text and "最低点" in text
    assert "末点累计" in text


def test_compare_text_drawdown_never_exceeds_100pct() -> None:
    """归一化后两点相减会算出 >100% 的回撤 —— 文字里绝不能出现。"""
    for seed in range(8):
        raws = [{"data": {"name": "X", "klines": _klines(seed=seed)}}]
        text = rt.compare_text(raws)
        m = re.search(r"区间最大回撤 (-[\d.]+)%", text)
        assert m
        assert float(m.group(1)) > -100.0


def test_compare_text_explains_normalization() -> None:
    """看不到图的 AI 需要知道这些百分比是相对首日的累计涨跌幅。"""
    text = rt.compare_text([{"data": {"name": "甲", "klines": _klines()}}])
    assert "归一化" in text


# ============================================================
# 云图文字
# ============================================================
def _cloud(n: int) -> dict:
    return {"data": {"diff": [{"f3": 10 - i, "f14": f"股{i}", "f100": "板块"} for i in range(n)]}}


def test_cloudmap_text_small_list_has_no_overlap() -> None:
    """标的数少于 2×top_n 时切 Top/Bottom 会重叠，涨停股会混进「领跌」。"""
    text = rt.cloudmap_text(_cloud(5), "大盘云图", top_n=10)
    assert "领跌" not in text
    assert "全部" in text
    for i in range(5):
        assert text.count(f"股{i}(") == 1  # 每只只能出现一次


def test_cloudmap_text_large_list_splits_top_bottom() -> None:
    text = rt.cloudmap_text(_cloud(40), "大盘云图", top_n=10)
    assert "领涨 Top10" in text and "领跌 Top10" in text


def test_cloudmap_text_has_stats() -> None:
    text = rt.cloudmap_text(_cloud(40), "大盘云图")
    assert "上涨" in text and "下跌" in text and "平均涨跌幅" in text


# ============================================================
# 边界
# ============================================================
def test_empty_klines_returns_empty_string() -> None:
    assert rt.kline_text({"data": {"name": "X", "klines": []}}, "single-stock-kline-101") == ""
    assert rt.compare_text([{"data": {"name": "X", "klines": []}}]) == ""
    assert rt.cloudmap_text({"data": {"diff": []}}, "大盘云图") == ""


def test_short_series_shows_na_not_zero() -> None:
    """数据不足时指标必须显示 N/A —— 显示 0 会让 AI 以为真是 0。"""
    text = rt.kline_text({"data": {"name": "X", "klines": _klines(n=3)}}, "single-stock-kline-101")
    assert "N/A" in text
    assert "MA60 N/A" in text


def test_single_stock_text() -> None:
    raw = {"data": {"f58": "某股", "f43": 12.3, "f170": 1.5, "f60": 12.0, "f44": 12.5, "f45": 11.9, "f168": 2.0}}
    text = rt.single_stock_text(raw)
    assert "某股" in text and "12.3" in text
