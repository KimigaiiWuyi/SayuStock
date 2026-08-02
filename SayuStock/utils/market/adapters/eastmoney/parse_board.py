"""clist / hotmap diff 行 → BoardSnapshot。"""

from __future__ import annotations

from typing import Mapping, Sequence

from ...enums import BoardKind
from ...errors import MarketError, empty_error, parse_error
from ...models import BoardRow, BoardExtras, BoardSnapshot
from .json_util import opt_int, opt_str, opt_float, as_mapping, require_mapping
from .map_fields import PROVIDER, BOARD_FIELD


def parse_board_row(row: Mapping[str, object]) -> BoardRow | None:
    code = opt_str(row, BOARD_FIELD["code"])
    name = opt_str(row, BOARD_FIELD["name"])
    if code is None and name is None:
        return None
    fall_name = opt_str(row, BOARD_FIELD["fall_name"])
    fall_pct = opt_float(row, BOARD_FIELD["fall_change_pct"])
    extras = BoardExtras(
        pe=opt_float(row, BOARD_FIELD["pe"]),
        turnover_rate=opt_float(row, BOARD_FIELD["turnover_rate"]),
        volume_ratio=opt_float(row, BOARD_FIELD["volume_ratio"]),
        float_market_cap=opt_float(row, BOARD_FIELD["float_market_cap"]),
        up_count=opt_int(row, BOARD_FIELD["up_count"]),
        down_count=opt_int(row, BOARD_FIELD["down_count"]),
        lead_code=opt_str(row, BOARD_FIELD["lead_code"]),
        fall_name=fall_name,
        fall_change_pct=fall_pct,
    )
    has_extra = any(
        v is not None
        for v in (
            extras.pe,
            extras.turnover_rate,
            extras.volume_ratio,
            extras.float_market_cap,
            extras.up_count,
            extras.down_count,
            extras.lead_code,
            extras.fall_name,
            extras.fall_change_pct,
        )
    )
    return BoardRow(
        code=code or "",
        name=name or code or "",
        price=opt_float(row, BOARD_FIELD["price"]),
        change_pct=opt_float(row, BOARD_FIELD["change_pct"]),
        amount=opt_float(row, BOARD_FIELD["amount"]),
        market_cap=opt_float(row, BOARD_FIELD["market_cap"]),
        industry=opt_str(row, BOARD_FIELD["industry"]),
        lead_name=opt_str(row, BOARD_FIELD["lead_name"]),
        lead_change_pct=opt_float(row, BOARD_FIELD["lead_change_pct"]),
        fall_name=fall_name,
        fall_change_pct=fall_pct,
        extras=extras if has_extra else None,
    )


def _iter_diff(diff: object) -> Sequence[Mapping[str, object]]:
    if isinstance(diff, list):
        return [r for r in diff if isinstance(r, dict)]
    if isinstance(diff, dict):
        # 偶发 dict 形 diff
        return [v for v in diff.values() if isinstance(v, dict)]
    return []


def parse_board_payload(
    payload: object,
    *,
    kind: BoardKind,
    title: str,
) -> BoardSnapshot | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("board payload 非对象", provider=PROVIDER)
    data = require_mapping(root, "data")
    if data is None:
        return empty_error("board data 为空", provider=PROVIDER)
    if "diff" not in data:
        return empty_error("缺少 diff", provider=PROVIDER)
    rows: list[BoardRow] = []
    for item in _iter_diff(data["diff"]):
        row = parse_board_row(item)
        if row is not None:
            rows.append(row)
    if not rows:
        return empty_error("board 行为空", provider=PROVIDER)
    return BoardSnapshot(kind=kind, title=title, rows=tuple(rows))
