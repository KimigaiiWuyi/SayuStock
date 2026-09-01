"""天天基金搜索 / 净值 JSON → 领域模型。"""

from __future__ import annotations

from typing import Mapping
from datetime import date, datetime
from dataclasses import dataclass

from ...enums import AssetClass, KlinePeriod
from ...errors import MarketError, empty_error
from ...models import Bar, Quote, SymbolRef, KlineSeries
from ..eastmoney.json_util import opt_str, opt_float, as_mapping

PROVIDER = "tiantian"


@dataclass(frozen=True, slots=True)
class FundHit:
    code: str
    name: str
    fund_type: str
    unit_nav: float | None = None


def make_fund_symbol(code: str, name: str, fund_type: str = "") -> SymbolRef:
    st = fund_type.strip() or "基金"
    return SymbolRef(
        code=code,
        name=name or code,
        asset_class=AssetClass.FUND,
        exchange="OTC",
        provider_symbol=f"150.{code}",
        sec_type=st,
    )


def _fold(text: str) -> str:
    return text.strip().casefold()


def _row_search_keys(row: Mapping[str, object]) -> list[str]:
    keys: list[str] = []
    code = opt_str(row, "CODE") or opt_str(row, "_id")
    if code is not None:
        keys.append(code)
    name = opt_str(row, "NAME")
    if name is not None:
        keys.append(name)
    jp = opt_str(row, "JP")
    if jp is not None:
        keys.append(jp)
    if "FundBaseInfo" in row:
        info = as_mapping(row["FundBaseInfo"])
        if info is not None:
            short = opt_str(info, "SHORTNAME")
            if short is not None:
                keys.append(short)
            other = opt_str(info, "OTHERNAME")
            if other is not None:
                keys.extend(part.strip() for part in other.split(",") if part.strip())
    return keys


def parse_search_payload(payload: object, query: str) -> FundHit | None:
    root = as_mapping(payload)
    if root is None:
        return None
    if "Datas" not in root:
        return None
    rows = root["Datas"]
    if not isinstance(rows, list) or not rows:
        return None
    q_fold = _fold(query)
    picked: Mapping[str, object] | None = None
    for raw in rows:
        row = as_mapping(raw)
        if row is None:
            continue
        code = opt_str(row, "CODE") or opt_str(row, "_id")
        if code is None:
            continue
        aliases = _row_search_keys(row)
        if any(_fold(alias) == q_fold for alias in aliases):
            picked = row
            break
        if picked is None:
            picked = row
    if picked is None:
        return None
    code = opt_str(picked, "CODE") or opt_str(picked, "_id")
    name = opt_str(picked, "NAME")
    if code is None:
        return None
    fund_type = ""
    unit_nav: float | None = None
    if "FundBaseInfo" in picked:
        info = as_mapping(picked["FundBaseInfo"])
        if info is not None:
            fund_type = opt_str(info, "FTYPE") or opt_str(info, "SHORTNAME") or ""
            if name is None:
                name = opt_str(info, "SHORTNAME")
            unit_nav = opt_float(info, "DWJZ")
    return FundHit(code=code, name=name or code, fund_type=fund_type, unit_nav=unit_nav)


def _parse_nav_row(row: Mapping[str, object]) -> Bar | None:
    day_s = opt_str(row, "FSRQ")
    if day_s is None:
        return None
    try:
        ts = datetime.strptime(day_s, "%Y-%m-%d")
    except ValueError:
        return None
    ljjz = opt_float(row, "LJJZ")
    dwjz = opt_float(row, "DWJZ")
    close = ljjz if ljjz is not None else dwjz
    if close is None:
        return None
    change_pct = opt_float(row, "JZZZL")
    return Bar(
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
        amount=0.0,
        amplitude=0.0,
        change_pct=change_pct,
        change_amount=None,
        turnover_rate=None,
    )


def parse_nav_rows(
    rows: list[object],
    *,
    symbol: SymbolRef,
    period: KlinePeriod,
    start: date | None,
    end: date | None,
) -> KlineSeries | MarketError:
    bars: list[Bar] = []
    seen: set[date] = set()
    for raw in rows:
        row = as_mapping(raw)
        if row is None:
            continue
        bar = _parse_nav_row(row)
        if bar is None:
            continue
        day = bar.ts.date()
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        if day in seen:
            continue
        seen.add(day)
        bars.append(bar)
    if not bars:
        return empty_error("净值解析后为空", provider=PROVIDER)
    bars.sort(key=lambda b: b.ts)
    filled: list[Bar] = []
    prev_close: float | None = None
    for bar in bars:
        chg_amt = (bar.close - prev_close) if prev_close is not None else None
        chg_pct = None
        if prev_close is not None and prev_close != 0:
            chg_pct = (bar.close - prev_close) / prev_close * 100.0
        filled.append(
            Bar(
                ts=bar.ts,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                amount=bar.amount,
                amplitude=bar.amplitude,
                change_pct=chg_pct,
                change_amount=chg_amt,
                turnover_rate=bar.turnover_rate,
            )
        )
        prev_close = bar.close
    return KlineSeries(symbol=symbol, period=period, bars=tuple(filled), adjusted=True)


def quote_from_nav_rows(symbol: SymbolRef, hit: FundHit, rows_newest_first: list[object]) -> Quote:
    """Quote 用单位净值 DWJZ + JZZZL；K 线对比仍用累计净值 LJJZ。"""
    units: list[tuple[datetime, float, float | None]] = []
    for raw in rows_newest_first:
        row = as_mapping(raw)
        if row is None:
            continue
        day_s = opt_str(row, "FSRQ")
        dwjz = opt_float(row, "DWJZ")
        if day_s is None or dwjz is None:
            continue
        try:
            ts = datetime.strptime(day_s, "%Y-%m-%d")
        except ValueError:
            continue
        units.append((ts, dwjz, opt_float(row, "JZZZL")))
        if len(units) >= 2:
            break
    if not units:
        price = hit.unit_nav if hit.unit_nav is not None else 0.0
        return Quote(
            symbol=symbol,
            price=price,
            open=None,
            high=None,
            low=None,
            prev_close=None,
            change_pct=None,
            change_amount=None,
            volume=0.0,
            amount=0.0,
            turnover_rate=None,
            pe=None,
            pb=None,
            market_cap=None,
            float_market_cap=None,
            industry=hit.fund_type or None,
            limit_up=None,
            limit_down=None,
            as_of=None,
        )
    ts, price, jzzzl = units[0]
    prev = units[1][1] if len(units) > 1 else None
    if prev is None and jzzzl is not None and price != 0:
        prev = price / (1.0 + jzzzl / 100.0)
    chg_amt = (price - prev) if prev is not None else None
    return Quote(
        symbol=symbol,
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=prev,
        change_pct=jzzzl,
        change_amount=chg_amt,
        volume=0.0,
        amount=0.0,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=hit.fund_type or None,
        limit_up=None,
        limit_down=None,
        as_of=ts,
    )
