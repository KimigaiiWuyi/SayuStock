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
from SayuStock.stock_info.draw_info import (
    _BREADTH_BAR_RIGHT,
    _BREADTH_BAR_WIDTH,
    _BREADTH_BAR_COLORS,
    breadth_bar_left,
)

# texture2d/div.png 实测尺寸（概览图把分布柱贴在这块 850×500 贴图上）
DIV_WIDTH = 850


def test_every_band_has_direction_and_color() -> None:
    """分档表一增删，方向/配色必须同步补齐，不能漏档或错色。"""
    assert set(BREADTH_BANDS) == set(BREADTH_DIRECTION)
    assert set(BREADTH_BANDS) == set(_BREADTH_BAR_COLORS)


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


def test_last_bar_stays_inside_canvas_for_any_band_count() -> None:
    for total in range(2, len(BREADTH_BANDS) + 3):
        last = breadth_bar_left(total - 1, total)
        assert last + _BREADTH_BAR_WIDTH == _BREADTH_BAR_RIGHT
        assert last + _BREADTH_BAR_WIDTH <= DIV_WIDTH
        assert breadth_bar_left(0, total) >= 0


def test_twelve_band_layout_keeps_legacy_spacing() -> None:
    """12 档（加「平」之前的档数）间距仍是 66px：老图视觉不回归。"""
    assert [breadth_bar_left(i, 12) for i in range(12)] == [45 + 66 * i for i in range(12)]


def test_thirteen_band_layout_fits() -> None:
    """13 档（当前口径）末柱右缘 807，家数标签也在画布内。"""
    last = breadth_bar_left(len(BREADTH_BANDS) - 1, len(BREADTH_BANDS))
    assert last + _BREADTH_BAR_WIDTH == 807
    assert last + _BREADTH_BAR_WIDTH / 2 <= DIV_WIDTH
