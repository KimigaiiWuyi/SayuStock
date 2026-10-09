"""datacenter 新股申购表 / clist 港美股上市列表 → IpoEvent。"""

from __future__ import annotations

import re
from typing import Mapping
from datetime import date

from ...enums import IpoMarket
from ...errors import MarketError, empty_error, parse_error
from ...models import IpoEvent
from .json_util import opt_int, opt_str, opt_float, as_mapping, require_mapping
from .map_fields import PROVIDER

# A股代码前缀 → 板块。北交所记录的 MARKET 字段为空，统一按前缀推断。
_CN_BOARD_PREFIX: tuple[tuple[str, str], ...] = (
    ("30", "创业板"),
    ("68", "科创板"),
    ("92", "北交所"),
    ("43", "北交所"),
    ("83", "北交所"),
    ("87", "北交所"),
    ("60", "沪主板"),
    ("00", "深主板"),
)

# 港股新上市里的基金/杠杆产品；-R/-U 是同一基金的多币柜台重复行
_HK_NOISE_NAME_RE = re.compile(r"ETF|基金|REIT|信托|Trust|杠杆|反向|两倍|做空", re.IGNORECASE)
# 美股 Wt/Warrant/Unit/Right 是 SPAC 权证/单位行，不是 IPO 主体
_US_NOISE_NAME_RE = re.compile(r"\b(?:Wt|Warrant|Warrants|Unit|Units|Right|Rights)\b|ETF|基金", re.IGNORECASE)

# clist f13 → 美股交易所
_US_EXCHANGE: dict[int, str] = {105: "纳斯达克", 106: "纽交所", 107: "美交所"}


def _parse_em_date(value: object) -> date | None:
    """东财日期 ``YYYY-MM-DD HH:MM:SS``；空值与 1900 前哨兵值视为无。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    if parsed.year <= 1900:
        return None
    return parsed


def _cn_board_of(code: str) -> str:
    for prefix, board in _CN_BOARD_PREFIX:
        if code.startswith(prefix):
            return board
    return "A股"


def parse_ipo_apply_row(row: Mapping[str, object]) -> IpoEvent | None:
    name = opt_str(row, "SECURITY_NAME_ABBR") or opt_str(row, "SECURITY_NAME")
    code = opt_str(row, "SECURITY_CODE")
    if not name or not code:
        return None
    apply_date = _parse_em_date(row.get("APPLY_DATE"))
    listing_date = _parse_em_date(row.get("LISTING_DATE"))
    if apply_date is None and listing_date is None:
        return None
    return IpoEvent(
        market=IpoMarket.CN,
        code=code,
        name=name,
        listing_date=listing_date,
        apply_date=apply_date,
        ballot_date=_parse_em_date(row.get("BALLOT_NUM_DATE")),
        pay_date=_parse_em_date(row.get("BALLOT_PAY_DATE")),
        issue_price=opt_float(row, "ISSUE_PRICE"),
        raise_yi=opt_float(row, "TOTAL_RAISE_FUNDS"),
        currency="CNY",
        first_day_change=opt_float(row, "LD_CLOSE_CHANGE"),
        board=_cn_board_of(code),
    )


def parse_ipo_apply_payload(payload: object) -> list[IpoEvent] | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("新股申购响应无效", provider=PROVIDER)
    result = require_mapping(root, "result")
    if result is None:
        message = root.get("message")
        detail = f": {message}" if message else ""
        return empty_error(f"新股申购无数据{detail}", provider=PROVIDER)
    data = result.get("data")
    rows = data if isinstance(data, list) else []
    events = [ev for ev in (parse_ipo_apply_row(r) for r in rows if isinstance(r, dict)) if ev is not None]
    if not events:
        return empty_error("新股申购列表为空", provider=PROVIDER)
    return events


def _date_from_yyyymmdd(value: object) -> date | None:
    compact: int | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        compact = value
    elif isinstance(value, str) and value.strip().isdigit():
        compact = int(value.strip())
    if compact is None or compact < 20000101 or compact > 29991231:
        return None
    try:
        return date(compact // 10000, compact // 100 % 100, compact % 100)
    except ValueError:
        return None


def _is_ipo_noise(market: IpoMarket, code: str, name: str) -> bool:
    """剔除非普通股新上市条目：ETF/基金/杠杆产品、港股多币柜台、美股权证/单位。"""
    if market == IpoMarket.HK:
        if name.endswith(("-R", "-U")):
            return True
        return bool(_HK_NOISE_NAME_RE.search(name))
    if market == IpoMarket.US:
        # 东财美股代码后缀约定：U=单位 W=权证 R=权利（位数 ≥5 才可能是后缀）
        if len(code) >= 5 and code[-1] in "UWR":
            return True
        return bool(_US_NOISE_NAME_RE.search(name))
    return False


def _iter_clist_diff(diff: object) -> list[Mapping[str, object]]:
    """list 直接用；``{"0": row}`` 展开值。自带 f12 的对象是单行。"""
    if isinstance(diff, list):
        return [row for row in diff if isinstance(row, dict)]
    if isinstance(diff, dict):
        if "f12" in diff or "f14" in diff:
            return [diff]
        return [row for row in diff.values() if isinstance(row, dict)]
    return []


def parse_ipo_clist_row(
    row: Mapping[str, object],
    market: IpoMarket,
    *,
    board: str | None = None,
) -> IpoEvent | None:
    code = opt_str(row, "f12")
    name = opt_str(row, "f14")
    if not code or not name:
        return None
    listing_date = _date_from_yyyymmdd(row.get("f26"))
    if listing_date is None:
        return None
    if _is_ipo_noise(market, code, name):
        return None
    if market == IpoMarket.US:
        f13 = opt_int(row, "f13")
        label = _US_EXCHANGE.get(f13) if f13 is not None else None
    else:
        # f13 在港股 clist 里是市场号 116，区分不了主板/创业板，由查询侧传入
        label = board
    return IpoEvent(
        market=market,
        code=code,
        name=name,
        listing_date=listing_date,
        currency="HKD" if market == IpoMarket.HK else "USD",
        board=label,
    )


def parse_ipo_clist_payload(
    payload: object,
    market: IpoMarket,
    *,
    board: str | None = None,
) -> list[IpoEvent] | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("IPO上市列表响应无效", provider=PROVIDER)
    data = require_mapping(root, "data")
    if data is None:
        return empty_error("IPO上市列表无数据", provider=PROVIDER)
    rows = _iter_clist_diff(data.get("diff"))
    events = [ev for ev in (parse_ipo_clist_row(row, market, board=board) for row in rows) if ev is not None]
    if not events:
        return empty_error(f"{market.value} IPO上市列表为空", provider=PROVIDER)
    return events
