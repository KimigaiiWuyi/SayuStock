"""腾讯原始响应 → 领域模型（供应商字段仅本文件解析）。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from datetime import datetime

from .client import PROVIDER
from ...enums import KlinePeriod
from ...errors import MarketError, empty_error, parse_error
from ...models import Bar, Quote, SymbolRef, KlineSeries, IntradayPoint, IntradaySeries

# 东财 100.* 美股指数 → 腾讯符号（注意东财 NDX 实为纳斯达克综合，对应 .IXIC）
_US_INDEX_SYMBOLS: dict[str, str] = {
    "SPX": "usINX",
    "DJIA": "usDJI",
    "NDX": "usIXIC",
}


def tencent_symbol_from_secid(secid: str) -> str | None:
    """东财 secid → 腾讯符号：1.600519→sh600519，105.QQQ→usQQQ，100.SPX→usINX。"""
    if "." not in secid:
        return None
    prefix, code = secid.split(".", 1)
    if prefix == "1":
        return f"sh{code}"
    if prefix == "0":
        return f"sz{code}"
    if prefix in ("105", "106", "107", "153"):
        # 美股纳斯达克/纽交所/美交所/粉单；腾讯仅盘口可用（K线/分时无数据）
        return f"us{code}"
    if prefix == "100":
        # 美股指数：盘口可用，代码与东财不同名（SPX→INX、DJIA→DJI、NDX→IXIC）
        return _US_INDEX_SYMBOLS.get(code.upper())
    return None


def _f(parts: Sequence[str], idx: int) -> float | None:
    if idx >= len(parts):
        return None
    raw = parts[idx].strip()
    if not raw or raw == "-":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_qt_line(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """qt.gtimg.cn ~ 分隔行 → Quote。

    列序（~ 分隔）：1 名称, 2 代码, 3 现价, 4 昨收, 5 今开, 30 时间,
    31 涨跌, 32 涨跌%, 33 最高, 34 最低, 36 量(手), 37 额(万), 38 换手%,
    39 PE, 44 流通市值(亿), 45 总市值(亿), 46 PB, 47 涨停, 48 跌停。
    """
    parts = line.split("~")
    if len(parts) < 38:
        return parse_error("腾讯盘口字段不足", provider=PROVIDER)
    name = parts[1].strip() or symbol.name
    price = _f(parts, 3)
    prev_close = _f(parts, 4)
    open_px = _f(parts, 5)
    if price is None or price == 0.0:
        price = prev_close or open_px
    if price is None:
        return parse_error("腾讯盘口缺少现价", provider=PROVIDER)
    as_of = None
    raw_ts = parts[30].strip() if len(parts) > 30 else ""
    if len(raw_ts) >= 14 and raw_ts[:14].isdigit():
        try:
            as_of = datetime.strptime(raw_ts[:14], "%Y%m%d%H%M%S")
        except ValueError:
            as_of = None
    volume_hand = _f(parts, 36)
    amount_wan = _f(parts, 37)
    float_cap_yi = _f(parts, 44)
    total_cap_yi = _f(parts, 45)
    return Quote(
        symbol=SymbolRef(
            code=symbol.code,
            name=name,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=symbol.sec_type,
        ),
        price=price,
        open=open_px,
        high=_f(parts, 33),
        low=_f(parts, 34),
        prev_close=prev_close,
        change_pct=_f(parts, 32),
        change_amount=_f(parts, 31),
        volume=volume_hand * 100.0 if volume_hand is not None else None,
        amount=amount_wan * 10000.0 if amount_wan is not None else None,
        turnover_rate=_f(parts, 38),
        pe=_f(parts, 39),
        pb=_f(parts, 46),
        market_cap=total_cap_yi * 100000000.0 if total_cap_yi is not None else None,
        float_market_cap=float_cap_yi * 100000000.0 if float_cap_yi is not None else None,
        industry=None,
        limit_up=_f(parts, 47),
        limit_down=_f(parts, 48),
        as_of=as_of,
    )


def parse_qt_line_us(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """qt.gtimg.cn 美股 ~ 分隔行 → Quote。

    与 A 股同源的核心列序一致：1 名称, 2 代码.后缀, 3 现价, 4 昨收, 5 今开,
    30 时间(美东 "YYYY-MM-DD HH:MM:SS"), 31 涨跌, 32 涨跌%, 33 最高, 34 最低,
    35 币种, 36 量(股), 37 额(币种元)。差异：38 换手%, 39 PE, 43 PB,
    44/45 流通/总市值(亿), 46 英文名；美股无涨跌停。量额单位与东财美股口径
    一致（股/元），不做手/万换算。
    """
    parts = line.split("~")
    if len(parts) < 38:
        return parse_error("腾讯美股盘口字段不足", provider=PROVIDER)
    name = parts[1].strip() or symbol.name
    price = _f(parts, 3)
    prev_close = _f(parts, 4)
    open_px = _f(parts, 5)
    if price is None or price == 0.0:
        price = prev_close or open_px
    if price is None:
        return parse_error("腾讯美股盘口缺少现价", provider=PROVIDER)
    float_cap_yi = _f(parts, 44)
    total_cap_yi = _f(parts, 45)
    return Quote(
        symbol=SymbolRef(
            code=symbol.code,
            name=name,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=symbol.sec_type,
        ),
        price=price,
        open=open_px,
        high=_f(parts, 33),
        low=_f(parts, 34),
        prev_close=prev_close,
        change_pct=_f(parts, 32),
        change_amount=_f(parts, 31),
        volume=_f(parts, 36),
        amount=_f(parts, 37),
        turnover_rate=_f(parts, 38),
        pe=_f(parts, 39),
        pb=_f(parts, 43),
        market_cap=total_cap_yi * 100000000.0 if total_cap_yi is not None else None,
        float_market_cap=float_cap_yi * 100000000.0 if float_cap_yi is not None else None,
        industry=None,
        limit_up=None,
        limit_down=None,
        # 东财主源美股 Quote 也不带 as_of；腾讯美股时间戳为美东时间，保持一致置空
        as_of=None,
    )


def _parse_bar_ts(raw: str) -> datetime | None:
    text = raw.strip()
    for fmt in ("%Y%m%d%H%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    if " " in text:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None


def _bars_from_rows(rows: Sequence[Any]) -> list[Bar]:
    """腾讯 K 行 [时间,开,收,高,低,量(手),...] → Bar。注意列序为开、收、高、低。"""
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        ts = _parse_bar_ts(str(row[0]))
        if ts is None:
            continue
        try:
            open_px = float(row[1])
            close = float(row[2])
            high = float(row[3])
            low = float(row[4])
            volume_hand = float(row[5])
        except (TypeError, ValueError):
            continue
        bars.append(
            Bar(
                ts=ts,
                open=open_px,
                high=high,
                low=low,
                close=close,
                volume=volume_hand * 100.0,
                amount=None,
                amplitude=None,
                change_pct=None,
                change_amount=None,
                turnover_rate=None,
            )
        )
    return bars


def _payload_rows(payload: object, symbol_key: str, unit: str) -> list[Any] | MarketError:
    """取 data.<sym>.qfq<unit>（个股，前复权）或 data.<sym>.<unit>（指数）。"""
    if not isinstance(payload, Mapping):
        return parse_error("腾讯K线 payload 非对象", provider=PROVIDER)
    data = payload.get("data")
    if not isinstance(data, Mapping) or symbol_key not in data:
        return empty_error("腾讯K线 data 为空", provider=PROVIDER)
    node = data[symbol_key]
    if not isinstance(node, Mapping):
        return parse_error("腾讯K线节点非对象", provider=PROVIDER)
    for key in (f"qfq{unit}", unit):
        rows = node.get(key)
        if isinstance(rows, list) and rows:
            return rows
    return empty_error(f"腾讯K线缺少 {unit} 行", provider=PROVIDER)


def parse_kline_payload(
    payload: object,
    *,
    symbol: SymbolRef,
    tencent_symbol: str,
    period: KlinePeriod,
    unit: str,
    adjusted: bool,
) -> KlineSeries | MarketError:
    rows = _payload_rows(payload, tencent_symbol, unit)
    if isinstance(rows, MarketError):
        return rows
    bars = _bars_from_rows(rows)
    if not bars:
        return empty_error("腾讯K线解析后为空", provider=PROVIDER)
    return KlineSeries(symbol=symbol, period=period, bars=tuple(bars), adjusted=adjusted)


def _minute_rows(payload: object, tencent_symbol: str) -> list[str] | MarketError:
    if not isinstance(payload, Mapping):
        return parse_error("腾讯分时 payload 非对象", provider=PROVIDER)
    data = payload.get("data")
    if not isinstance(data, Mapping) or tencent_symbol not in data:
        return empty_error("腾讯分时 data 为空", provider=PROVIDER)
    node = data[tencent_symbol]
    if not isinstance(node, Mapping):
        return parse_error("腾讯分时节点非对象", provider=PROVIDER)
    inner = node.get("data")
    if not isinstance(inner, Mapping):
        return parse_error("腾讯分时缺少 data", provider=PROVIDER)
    rows = inner.get("data")
    if not isinstance(rows, list) or not rows:
        return empty_error("腾讯分时为空", provider=PROVIDER)
    return [r for r in rows if isinstance(r, str)]


def parse_minute_payload(
    payload: object,
    *,
    symbol: SymbolRef,
    tencent_symbol: str,
    quote: Quote | None,
    trade_date: str,
) -> IntradaySeries | MarketError:
    """分时 "HHMM price 累计量(手) 累计额(元)" → IntradaySeries（分钟差分）。"""
    rows = _minute_rows(payload, tencent_symbol)
    if isinstance(rows, MarketError):
        return rows
    points: list[IntradayPoint] = []
    open_px: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    prev_cum_vol = 0.0
    prev_cum_amount = 0.0
    cum_vol = 0.0
    cum_amount = 0.0
    for row in rows:
        parts = row.split()
        if len(parts) < 4:
            continue
        hhmm = parts[0].strip()
        if len(hhmm) != 4 or not hhmm.isdigit():
            continue
        try:
            price = float(parts[1])
            cum_vol = float(parts[2]) * 100.0  # 手 → 股
            cum_amount = float(parts[3])
        except ValueError:
            continue
        if price <= 0:
            continue
        try:
            ts = datetime.strptime(f"{trade_date} {hhmm[:2]}:{hhmm[2:]}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        minute_vol = max(0.0, cum_vol - prev_cum_vol)
        minute_amount = max(0.0, cum_amount - prev_cum_amount)
        prev_cum_vol, prev_cum_amount = cum_vol, cum_amount
        if open_px is None:
            open_px = price
        day_high = price if day_high is None else max(day_high, price)
        day_low = price if day_low is None else min(day_low, price)
        avg_price = cum_amount / cum_vol if cum_vol > 0 else price
        points.append(
            IntradayPoint(
                ts=ts,
                price=price,
                open=open_px,
                high=day_high,
                low=day_low,
                volume=minute_vol,
                amount=minute_amount,
                avg_price=avg_price,
            )
        )
    if not points:
        return empty_error("腾讯分时解析后为空", provider=PROVIDER)
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=quote, ndays=1)
