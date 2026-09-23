"""纳斯达克 IPO 日历 payload → IpoEvent（priced / upcoming / filed）。"""

from __future__ import annotations

import re
from typing import Mapping
from datetime import date, timedelta

from ...enums import IpoMarket
from ...errors import MarketError, parse_error
from ...models import IpoEvent
from ..eastmoney.json_util import as_mapping, require_mapping

PROVIDER = "nasdaq"

# 交易所 → 中文（proposedExchange 原文大小写不定）
_EXCHANGE_RE = re.compile(r"nasdaq", re.IGNORECASE)
_DOLLAR_RE = re.compile(r"^\$?([\d,]+(?:\.\d+)?)$")


def _parse_us_date(value: object) -> date | None:
    """纳斯达克日期 ``M/D/YYYY``；空值/非日期返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        month, day, year = text.split("/")
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _parse_dollar_yi(value: object) -> float | None:
    """``"$250,000,000"`` → 亿美元。"""
    if not isinstance(value, str):
        return None
    m = _DOLLAR_RE.match(value.strip())
    if m is None:
        return None
    try:
        return float(m.group(1).replace(",", "")) / 1e8
    except ValueError:
        return None


def _parse_price(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return float(value.strip().replace(",", ""))
    except ValueError:
        return None


def _board_of(value: object) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return None
    if _EXCHANGE_RE.search(text):
        return "纳斯达克"
    if text.upper().startswith("NYSE"):
        return "纽交所"
    return text


def _section_rows(data: Mapping[str, object], section: str) -> list[Mapping[str, object]]:
    """priced/filed 直接在 rows；upcoming 在 upcomingTable.rows。"""
    node = require_mapping(data, section)
    if node is None:
        return []
    rows: object = node.get("rows")
    if rows is None and section == "upcoming":
        table = require_mapping(node, "upcomingTable")
        rows = table.get("rows") if table is not None else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def parse_nasdaq_calendar(payload: object) -> list[IpoEvent] | MarketError:
    """一个月的日历 → IpoEvent；withdrawn 不进日历。"""
    root = as_mapping(payload)
    if root is None:
        return parse_error("纳斯达克日历响应无效", provider=PROVIDER)
    data = require_mapping(root, "data")
    if data is None:
        return parse_error("纳斯达克日历无数据", provider=PROVIDER)
    events: list[IpoEvent] = []

    for row in _section_rows(data, "priced"):
        listing = _parse_us_date(row.get("pricedDate"))
        name = str(row.get("companyName") or "").strip()
        if listing is None or not name:
            continue
        events.append(
            IpoEvent(
                market=IpoMarket.US,
                code=str(row.get("proposedTickerSymbol") or "").strip(),
                name=name,
                listing_date=listing,
                issue_price=_parse_price(row.get("proposedSharePrice")),
                raise_yi=_parse_dollar_yi(row.get("dollarValueOfSharesOffered")),
                currency="USD",
                board=_board_of(row.get("proposedExchange")),
            )
        )

    for row in _section_rows(data, "upcoming"):
        name = str(row.get("companyName") or "").strip()
        if not name:
            continue
        events.append(
            IpoEvent(
                market=IpoMarket.US,
                code=str(row.get("proposedTickerSymbol") or "").strip(),
                name=name,
                listing_date=_parse_us_date(row.get("expectedPriceDate")),
                issue_price=_parse_price(row.get("proposedSharePrice")),
                raise_yi=_parse_dollar_yi(row.get("dollarValueOfSharesOffered")),
                currency="USD",
                board=_board_of(row.get("proposedExchange")),
            )
        )

    for row in _section_rows(data, "filed"):
        filed = _parse_us_date(row.get("filedDate"))
        name = str(row.get("companyName") or "").strip()
        if filed is None or not name:
            continue
        events.append(
            IpoEvent(
                market=IpoMarket.US,
                code=str(row.get("proposedTickerSymbol") or "").strip(),
                name=name,
                filed_date=filed,
                raise_yi=_parse_dollar_yi(row.get("dollarValueOfSharesOffered")),
                currency="USD",
                board=_board_of(row.get("proposedExchange")),
            )
        )

    return dedupe_ipo_events(events)


def dedupe_ipo_events(events: list[IpoEvent]) -> list[IpoEvent]:
    """按 (code, name) 去重；跨月抓取时同一笔交易可能出现两次。"""
    seen: set[tuple[str, str]] = set()
    out: list[IpoEvent] = []
    for ev in events:
        key = (ev.code, ev.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def months_for_window(anchor: date, *, before: int = 2, after: int = 7) -> list[str]:
    """覆盖 [anchor-before, anchor+after] 的月份列表（去重保序）。"""
    start = anchor - timedelta(days=max(before, 0))
    end = anchor + timedelta(days=max(after, 0))
    months: list[str] = []
    cur = start.replace(day=1)
    end_month = end.replace(day=1)
    while cur <= end_month:
        months.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return months
