"""Avoid endpoint and event-label collisions without moving their data."""

from datetime import date, datetime

import pytest

from SayuStock.render.compare import prepare_compare
from SayuStock.render.palette import SERIES
from SayuStock.utils.constant import ErroText
from SayuStock.render.valuation import prepare_valuation
from SayuStock.render.chart_page import TOP, RIGHT, Mark, Panel, ChartPage, page_html, add_end_labels
from SayuStock.utils.market.enums import ValueKind, AssetClass, KlinePeriod
from SayuStock.utils.market.models import Bar, SymbolRef, KlineSeries, ValueSeries
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
    assert [mark.label for mark in panel.marks] == ["+10.00%"]
    (item,) = page.legend
    assert item.label == "沪深300ETF"
    assert item.value == "+10.00%"
    assert "100.000 → 110.000" in item.detail
    assert "最大涨幅 +22.22% · 01-07→01-08" in item.detail
    assert "最大回撤 -25.00% · 01-06→01-07" in item.detail

    ranges = [rule for rule in panel.rules if rule.dash == "4 4"]
    assert len(ranges) == 2
    coordinates = {(dot.x, dot.y) for dot in panel.dots}
    for rule in ranges:
        assert (rule.x1, rule.y1) in coordinates
        assert (rule.x2, rule.y2) in coordinates
    assert item.detail in page_html(page)


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
