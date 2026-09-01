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


def _usable_price(price: float, open_px: float) -> float | None:
    """集合竞价首分钟现价常为 0，用开盘价顶上，避免五日图 Y 轴被 0 拉爆。"""
    if price != 0.0:
        return price
    if open_px != 0.0:
        return open_px
    return None


def parse_trend_line(line: str) -> IntradayPoint | None:
    parts = line.split(",")
    if len(parts) < 8:
        return None
    ts = _parse_ts(parts[0])
    if ts is None:
        return None
    try:
        open_px = float(parts[2])
        price = _usable_price(float(parts[1]), open_px)
        if price is None:
            return None
        avg_raw = float(parts[7])
        avg_price = avg_raw if avg_raw != 0.0 else price
        return IntradayPoint(
            ts=ts,
            price=price,
            open=open_px,
            high=float(parts[3]),
            low=float(parts[4]),
            volume=float(parts[5]),
            amount=float(parts[6]),
            avg_price=avg_price,
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
        open_px = _num(row, "open", 0.0)
        price = _usable_price(_num(row, "price"), open_px)
        if price is None:
            continue
        avg_raw = _num(row, "avg_price", 0.0)
        points.append(
            IntradayPoint(
                ts=ts,
                price=price,
                open=open_px if open_px != 0.0 else price,
                high=_num(row, "high", price),
                low=_num(row, "low", price),
                volume=_num(row, "amount"),
                amount=_num(row, "money"),
                avg_price=avg_raw if avg_raw != 0.0 else price,
            )
        )
    return tuple(points)


def parse_intraday_from_trends_list(
    trends: object,
    symbol: SymbolRef,
    quote: Quote | None = None,
    *,
    ndays: int = 1,
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
    days = ndays if ndays > 1 else 1
    return IntradaySeries(symbol=symbol, points=points, quote=quote, ndays=days)


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
