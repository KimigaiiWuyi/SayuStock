"""Avoid endpoint and event-label collisions without moving their data."""

import re
from datetime import date, datetime

import pytest

from SayuStock.render.compare import prepare_compare
from SayuStock.render.palette import UP, DOWN, SERIES
from SayuStock.utils.constant import ErroText
from SayuStock.render.intraday import prepare_multi_intraday
from SayuStock.render.valuation import prepare_valuation
from SayuStock.render.chart_page import TOP, RIGHT, Mark, Panel, ChartPage, page_html, plot_bounds, add_end_labels
from SayuStock.utils.market.enums import ValueKind, AssetClass, KlinePeriod
from SayuStock.utils.market.models import Bar, SymbolRef, KlineSeries, ValueSeries, IntradayPoint, IntradaySeries
from SayuStock.utils.market.models.value import ValuePoint, DividendEvent


@pytest.mark.parametrize("ys", [(80, 80, 80), (80, 82, 84), (160, 161, 162)])
def test_nearby_end_labels_have_room_and_keep_their_own_leaders(ys: tuple[int, int, int]) -> None:
    panel = Panel("对比", 180, [])
    entries = [Mark(x, y, f"{i:+.2f}%", SERIES[i]) for i, (x, y) in enumerate(zip((300, 700, RIGHT), ys, strict=True))]
    add_end_labels(panel, entries)

    assert len(panel.marks) == len(panel.rules) == len(entries)
    assert all(b.y - a.y >= 20 for a, b in zip(panel.marks, panel.marks[1:]))
    for entry, mark, rule in zip(entries, panel.marks, panel.rules, strict=True):
        assert TOP <= mark.y - 4 <= panel.bottom
        assert mark.x > RIGHT
        assert (mark.label, mark.color) == (entry.label, entry.color)
        assert (rule.x1, rule.y1) == (entry.x + 5, entry.y)
        assert (rule.x2, rule.y2) == (mark.x - 4, mark.y - 4)
        assert rule.dash and rule.color == entry.color


@pytest.mark.parametrize("ys", [(), (60,), (30, 70, 130)])
def test_isolated_end_labels_stay_at_their_data_height(ys: tuple[int, ...]) -> None:
    panel = Panel("对比", 180, [])
    add_end_labels(panel, [Mark(RIGHT, y, str(y), SERIES[0]) for y in ys])
    assert [mark.y - 4 for mark in panel.marks] == list(ys)
    assert len(panel.rules) == len(ys)


def _series(name: str, prices: tuple[float, ...]) -> KlineSeries:
    bars = tuple(
        Bar(datetime(2026, 1, 5 + i), close, close, close, close, 1, None, None, None, None, None)
        for i, close in enumerate(prices)
    )
    return KlineSeries(SymbolRef("510300", name, AssetClass.ETF, "SSE", "510300"), KlinePeriod.D1_YEAR, bars, False)


def test_compare_keeps_short_rail_labels_and_full_interval_legend() -> None:
    page = prepare_compare([_series("沪深300ETF", (100, 120, 90, 110))])
    assert isinstance(page, ChartPage)
    panel = page.panels[0]
    assert [mark.label for mark in panel.marks] == ["沪深300ETF +10.00%"]
    assert [(span.text, span.color, span.bold) for span in panel.marks[0].spans] == [
        ("沪深300ETF", SERIES[0], True),
        (" +10.00%", UP, True),
    ]
    assert 'class="chart-end-label"' in page_html(page)
    assert len(panel.boxes) >= 2
    assert any("涨幅 +20.00% · 01-06" in "\n".join(box.lines) for box in panel.boxes)
    assert any("跌幅 -10.00% · 01-07" in "\n".join(box.lines) for box in panel.boxes)
    assert all(box.styled_lines for box in panel.boxes)
    assert any(span.color == UP and span.bold for box in panel.boxes for line in box.styled_lines for span in line)
    assert any(span.color == DOWN and span.bold for box in panel.boxes for line in box.styled_lines for span in line)
    (item,) = page.legend
    assert item.label == "沪深300ETF"
    assert item.value == "+10.00%"
    assert item.value_color == UP
    assert "100.000 → 110.000" in item.detail
    assert "最大涨幅 +22.22% · 01-07→01-08" in item.detail
    assert "最大回撤 -25.00% · 01-06→01-07" in item.detail
    assert any(span.color == UP and span.bold for row in item.detail_lines for span in row)
    assert any(span.color == DOWN and span.bold for row in item.detail_lines for span in row)

    ranges = [rule for rule in panel.rules if rule.dash == "4 4"]
    assert len(ranges) == 2
    coordinates = {(dot.x, dot.y) for dot in panel.dots}
    for rule in ranges:
        assert (rule.x1, rule.y1) in coordinates
        assert (rule.x2, rule.y2) in coordinates
    rendered_text = re.sub(r"<[^>]+>", "", page_html(page))
    assert "100.000 → 110.000" in rendered_text
    assert "最大涨幅 +22.22% · 01-07→01-08" in rendered_text
    assert "最大回撤 -25.00% · 01-06→01-07" in rendered_text


def test_zero_base_does_not_render_a_false_return() -> None:
    zero_base = _series("缺基准", (0, 10))
    assert prepare_compare([zero_base]) == ErroText["notData"]
    page = prepare_compare([zero_base, _series("沪深300ETF", (100, 110))])
    assert isinstance(page, ChartPage)
    assert [(item.label, item.value) for item in page.legend] == [("沪深300ETF", "+10.00%")]


def test_overlapping_dividends_keep_separate_markers_and_notes() -> None:
    ex_date = date(2026, 1, 6)
    events = tuple(DividendEvent(ex_date, date(2025, 9, day), day / 10, float(day), False) for day in range(1, 5))
    series = ValueSeries(
        SymbolRef("510300", "沪深300ETF", AssetClass.ETF, "SSE", "510300"),
        ValueKind.DY,
        (ValuePoint(date(2026, 1, 5), 2), ValuePoint(ex_date, 3, events), ValuePoint(date(2026, 1, 7), 4)),
    )
    page = prepare_valuation([series], ValueKind.DY, "股息率")
    assert isinstance(page, ChartPage)
    (panel,) = page.panels
    marks = sorted((mark for mark in panel.marks if mark.label.startswith("#")), key=lambda mark: mark.y)
    assert {mark.label for mark in marks} == {"#1", "#2", "#3", "#4"}
    assert all(b.y - a.y >= 15 for a, b in zip(marks, marks[1:]))
    leaders = [rule for rule in panel.rules if rule.dash and rule.x2 in {mark.x for mark in marks}]
    assert len(leaders) == 4
    assert len({(rule.x1, rule.y1) for rule in leaders}) == 1
    assert {(rule.x2, rule.y2 + 4) for rule in leaders} == {(mark.x, mark.y) for mark in marks}
    assert [note.split()[0] for note in page.notes] == ["#1", "#2", "#3", "#4"]
    assert all("除权 2026-01-06" in note and "每股" in note and "贡献" in note for note in page.notes)


def test_multi_intraday_end_labels_include_symbol_name_and_fit_right_rail() -> None:
    def _intraday(code: str, name: str, end_price: float) -> IntradaySeries:
        symbol = SymbolRef(code, name, AssetClass.EQUITY, "SSE", f"1.{code}")
        pts = (
            IntradayPoint(datetime(2026, 8, 28, 9, 30), 10.0, 10.0, 10.0, 10.0, 100.0, 1e6, 10.0),
            IntradayPoint(datetime(2026, 8, 28, 9, 31), end_price, end_price, end_price, end_price, 100.0, 1e6, 10.0),
        )
        return IntradaySeries(symbol, pts, quote=None, ndays=1, ref_close=10.0, provider="eastmoney")

    page = prepare_multi_intraday([_intraday("600101", "远山科技", 10.5), _intraday("600102", "星海微电子", 9.5)])
    assert isinstance(page, ChartPage)
    price = page.panels[0]
    assert {mark.label for mark in price.marks} == {"远山科技 +5.00%", "星海微电子 -5.00%"}
    spans_by_label = {mark.label: mark.spans for mark in price.marks}
    assert spans_by_label["远山科技 +5.00%"][1].color == UP
    assert spans_by_label["星海微电子 -5.00%"][1].color == DOWN
    assert [(rect.color, rect.opacity) for rect in price.backdrop[:2]] == [(UP, 0.08), (DOWN, 0.08)]
    assert [item.value_color for item in page.legend] == [UP, DOWN]
    assert page.right_margin > 77
    assert plot_bounds(page.width, page.right_margin)[1] == RIGHT
