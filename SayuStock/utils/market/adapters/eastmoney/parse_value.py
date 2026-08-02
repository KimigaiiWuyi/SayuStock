"""估值序列 TypedDict / rows → ValueSeries。"""

from __future__ import annotations

from datetime import date, datetime

from ...enums import ValueKind, AssetClass
from ...errors import MarketError, empty_error, parse_error
from ...models import SymbolRef, ValuePoint, ValueSeries
from .json_util import opt_str, opt_float, as_mapping
from .map_fields import PROVIDER


def _parse_day(raw: str) -> date | None:
    text = raw[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


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
    for item in rows:
        row = as_mapping(item)
        if row is None or "date" not in row or "value" not in row:
            continue
        day_raw = row["date"]
        if not isinstance(day_raw, str):
            continue
        day = _parse_day(day_raw)
        value = opt_float(row, "value")
        if day is None or value is None:
            continue
        points.append(ValuePoint(day=day, value=value))
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
    return ValueSeries(symbol=symbol, kind=kind, points=tuple(points))
