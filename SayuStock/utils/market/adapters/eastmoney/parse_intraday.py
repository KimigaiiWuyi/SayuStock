"""trends CSV / 已解析 list → IntradaySeries。"""

from __future__ import annotations

from typing import Mapping, Sequence
from datetime import datetime

from ...errors import MarketError, empty_error, parse_error
from ...models import Quote, SymbolRef, IntradayPoint, IntradaySeries
from .json_util import as_mapping
from .map_fields import PROVIDER


def _parse_ts(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_trend_line(line: str) -> IntradayPoint | None:
    parts = line.split(",")
    if len(parts) < 8:
        return None
    ts = _parse_ts(parts[0])
    if ts is None:
        return None
    try:
        return IntradayPoint(
            ts=ts,
            price=float(parts[1]),
            open=float(parts[2]),
            high=float(parts[3]),
            low=float(parts[4]),
            volume=float(parts[5]),
            amount=float(parts[6]),
            avg_price=float(parts[7]),
        )
    except ValueError:
        return None


def _num(row: Mapping[str, object], key: str, fallback: float = 0.0) -> float:
    if key not in row:
        return fallback
    raw = row[key]
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return fallback
    return fallback


def parse_trend_dicts(rows: Sequence[Mapping[str, object]]) -> tuple[IntradayPoint, ...]:
    points: list[IntradayPoint] = []
    for row in rows:
        if "datetime" not in row or "price" not in row:
            continue
        ts_raw = row["datetime"]
        if not isinstance(ts_raw, str):
            continue
        ts = _parse_ts(ts_raw)
        if ts is None:
            continue
        price = _num(row, "price")
        points.append(
            IntradayPoint(
                ts=ts,
                price=price,
                open=_num(row, "open", price),
                high=_num(row, "high", price),
                low=_num(row, "low", price),
                volume=_num(row, "amount"),
                amount=_num(row, "money"),
                avg_price=_num(row, "avg_price", price),
            )
        )
    return tuple(points)


def parse_intraday_from_trends_list(
    trends: object,
    symbol: SymbolRef,
    quote: Quote | None = None,
) -> IntradaySeries | MarketError:
    if isinstance(trends, str):
        return parse_error(trends, provider=PROVIDER)
    if not isinstance(trends, list) or not trends:
        return empty_error("分时为空", provider=PROVIDER)

    first = trends[0]
    if isinstance(first, str):
        points = tuple(p for line in trends if isinstance(line, str) for p in (parse_trend_line(line),) if p)
    elif isinstance(first, dict):
        points = parse_trend_dicts(trends)
    else:
        return parse_error("分时元素类型未知", provider=PROVIDER)

    if not points:
        return empty_error("分时解析后为空", provider=PROVIDER)
    return IntradaySeries(symbol=symbol, points=points, quote=quote)


def extract_trends_from_payload(payload: object) -> object | None:
    root = as_mapping(payload)
    if root is None:
        return None
    if "trends" in root:
        return root["trends"]
    if "data" in root:
        data = as_mapping(root["data"])
        if data is not None and "trends" in data:
            return data["trends"]
    return None
