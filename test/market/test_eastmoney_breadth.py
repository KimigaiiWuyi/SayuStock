"""东财涨跌分布（updowndistribution）分桶解析测试。

原始结构：key="2" 为正侧 10 档计数、key="3" 为负侧 10 档、key="4" 为平盘家数、
key="5"/"6" 为涨跌停家数。对拍依据：改造前 ai_tools/draw_info 直接按同样下标取值，
本测试锁住口径不变。
"""

from __future__ import annotations

from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import BREADTH_BANDS, breadth_counts as counts_of
from SayuStock.utils.market.adapters.eastmoney.parse_breadth import parse_breadth_payload

# 真实响应形状（20 档计数 + 平盘 + 涨跌停家数），非真实数值
PAYLOAD: dict[str, object] = {
    "2": [1200, 900, 600, 400, 300, 180, 120, 80, 50, 30],
    "3": [1100, 800, 500, 350, 250, 150, 100, 70, 45, 25],
    "4": 200,
    "5": 61,
    "6": 18,
}


def test_buckets_follow_canonical_band_order() -> None:
    bar = parse_breadth_payload(PAYLOAD)
    assert not is_market_error(bar)
    assert tuple(b.label for b in bar.buckets) == BREADTH_BANDS


def test_limit_counts_come_from_dedicated_keys() -> None:
    bar = parse_breadth_payload(PAYLOAD)
    assert not is_market_error(bar)
    counts = counts_of(bar)
    assert counts["涨停"] == 61
    assert counts["跌停"] == 18


def test_tail_slots_merge_into_5_to_limit_band() -> None:
    """东财把各板涨停带拆成 5 个槽，须并入同一展示档（与改造前 sum 口径一致）。"""
    bar = parse_breadth_payload(PAYLOAD)
    assert not is_market_error(bar)
    counts = counts_of(bar)
    assert counts["5~10"] == 180 + 120 + 80 + 50 + 30
    assert counts["-5~-10"] == 150 + 100 + 70 + 45 + 25
    assert counts["3~5"] == 400 + 300
    assert counts["-3~-5"] == 350 + 250
    assert counts["0~1"] == 1200
    assert counts["0~-1"] == 1100


def test_flat_count_comes_from_key_4() -> None:
    """平盘家数取 key="4"（实测与同花顺同口径 flat 一致），不再按 0 填充。"""
    bar = parse_breadth_payload(PAYLOAD)
    assert not is_market_error(bar)
    assert counts_of(bar)["平"] == 200


def test_raw_payload_is_retained_for_legacy_draw() -> None:
    bar = parse_breadth_payload(PAYLOAD)
    assert not is_market_error(bar)
    assert bar.raw is PAYLOAD


def test_ragged_and_dirty_slots_are_tolerated() -> None:
    """档位数组缺项或含脏值时补 0，不抛异常。"""
    bar = parse_breadth_payload({"2": [5, "7", None, True], "3": [], "5": "3", "6": 1})
    assert not is_market_error(bar)
    counts = counts_of(bar)
    assert counts["0~1"] == 5
    assert counts["1~2"] == 7
    assert counts["2~3"] == 0
    assert counts["3~5"] == 1  # True → 1
    assert counts["5~10"] == 0
    assert counts["涨停"] == 3
    assert counts["跌停"] == 1


def test_non_mapping_payload_is_error() -> None:
    assert is_market_error(parse_breadth_payload("<html>blocked</html>"))


def test_all_zero_payload_is_error() -> None:
    assert is_market_error(parse_breadth_payload({"2": [0] * 10, "3": [0] * 10, "5": 0, "6": 0}))
