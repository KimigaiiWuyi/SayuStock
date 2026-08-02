"""OKX 原生 candle / ticker → 领域模型（不经东财 f* 壳）。

OKX candle 列序（文档 v5）：
  [0] ts_ms, [1] o, [2] h, [3] l, [4] c, [5] vol, [6] volCcy, [7] volCcyQuote, [8] confirm
"""

from __future__ import annotations

from typing import Sequence
from datetime import datetime, timezone, timedelta

from ...enums import AssetClass, KlinePeriod
from ...errors import MarketError, empty_error, parse_error
from ...models import Bar, Quote, SymbolRef, KlineSeries, IntradayPoint, IntradaySeries

PROVIDER = "okx"
_TZ_UTC8 = timezone(timedelta(hours=8))


def make_symbol(inst_id: str) -> SymbolRef:
    return SymbolRef(
        code=inst_id,
        name=inst_id,
        asset_class=AssetClass.CRYPTO,
        exchange="OKX",
        provider_symbol=inst_id,
    )


def _as_candle_row(raw: object) -> Sequence[object] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 6:
        return raw
    return None


def _f(row: Sequence[object], index: int) -> float | None:
    if index >= len(row):
        return None
    value = row[index]
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _ts_ms(row: Sequence[object]) -> datetime | None:
    ms = _f(row, 0)
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=_TZ_UTC8).replace(tzinfo=None)


def candles_to_intraday_points(candles: Sequence[object]) -> tuple[IntradayPoint, ...]:
    points: list[IntradayPoint] = []
    for raw in candles:
        row = _as_candle_row(raw)
        if row is None:
            continue
        ts = _ts_ms(row)
        o = _f(row, 1)
        h = _f(row, 2)
        lo = _f(row, 3)
        c = _f(row, 4)
        vol = _f(row, 5) or 0.0
        turnover = _f(row, 7) if len(row) > 7 else None
        if ts is None or o is None or h is None or lo is None or c is None:
            continue
        money = turnover if turnover is not None else 0.0
        avg = (money / vol) if vol > 0 else c
        points.append(
            IntradayPoint(
                ts=ts,
                price=c,
                open=o,
                high=h,
                low=lo,
                volume=vol,
                amount=money,
                avg_price=avg,
            )
        )
    return tuple(points)


def candles_to_bars(candles: Sequence[object]) -> tuple[Bar, ...]:
    bars: list[Bar] = []
    prev_close: float | None = None
    for raw in candles:
        row = _as_candle_row(raw)
        if row is None:
            continue
        ts = _ts_ms(row)
        o = _f(row, 1)
        h = _f(row, 2)
        lo = _f(row, 3)
        c = _f(row, 4)
        vol = _f(row, 5) or 0.0
        amount = _f(row, 7) if len(row) > 7 else None
        if ts is None or o is None or h is None or lo is None or c is None:
            continue
        base = prev_close if prev_close is not None and prev_close != 0 else o
        change_amt = c - base
        change_pct = (change_amt / base * 100.0) if base else None
        amplitude = ((h - lo) / base * 100.0) if base else None
        bars.append(
            Bar(
                ts=ts,
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=vol,
                amount=amount,
                amplitude=amplitude,
                change_pct=change_pct,
                change_amount=change_amt,
                turnover_rate=0.0,
            )
        )
        prev_close = c
    return tuple(bars)


def quote_from_intraday(symbol: SymbolRef, points: tuple[IntradayPoint, ...]) -> Quote | MarketError:
    if not points:
        return empty_error("OKX 分时为空", provider=PROVIDER)
    first = points[0]
    last = points[-1]
    day_open = first.open if first.open != 0 else first.price
    high = max(p.high for p in points)
    low = min(p.low for p in points)
    total_vol = sum(p.volume for p in points)
    total_amt = sum(p.amount for p in points)
    chg_amt = last.price - day_open
    chg_pct = (chg_amt / day_open * 100.0) if day_open else None
    return Quote(
        symbol=symbol,
        price=last.price,
        open=day_open,
        high=high,
        low=low,
        prev_close=day_open,
        change_pct=round(chg_pct, 4) if chg_pct is not None else None,
        change_amount=round(chg_amt, 6),
        volume=total_vol,
        amount=total_amt,
        turnover_rate=0.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry="crypto",
        limit_up=None,
        limit_down=None,
        as_of=last.ts,
    )


def build_intraday_series(
    inst_id: str,
    candles: Sequence[object],
) -> IntradaySeries | MarketError:
    symbol = make_symbol(inst_id)
    points = candles_to_intraday_points(candles)
    if not points:
        return empty_error("OKX 分时解析为空", provider=PROVIDER)
    quote = quote_from_intraday(symbol, points)
    if isinstance(quote, MarketError):
        return quote
    return IntradaySeries(symbol=symbol, points=points, quote=quote)


def build_kline_series(
    inst_id: str,
    candles: Sequence[object],
    period: KlinePeriod,
) -> KlineSeries | MarketError:
    symbol = make_symbol(inst_id)
    bars = candles_to_bars(candles)
    if not bars:
        return empty_error("OKX K线解析为空", provider=PROVIDER)
    return KlineSeries(symbol=symbol, period=period, bars=bars, adjusted=False)


def quote_from_index_ticker(
    inst_id: str,
    *,
    price: float,
    open_24h: float | None,
    open_utc8: float | None,
) -> Quote:
    symbol = make_symbol(inst_id)
    base = open_utc8 if open_utc8 and open_utc8 != 0 else (open_24h if open_24h else price)
    chg = ((price - base) / base * 100.0) if base else None
    return Quote(
        symbol=symbol,
        price=price,
        open=base,
        high=None,
        low=None,
        prev_close=base,
        change_pct=round(chg, 4) if chg is not None else None,
        change_amount=round(price - base, 6) if base else None,
        volume=None,
        amount=None,
        turnover_rate=0.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry="crypto",
        limit_up=None,
        limit_down=None,
        as_of=None,
    )


def ensure_candle_list(payload: object) -> list[object] | MarketError:
    if not isinstance(payload, list):
        return parse_error("OKX candles 非列表", provider=PROVIDER)
    return payload
