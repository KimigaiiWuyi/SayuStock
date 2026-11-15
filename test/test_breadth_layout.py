"""大盘概览「涨跌分布」柱：分档口径 / 配色 / 是否画出 div.png 画布。

回归背景：加入「平」档后 BREADTH_BANDS 从 12 档变 13 档，而渲染端
① 仍按「第 6 根柱」对半判涨跌 → 平盘被涂成上涨红；
② 柱间距仍写死 66px → 末柱右缘 873 越出 850 宽的 div.png。
两条都在**不换源**时就会发生（东财正常供数也一样）。
"""

from __future__ import annotations

from SayuStock.utils.market.models import (
    BREADTH_BANDS,
    BREADTH_DIRECTION,
    BreadthBar,
    BreadthBucket,
    breadth_counts,
    breadth_up_down,
)
from SayuStock.utils.market_overview_html import breadth_columns


def test_every_band_has_direction_and_color() -> None:
    """分档表一增删，方向/配色必须同步补齐，不能漏档或错色。"""
    assert set(BREADTH_BANDS) == set(BREADTH_DIRECTION)
    assert [column.name for column in breadth_columns(dict.fromkeys(BREADTH_BANDS, 0))] == list(reversed(BREADTH_BANDS))


def test_flat_band_is_neither_up_nor_down() -> None:
    assert BREADTH_DIRECTION["平"] == 0
    counts = dict.fromkeys(BREADTH_BANDS, 0)
    counts["涨停"] = 61
    counts["平"] = 200
    counts["跌停"] = 18
    assert breadth_up_down(counts) == (61, 18)


def test_up_down_ignores_flat_band() -> None:
    """平盘家数无论多大都不计入任何一侧（旧实现按柱下标对半切会把它算进上涨）。"""
    counts = dict.fromkeys(BREADTH_BANDS, 0)
    counts["平"] = 9999
    assert breadth_up_down(counts) == (0, 0)


def test_counts_are_total_and_ordered() -> None:
    bar = BreadthBar(buckets=(BreadthBucket(label="涨停", count=3),))
    counts = breadth_counts(bar)
    assert tuple(counts) == BREADTH_BANDS
    assert counts["涨停"] == 3
    assert counts["跌停"] == 0


def test_flat_band_has_neutral_tone_and_positive_counts_have_height() -> None:
    counts = dict.fromkeys(BREADTH_BANDS, 0)
    counts["涨停"], counts["平"], counts["跌停"] = 50, 100, 20
    bars = {column.name: column for column in breadth_columns(counts)}
    assert bars["平"].tone == "flat" and bars["平"].height == 100
    assert bars["涨停"].tone == "up" and bars["涨停"].height == 50
    assert bars["跌停"].tone == "down" and bars["跌停"].height == 20
