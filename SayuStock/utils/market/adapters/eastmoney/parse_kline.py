"""data.klines CSV → KlineSeries。"""

from __future__ import annotations

from typing import Mapping
from datetime import datetime

from ...enums import KlinePeriod
from ...errors import MarketError, empty_error, parse_error
from ...models import Bar, SymbolRef, KlineSeries
from .json_util import opt_str, as_mapping, require_mapping
from .map_fields import PROVIDER


def _parse_bar_ts(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    if " " in text:
        try:
            return datetime.strptime(text.split(" ")[0], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def parse_kline_line(line: str) -> Bar | None:
    parts = line.split(",")
    if len(parts) < 6:
        return None
    ts = _parse_bar_ts(parts[0])
    if ts is None:
        return None
    try:
        open_ = float(parts[1])
        close = float(parts[2])
        high = float(parts[3])
        low = float(parts[4])
        volume = float(parts[5])
        amount = float(parts[6]) if len(parts) > 6 and parts[6] not in ("", "-") else None
        amplitude = float(parts[7]) if len(parts) > 7 and parts[7] not in ("", "-") else None
        change_pct = float(parts[8]) if len(parts) > 8 and parts[8] not in ("", "-") else None
        change_amount = float(parts[9]) if len(parts) > 9 and parts[9] not in ("", "-") else None
        turnover_rate = float(parts[10]) if len(parts) > 10 and parts[10] not in ("", "-") else None
    except ValueError:
        return None
    return Bar(
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=amount,
        amplitude=amplitude,
        change_pct=change_pct,
        change_amount=change_amount,
        turnover_rate=turnover_rate,
    )


def parse_kline_payload(
    payload: object,
    *,
    symbol: SymbolRef,
    period: KlinePeriod,
    adjusted: bool = True,
) -> KlineSeries | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("kline payload 非对象", provider=PROVIDER)
    data = require_mapping(root, "data")
    if data is None:
        return empty_error("kline data 为空", provider=PROVIDER)
    if "klines" not in data:
        return empty_error("缺少 klines", provider=PROVIDER)
    raw_lines = data["klines"]
    if not isinstance(raw_lines, list) or not raw_lines:
        return empty_error("klines 为空", provider=PROVIDER)

    bars: list[Bar] = []
    for line in raw_lines:
        if not isinstance(line, str):
            continue
        bar = parse_kline_line(line)
        if bar is not None:
            bars.append(bar)
    if not bars:
        return empty_error("K 线解析后为空", provider=PROVIDER)

    # 名称以 payload 为准补全（去掉 sec_type 后缀）；版块标签保留在 sec_type
    name = opt_str(data, "name")
    if name:
        clean = name.split(" (")[0].strip()
        # 若 payload 带 " (韩股)" 等且 fallback 无 sec_type，从后缀回填
        sec_type = symbol.sec_type
        if not sec_type and " (" in name and name.endswith(")"):
            sec_type = name.rsplit(" (", 1)[-1].rstrip(")").strip()
        symbol = SymbolRef(
            code=symbol.code,
            name=clean,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=sec_type,
        )
    code = opt_str(data, "code")
    if code:
        symbol = SymbolRef(
            code=code,
            name=symbol.name,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=symbol.sec_type,
        )

    return KlineSeries(symbol=symbol, period=period, bars=tuple(bars), adjusted=adjusted)


def symbol_from_kline_payload(payload: Mapping[str, object], fallback: SymbolRef) -> SymbolRef:
    data = require_mapping(payload, "data")
    if data is None:
        return fallback
    code = opt_str(data, "code") or fallback.code
    name_raw = opt_str(data, "name") or fallback.name
    name = name_raw.split(" (")[0].strip() if " (" in name_raw else name_raw
    sec_type = fallback.sec_type
    if not sec_type and " (" in name_raw and name_raw.endswith(")"):
        sec_type = name_raw.rsplit(" (", 1)[-1].rstrip(")").strip()
    return SymbolRef(
        code=code,
        name=name,
        asset_class=fallback.asset_class,
        exchange=fallback.exchange,
        provider_symbol=fallback.provider_symbol,
        sec_type=sec_type,
    )
