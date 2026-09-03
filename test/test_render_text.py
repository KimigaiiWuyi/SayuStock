"""utils/render_text.py 单测 —— AI 文字输出（领域模型入参）。

路径与 ``SayuStock`` 包壳由 ``test/conftest.py`` 处理（避免触发 Plugins 注册）。
"""

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


def _klines(n: int = 160, seed: int = 3) -> list[str]:
    return make_klines(n, seed)


def _kline_series(name: str = "测试股份", n: int = 160, seed: int = 3) -> KlineSeries:
    bars: list[Bar] = []
    for line in _klines(n, seed):
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
        symbol=SymbolRef(
            code="600000",
            name=name,
            asset_class=AssetClass.EQUITY,
            exchange="SSE",
            provider_symbol="1.600000",
        ),
        period=KlinePeriod.D1,
        bars=tuple(bars),
        adjusted=True,
    )


@pytest.fixture
def series() -> KlineSeries:
    return _kline_series()


@pytest.mark.parametrize(
    "label",
    [
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
    ],
)
def test_kline_text_contains_every_charted_indicator(series: KlineSeries, label: str) -> None:
    assert label in rt.kline_text(series, "single-stock-kline-101")


def test_kline_text_has_recent_bars_table(series: KlineSeries) -> None:
    text = rt.kline_text(series, "single-stock-kline-101")
    assert "最近 10 根" in text
    body = text.split("最近 10 根:")[1].strip().splitlines()
    assert len(body) == 11


def test_kline_text_period_name(series: KlineSeries) -> None:
    assert "日K" in rt.kline_text(series, "single-stock-kline-101")
    assert "周K" in rt.kline_text(series, "single-stock-kline-102")
    assert "60分钟" in rt.kline_text(series, "single-stock-kline-60")


def test_kline_text_numbers_match_indicators(series: KlineSeries) -> None:
    from_text = rt.kline_text(series, "single-stock-kline-101")
    df = kline_to_df(series)
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


def test_kline_text_macd_bar_uses_domestic_convention(series: KlineSeries) -> None:
    text = rt.kline_text(series, "single-stock-kline-101")
    dif = _grab(text, r"DIF ([\-\d.]+)")
    dea = _grab(text, r"DEA ([\-\d.]+)")
    bar = _grab(text, r"BAR ([\-\d.]+)")
    assert bar == pytest.approx((dif - dea) * 2, abs=0.02)


def test_kline_text_marks_bar_color(series: KlineSeries) -> None:
    text = rt.kline_text(series, "single-stock-kline-101")
    assert "红柱" in text or "绿柱" in text


def test_compare_text_has_swing_and_extremes() -> None:
    series_list = [_kline_series("甲", seed=1), _kline_series("乙", seed=2)]
    text = rt.compare_text(series_list)
    for name in ("甲", "乙"):
        assert name in text
    assert "区间最大涨幅" in text and "区间最大回撤" in text
    assert "最高点" in text and "最低点" in text
    assert "末点累计" in text


def test_compare_text_drawdown_never_exceeds_100pct() -> None:
    for seed in range(8):
        text = rt.compare_text([_kline_series("X", seed=seed)])
        m = re.search(r"区间最大回撤 (-[\d.]+)%", text)
        assert m
        assert float(m.group(1)) > -100.0


def test_compare_text_explains_normalization() -> None:
    text = rt.compare_text([_kline_series("甲")])
    assert "归一化" in text


def _cloud(n: int) -> BoardSnapshot:
    rows = tuple(
        BoardRow(
            code=f"{i:06d}",
            name=f"股{i}",
            price=10.0,
            change_pct=float(10 - i),
            amount=1e8,
            market_cap=1e10,
            industry="板块",
            lead_name=None,
            lead_change_pct=None,
        )
        for i in range(n)
    )
    return BoardSnapshot(kind=BoardKind.HOTMAP, title="大盘云图", rows=rows)


def test_cloudmap_text_small_list_has_no_overlap() -> None:
    text = rt.cloudmap_text(_cloud(5), "大盘云图", top_n=10)
    assert "领跌" not in text
    assert "全部" in text
    for i in range(5):
        assert text.count(f"股{i}(") == 1


def test_cloudmap_text_large_list_splits_top_bottom() -> None:
    text = rt.cloudmap_text(_cloud(40), "大盘云图", top_n=10)
    assert "领涨 Top10" in text and "领跌 Top10" in text


def test_cloudmap_text_has_stats() -> None:
    text = rt.cloudmap_text(_cloud(40), "大盘云图")
    assert "上涨" in text and "下跌" in text and "平均涨跌幅" in text


def test_empty_klines_returns_empty_string() -> None:
    empty = KlineSeries(
        symbol=SymbolRef("X", "X", AssetClass.EQUITY, "EM", "X"),
        period=KlinePeriod.D1,
        bars=(),
        adjusted=True,
    )
    assert rt.kline_text(empty, "single-stock-kline-101") == ""
    assert rt.compare_text([empty]) == ""
    assert rt.cloudmap_text(BoardSnapshot(BoardKind.HOTMAP, "大盘云图", ()), "大盘云图") == ""


def test_short_series_shows_na_not_zero() -> None:
    text = rt.kline_text(_kline_series("X", n=3), "single-stock-kline-101")
    assert "N/A" in text
    assert "MA60 N/A" in text


def test_single_stock_text() -> None:
    symbol = SymbolRef("600000", "某股", AssetClass.EQUITY, "SSE", "1.600000")
    quote = Quote(
        symbol=symbol,
        price=12.3,
        open=12.0,
        high=12.5,
        low=11.9,
        prev_close=12.0,
        change_pct=1.5,
        change_amount=0.3,
        volume=1e6,
        amount=1e8,
        turnover_rate=2.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=datetime(2026, 7, 23, 15, 0),
    )
    series = IntradaySeries(
        symbol=symbol,
        points=(
            IntradayPoint(
                ts=datetime(2026, 7, 23, 9, 30),
                price=12.3,
                open=12.0,
                high=12.5,
                low=11.9,
                volume=100,
                amount=1230,
                avg_price=12.3,
            ),
        ),
        quote=quote,
    )
    text = rt.single_stock_text(series)
    assert "某股" in text and "12.3" in text
    assert "分时行情" in text


def test_single_stock_five_day_text() -> None:
    symbol = SymbolRef("000001", "上证指数", AssetClass.INDEX, "SSE", "1.000001")
    quote = Quote(
        symbol=symbol,
        price=3979.65,
        open=3979.0,
        high=4000.0,
        low=3960.0,
        prev_close=3986.3,
        change_pct=-0.17,
        change_amount=None,
        volume=1.0,
        amount=1.0,
        turnover_rate=1.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=datetime(2026, 9, 1, 15, 0),
    )
    points = (
        IntradayPoint(
            ts=datetime(2026, 8, 26, 15, 0),
            price=3881.74,
            open=3881.74,
            high=3881.74,
            low=3881.74,
            volume=1,
            amount=1,
            avg_price=3881.74,
        ),
        IntradayPoint(
            ts=datetime(2026, 9, 1, 15, 0),
            price=3979.65,
            open=3979.65,
            high=3979.65,
            low=3979.65,
            volume=1,
            amount=1,
            avg_price=3979.65,
        ),
    )
    series = IntradaySeries(symbol=symbol, points=points, quote=quote, ndays=5)
    text = rt.single_stock_text(series, ndays=5)
    assert "5日分时" in text
    assert "分日收盘" in text
    assert "08-26" in text and "09-01" in text
    assert "五日累计" in text
    assert "今日涨跌幅" in text
    assert "开盘价" not in text
