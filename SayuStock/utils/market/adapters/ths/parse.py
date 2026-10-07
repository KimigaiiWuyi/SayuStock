"""同花顺（扶摇）原始响应 → 领域模型（供应商字段仅本文件解析）。"""

from __future__ import annotations

from typing import Any, Mapping
from datetime import datetime, timezone, timedelta

from .._base import BJ_CODE_PREFIXES as _BJ_CODE_PREFIXES
from .client import PROVIDER
from ...enums import AssetClass
from ...errors import MarketError, parse_error
from ...models import Bar, Quote, SymbolRef

_BJ_TZ = timezone(timedelta(hours=8))

# 北交所代码段（东财 secid 前缀 0 下的特殊段）→ ths 后缀 .BJ


def _is_index_code(prefix: str, code: str) -> bool:
    """A 股指数代码形态：沪市 000xxx、深市 399xxx（个股沪市 6 开头、深市 000/003 开头）。

    解析层对部分指数返回空 sec_type → asset_class 误判 EQUITY，须叠加代码形态判定。
    """
    return (prefix == "1" and code.startswith("000")) or (prefix == "0" and code.startswith("399"))


def ths_symbol_from_secid(secid: str, asset_class: AssetClass) -> tuple[str, str] | None:
    """东财 secid + 资产类别 → (thscode, 端点类别 stock|index|fund)；不覆盖返回 None。

    扶摇只覆盖 A 股：个股（含北交所 .BJ）、A 股指数、场内基金（ETF/LOF）。
    美股/港股/韩股/场外基金（150.*，走天天基金槽）等一律返回 None 交给注册表回落。
    """
    prefix, _, code = secid.partition(".")
    if prefix == "1":
        suffix = "SH"
    elif prefix == "0":
        suffix = "BJ" if code.startswith(_BJ_CODE_PREFIXES) else "SZ"
    else:
        return None
    thscode = f"{code}.{suffix}"
    if asset_class == AssetClass.INDEX or _is_index_code(prefix, code):
        return thscode, "index"
    if asset_class == AssetClass.ETF:
        return thscode, "fund"
    if asset_class == AssetClass.EQUITY:
        return thscode, "stock"
    return None


def _ms_to_bj(ms: object) -> datetime | None:
    """毫秒 Unix 时间戳（东八区口径）→ 朴素北京时间。"""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=_BJ_TZ).replace(tzinfo=None)


def _num(item: Mapping[str, Any], key: str) -> float | None:
    raw = item.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def parse_snapshot_item(
    item: Mapping[str, Any],
    *,
    symbol: SymbolRef,
    timestamp_ms: object = None,
) -> Quote | MarketError:
    """快照 item（个股/指数/基金同构）→ Quote；快照无名称，沿用解析层名称。"""
    price = _num(item, "last_price")
    prev_close = _num(item, "prev_price")
    open_px = _num(item, "open_price")
    if price is None or price == 0.0:
        price = prev_close or open_px
    if price is None:
        return parse_error("同花顺快照缺少现价", provider=PROVIDER)
    return Quote(
        symbol=symbol,
        price=price,
        open=open_px,
        high=_num(item, "high_price"),
        low=_num(item, "low_price"),
        prev_close=prev_close,
        change_pct=_num(item, "price_change_ratio_pct"),
        change_amount=_num(item, "price_change"),
        volume=_num(item, "volume"),
        amount=_num(item, "turnover"),
        # 换手率仅场内基金快照返回（turnover_ratio_pct），个股/指数为 None
        turnover_rate=_num(item, "turnover_ratio_pct"),
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=_ms_to_bj(timestamp_ms),
    )


def parse_historical_bars(data: Mapping[str, Any]) -> list[Bar]:
    """historical data.item → Bar 列表（date_ms 为东八区交易日零点）。"""
    rows = data.get("item")
    if not isinstance(rows, list):
        return []
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ts = _ms_to_bj(row.get("date_ms"))
        open_px = _num(row, "open_price")
        high = _num(row, "high_price")
        low = _num(row, "low_price")
        close = _num(row, "close_price")
        if ts is None or open_px is None or high is None or low is None or close is None:
            continue
        volume = _num(row, "volume")
        bars.append(
            Bar(
                ts=ts,
                open=open_px,
                high=high,
                low=low,
                close=close,
                volume=volume if volume is not None else 0.0,
                amount=_num(row, "turnover"),
                amplitude=None,
                change_pct=None,
                change_amount=None,
                turnover_rate=None,
            )
        )
    return bars
