"""utils/render_text.py 核心单测 —— AI 文字输出（领域模型入参）。"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from kline_fixtures import make_klines

from SayuStock.utils import indicators as ind, render_text as rt
from SayuStock.utils.market.enums import BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.models import (
    Bar,
    Quote,
    BoardRow,
    SymbolRef,
    KlineSeries,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
)
from SayuStock.utils.market.convert.dataframe import kline_to_df

CHARTED_LABELS = (
    "MA5",
    "MA10",
    "MA20",
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
)


def _kline_series(name: str = "测试股份", n: int = 160, seed: int = 3) -> KlineSeries:
    bars: list[Bar] = []
    for line in make_klines(n, seed):
        parts = line.split(",")
        if len(parts) < 11:
            continue
        ts_s = parts[0]
        try:
            ts = datetime.strptime(ts_s[:10], "%Y-%m-%d")
            if len(ts_s) > 10:
                ts = datetime.strptime(ts_s[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        bars.append(
            Bar(
                ts=ts,
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]),
                amount=float(parts[6]),
                amplitude=float(parts[7]),
                change_pct=float(parts[8]),
                change_amount=float(parts[9]),
                turnover_rate=float(parts[10]),
            )
        )
    return KlineSeries(
        symbol=SymbolRef("600000", name, AssetClass.EQUITY, "SSE", "1.600000"),
        period=KlinePeriod.D1,
        bars=tuple(bars),
        adjusted=True,
    )


@pytest.fixture
def series() -> KlineSeries:
    return _kline_series()


def _grab(text: str, pattern: str) -> float:
    m = re.search(pattern, text)
    assert m, f"文字里找不到 {pattern}"
    return float(m.group(1))


def test_kline_text_contains_every_charted_indicator(series: KlineSeries) -> None:
    text = rt.kline_text(series, "single-stock-kline-101")
    for label in CHARTED_LABELS:
        assert label in text, f"缺少指标标签 {label}"


def test_kline_text_bars_table_and_period_names(series: KlineSeries) -> None:
    text = rt.kline_text(series, "single-stock-kline-101")
    assert "最近 10 根" in text
    assert len(text.split("最近 10 根:")[1].strip().splitlines()) == 11
    assert "日K" in text
    assert "周K" in rt.kline_text(series, "single-stock-kline-102")
    assert "60分钟" in rt.kline_text(series, "single-stock-kline-60")


def test_kline_text_numbers_match_indicators(series: KlineSeries) -> None:
    from_text = rt.kline_text(series, "single-stock-kline-101")
    expected = ind.compute_indicators(kline_to_df(series))
    assert _grab(from_text, r"MA20 ([\d.]+)") == pytest.approx(expected["ma20"], abs=0.005)
    assert _grab(from_text, r"BBI: ([\d.]+)") == pytest.approx(expected["bbi"], abs=0.005)
    assert _grab(from_text, r"K ([\-\d.]+)  D") == pytest.approx(expected["kdj_k"], abs=0.005)
    assert _grab(from_text, r"RSI6 ([\d.]+)") == pytest.approx(expected["rsi6"], abs=0.005)
    assert _grab(from_text, r"BAR ([\-\d.]+)") == pytest.approx(expected["macd_bar"], abs=0.005)


def test_kline_text_macd_bar_domestic_and_color(series: KlineSeries) -> None:
    text = rt.kline_text(series, "single-stock-kline-101")
    dif = _grab(text, r"DIF ([\-\d.]+)")
    dea = _grab(text, r"DEA ([\-\d.]+)")
    bar = _grab(text, r"BAR ([\-\d.]+)")
    assert bar == pytest.approx((dif - dea) * 2, abs=0.02)
    assert "红柱" in text or "绿柱" in text


def test_compare_text_swing_drawdown_and_normalization() -> None:
    text = rt.compare_text([_kline_series("甲", seed=1), _kline_series("乙", seed=2)])
    assert "甲" in text and "乙" in text and "归一化" in text
    assert "区间最大涨幅" in text and "区间最大回撤" in text
    assert "最高点" in text and "最低点" in text and "末点累计" in text
    for seed in range(6):
        m = re.search(r"区间最大回撤 (-[\d.]+)%", rt.compare_text([_kline_series("X", seed=seed)]))
        assert m and float(m.group(1)) > -100.0


def _cloud(n: int) -> BoardSnapshot:
    rows = tuple(BoardRow(f"{i:06d}", f"股{i}", 10.0, float(10 - i), 1e8, 1e10, "板块", None, None) for i in range(n))
    return BoardSnapshot(kind=BoardKind.HOTMAP, title="大盘云图", rows=rows)


def test_cloudmap_text_small_large_and_stats() -> None:
    small = rt.cloudmap_text(_cloud(5), "大盘云图", top_n=10)
    assert "领跌" not in small and "全部" in small
    assert all(small.count(f"股{i}(") == 1 for i in range(5))

    large = rt.cloudmap_text(_cloud(40), "大盘云图", top_n=10)
    assert "领涨 Top10" in large and "领跌 Top10" in large
    assert "上涨" in large and "下跌" in large and "平均涨跌幅" in large


def test_empty_and_short_series_text() -> None:
    empty = KlineSeries(SymbolRef("X", "X", AssetClass.EQUITY, "EM", "X"), KlinePeriod.D1, (), True)
    assert rt.kline_text(empty, "single-stock-kline-101") == ""
    assert rt.compare_text([empty]) == ""
    assert rt.cloudmap_text(BoardSnapshot(BoardKind.HOTMAP, "大盘云图", ()), "大盘云图") == ""
    short = rt.kline_text(_kline_series("X", n=3), "single-stock-kline-101")
    assert "N/A" in short and "MA60 N/A" in short


def test_single_stock_text_one_day() -> None:
    symbol = SymbolRef("600000", "某股", AssetClass.EQUITY, "SSE", "1.600000")
    quote = Quote(
        symbol,
        12.3,
        12.0,
        12.5,
        11.9,
        12.0,
        1.5,
        0.3,
        1e6,
        1e8,
        2.0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        datetime(2026, 7, 23, 15, 0),
    )
    series = IntradaySeries(
        symbol,
        (IntradayPoint(datetime(2026, 7, 23, 9, 30), 12.3, 12.0, 12.5, 11.9, 100, 1230, 12.3),),
        quote,
    )
    text = rt.single_stock_text(series)
    assert "某股" in text and "12.3" in text and "分时行情" in text


def test_single_stock_text_five_day() -> None:
    symbol = SymbolRef("000001", "上证指数", AssetClass.INDEX, "SSE", "1.000001")
    quote = Quote(
        symbol,
        3979.65,
        3979.0,
        4000.0,
        3960.0,
        3986.3,
        -0.17,
        None,
        1.0,
        1.0,
        1.0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        datetime(2026, 9, 1, 15, 0),
    )
    points = (
        IntradayPoint(datetime(2026, 8, 26, 15, 0), 3881.74, 3881.74, 3881.74, 3881.74, 1, 1, 3881.74),
        IntradayPoint(datetime(2026, 9, 1, 15, 0), 3979.65, 3979.65, 3979.65, 3979.65, 1, 1, 3979.65),
    )
    text = rt.single_stock_text(IntradaySeries(symbol, points, quote, ndays=5), ndays=5)
    assert "5日分时" in text and "分日收盘" in text and "08-26" in text and "09-01" in text
    assert "五日累计" in text and "今日涨跌幅" in text and "开盘价" not in text
