"""东财涨跌分布解析（updowndistribution）。"""

from __future__ import annotations

from typing import Mapping

from ...errors import MarketError, empty_error, parse_error
from ...models import BREADTH_BANDS, BreadthBar, BreadthBucket

PROVIDER = "eastmoney"

# 原始结构：key="2" 为正侧 10 档计数，key="3" 为负侧 10 档，key="5"/"6" 为涨跌停家数。
# 东财把不同板块的涨停带（主板10/双创20/北交所30）拆成 5 个槽并入同一展示档，
# 故正负侧各取 sum 合并，与自算全A的 BREADTH_BANDS 口径一致。
_POS_SLOT_MAP: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("5~10", (5, 6, 7, 8, 9)),
    ("3~5", (3, 4)),
    ("2~3", (2,)),
    ("1~2", (1,)),
    ("0~1", (0,)),
)
_NEG_SLOT_MAP: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("-5~-10", (5, 6, 7, 8, 9)),
    ("-3~-5", (3, 4)),
    ("-2~-3", (2,)),
    ("-1~-2", (1,)),
    ("0~-1", (0,)),
)


def _int_list(raw: Mapping[str, object], key: str, size: int) -> list[int]:
    """原始档位列表 → 定长 int 列表；类型不符或缺项一律补 0（供应商脏数据容错）。"""
    value = raw.get(key, [])
    if not isinstance(value, list):
        return [0] * size
    out: list[int] = []
    for item in value:
        if isinstance(item, bool):
            out.append(int(item))
        elif isinstance(item, (int, float)):
            out.append(int(item))
        elif isinstance(item, str):
            try:
                out.append(int(float(item)))
            except ValueError:
                out.append(0)
        else:
            out.append(0)
    if len(out) < size:
        out.extend([0] * (size - len(out)))
    return out[:size]


def _int_val(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key, 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def parse_breadth_payload(raw: object) -> BreadthBar | MarketError:
    """updowndistribution 原始 dict → BreadthBar。

    `平` 档该接口不提供（原始结构无对应键），按 0 填充，与改造前
    ai_tools/draw_info 的 `flat: 0` 行为一致，不引入新的口径偏差。
    """
    if not isinstance(raw, Mapping):
        return parse_error("东财涨跌分布响应非对象", provider=PROVIDER)
    pos = _int_list(raw, "2", 10)
    neg = _int_list(raw, "3", 10)
    counts: dict[str, int] = {label: 0 for label in BREADTH_BANDS}
    counts["涨停"] = _int_val(raw, "5")
    counts["跌停"] = _int_val(raw, "6")
    for label, slots in _POS_SLOT_MAP:
        counts[label] = sum(pos[i] for i in slots)
    for label, slots in _NEG_SLOT_MAP:
        counts[label] = sum(neg[i] for i in slots)
    if not any(counts.values()):
        return empty_error("东财涨跌分布解析后全零", provider=PROVIDER)
    return BreadthBar(
        buckets=tuple(BreadthBucket(label=label, count=counts[label]) for label in BREADTH_BANDS),
        raw=raw,
    )
