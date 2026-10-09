"""纳斯达克 IPO 日历 payload → IpoEvent（priced / upcoming / filed）。"""

from __future__ import annotations

import re
from typing import Mapping
from datetime import date, timedelta
from dataclasses import replace

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


def _price_parts(value: object) -> tuple[float | None, str | None]:
    """单值进 issue_price；``18.00-20.00`` 这类区间留在 issue_price_text。"""
    if not isinstance(value, str):
        return None, None
    text = value.strip().lstrip("$").strip()
    if not text:
        return None, None
    try:
        return float(text.replace(",", "")), None
    except ValueError:
        return None, text


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
        price, price_text = _price_parts(row.get("proposedSharePrice"))
        events.append(
            IpoEvent(
                market=IpoMarket.US,
                code=str(row.get("proposedTickerSymbol") or "").strip(),
                name=name,
                listing_date=listing,
                issue_price=price,
                issue_price_text=price_text,
                raise_yi=_parse_dollar_yi(row.get("dollarValueOfSharesOffered")),
                currency="USD",
                board=_board_of(row.get("proposedExchange")),
            )
        )

    for row in _section_rows(data, "upcoming"):
        name = str(row.get("companyName") or "").strip()
        if not name:
            continue
        price, price_text = _price_parts(row.get("proposedSharePrice"))
        events.append(
            IpoEvent(
                market=IpoMarket.US,
                code=str(row.get("proposedTickerSymbol") or "").strip(),
                name=name,
                listing_date=_parse_us_date(row.get("expectedPriceDate")),
                issue_price=price,
                issue_price_text=price_text,
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


def _fill_score(ev: IpoEvent) -> int:
    """有发行价和上市日的 priced 行优先于只有区间或申报日的行。"""
    score = 0
    if ev.issue_price is not None:
        score += 8
    if ev.listing_date is not None:
        score += 4
    if ev.issue_price_text:
        score += 2
    if ev.raise_yi is not None:
        score += 1
    if ev.filed_date is not None:
        score += 1
    return score


def _merge_pair(left: IpoEvent, right: IpoEvent) -> IpoEvent:
    primary, secondary = (left, right) if _fill_score(left) >= _fill_score(right) else (right, left)
    listing = primary.listing_date if primary.listing_date is not None else secondary.listing_date
    apply_date = primary.apply_date if primary.apply_date is not None else secondary.apply_date
    apply_end = primary.apply_end_date if primary.apply_end_date is not None else secondary.apply_end_date
    ballot = primary.ballot_date if primary.ballot_date is not None else secondary.ballot_date
    pay = primary.pay_date if primary.pay_date is not None else secondary.pay_date
    grey = primary.grey_market_date if primary.grey_market_date is not None else secondary.grey_market_date
    filed = primary.filed_date if primary.filed_date is not None else secondary.filed_date
    price = primary.issue_price if primary.issue_price is not None else secondary.issue_price
    price_text = primary.issue_price_text if primary.issue_price_text is not None else secondary.issue_price_text
    raised = primary.raise_yi if primary.raise_yi is not None else secondary.raise_yi
    first_day = primary.first_day_change if primary.first_day_change is not None else secondary.first_day_change
    oversub = primary.oversubscription if primary.oversubscription is not None else secondary.oversubscription
    board = primary.board if primary.board is not None else secondary.board
    return replace(
        primary,
        listing_date=listing,
        apply_date=apply_date,
        apply_end_date=apply_end,
        ballot_date=ballot,
        pay_date=pay,
        grey_market_date=grey,
        filed_date=filed,
        issue_price=price,
        issue_price_text=price_text,
        raise_yi=raised,
        first_day_change=first_day,
        oversubscription=oversub,
        board=board,
    )


def dedupe_ipo_events(events: list[IpoEvent]) -> list[IpoEvent]:
    """按 (code, name) 合并；跨月时保留信息更全的那一行，空字段用另一行补。"""
    order: list[tuple[str, str]] = []
    merged: dict[tuple[str, str], IpoEvent] = {}
    for ev in events:
        key = (ev.code.upper(), ev.name.casefold())
        prev = merged.get(key)
        if prev is None:
            order.append(key)
            merged[key] = ev
        else:
            merged[key] = _merge_pair(prev, ev)
    return [merged[key] for key in order]


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
