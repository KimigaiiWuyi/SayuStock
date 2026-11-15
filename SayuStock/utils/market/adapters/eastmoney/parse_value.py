"""估值序列 TypedDict / rows → ValueSeries。"""

from __future__ import annotations

from datetime import date, datetime

from ...enums import ValueKind, AssetClass
from ...errors import MarketError, empty_error, parse_error
from ...models import SymbolRef, ValuePoint, ValueSeries
from .json_util import opt_str, opt_float, as_mapping
from .map_fields import PROVIDER
from ...models.value import DividendEvent


def _parse_day(raw: str) -> date | None:
    text = raw[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_events(raw: object) -> tuple[DividendEvent, ...]:
    if not isinstance(raw, list):
        return ()
    events: list[DividendEvent] = []
    for item in raw:
        event = as_mapping(item)
        if event is None:
            continue
        ex_date = _parse_day(opt_str(event, "ex_date") or "")
        if ex_date is not None:
            events.append(
                DividendEvent(
                    ex_date,
                    _parse_day(opt_str(event, "report_date") or ""),
                    opt_float(event, "bonus_per_share"),
                    opt_float(event, "contribution_pct"),
                    "is_planned" in event and event["is_planned"] is True,
                )
            )
    return tuple(events)


def parse_value_series_payload(payload: object, *, kind: ValueKind) -> ValueSeries | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("value payload 非对象", provider=PROVIDER)
    if "rows" not in root:
        return empty_error("缺少 rows", provider=PROVIDER)
    rows = root["rows"]
    if not isinstance(rows, list) or not rows:
        return empty_error("估值序列为空", provider=PROVIDER)

    points: list[ValuePoint] = []
    missing_days: list[date] = []
    for item in rows:
        row = as_mapping(item)
        if row is None or "date" not in row or "value" not in row:
            continue
        day_raw = row["date"]
        if not isinstance(day_raw, str):
            continue
        day = _parse_day(day_raw)
        value = opt_float(row, "value")
        if day is None:
            continue
        if value is None:
            missing_days.append(day)
            continue
        events = _parse_events(row["events"]) if kind == ValueKind.DY and "events" in row else ()
        points.append(ValuePoint(day=day, value=value, events=events))
    if not points:
        return empty_error("估值点解析后为空", provider=PROVIDER)

    code = opt_str(root, "code") or ""
    name = opt_str(root, "name") or code
    secid = opt_str(root, "secid") or code
    symbol = SymbolRef(
        code=code,
        name=name,
        asset_class=AssetClass.EQUITY,
        exchange="EM",
        provider_symbol=secid,
    )
    return ValueSeries(symbol=symbol, kind=kind, points=tuple(points), missing_days=tuple(sorted(set(missing_days))))
