"""stock/get 原始 JSON → Quote。"""

from __future__ import annotations

from typing import Mapping

from ...enums import AssetClass
from ...errors import MarketError, empty_error, parse_error
from ...models import Quote, SymbolRef
from .json_util import opt_str, opt_float, as_mapping, require_mapping
from .map_fields import PROVIDER, QUOTE_FIELD


def _asset_class(sec_type: str) -> AssetClass:
    t = sec_type.lower()
    if "etf" in t or "基金" in sec_type:
        return AssetClass.ETF
    if "指数" in sec_type or "index" in t:
        return AssetClass.INDEX
    if "期货" in sec_type or "future" in t:
        return AssetClass.FUTURE
    if "债" in sec_type or "bond" in t:
        return AssetClass.BOND
    if "港" in sec_type or "hk" in t:
        return AssetClass.EQUITY
    return AssetClass.EQUITY


def _exchange(provider_symbol: str, sec_type: str) -> str:
    if provider_symbol.startswith("1."):
        return "SSE"
    if provider_symbol.startswith("0."):
        return "SZSE"
    if provider_symbol.startswith("116.") or "港" in sec_type:
        return "HKEX"
    if provider_symbol.startswith("105.") or "美" in sec_type:
        return "US"
    return "EM"


def _last_trend_price(root: Mapping[str, object]) -> float | None:
    """从 payload.trends 取最后一个有效价（get_single_stock 会附带分时）。"""
    trends = root.get("trends")
    if not isinstance(trends, list) or not trends:
        return None
    for item in reversed(trends):
        if isinstance(item, dict):
            raw = item.get("price")
        elif isinstance(item, str):
            parts = item.split(",")
            raw = parts[1] if len(parts) > 1 else None
        else:
            continue
        if raw is None or raw == "" or raw == "-":
            continue
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value == value:  # not NaN
            return value
    return None


def parse_quote_payload(
    payload: object,
    *,
    provider_symbol: str,
    sec_type: str = "",
) -> Quote | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("quote payload 非对象", provider=PROVIDER)
    data = require_mapping(root, "data")
    if data is None:
        return empty_error("quote data 为空", provider=PROVIDER)

    # 休市/连续合约未刷新时东财常把 f43 置为 "-"；回退昨收/开盘，避免整页失败。
    prev_close = opt_float(data, QUOTE_FIELD["prev_close"])
    open_px = opt_float(data, QUOTE_FIELD["open"])
    price = opt_float(data, QUOTE_FIELD["price"])
    if price is None:
        price = prev_close
    if price is None:
        price = open_px
    if price is None:
        price = _last_trend_price(root)
    if price is None:
        return parse_error("缺少现价 f43", provider=PROVIDER)

    code = opt_str(data, QUOTE_FIELD["code"]) or provider_symbol.split(".")[-1]
    name_raw = opt_str(data, QUOTE_FIELD["name"]) or code
    # get_single_stock 可能把 " (sec_type)" 拼进名称
    name = name_raw.split(" (")[0].strip() if " (" in name_raw else name_raw

    pe = opt_float(data, QUOTE_FIELD["pe"])
    if pe is None:
        pe = opt_float(data, QUOTE_FIELD["pe_dyn"])
    pb = opt_float(data, QUOTE_FIELD["pb"])
    if pb is None:
        pb = opt_float(data, QUOTE_FIELD["pb_alt"])
    industry = opt_str(data, QUOTE_FIELD["industry"])
    if industry is None:
        industry = opt_str(data, QUOTE_FIELD["industry_alt"])

    symbol = SymbolRef(
        code=code,
        name=name,
        asset_class=_asset_class(sec_type or name_raw),
        exchange=_exchange(provider_symbol, sec_type),
        provider_symbol=provider_symbol,
    )
    return Quote(
        symbol=symbol,
        price=price,
        open=open_px,
        high=opt_float(data, QUOTE_FIELD["high"]),
        low=opt_float(data, QUOTE_FIELD["low"]),
        prev_close=prev_close,
        change_pct=opt_float(data, QUOTE_FIELD["change_pct"]),
        change_amount=opt_float(data, QUOTE_FIELD["change_amount"]),
        volume=opt_float(data, QUOTE_FIELD["volume"]),
        amount=opt_float(data, QUOTE_FIELD["amount"]),
        turnover_rate=opt_float(data, QUOTE_FIELD["turnover_rate"]),
        pe=pe,
        pb=pb,
        market_cap=opt_float(data, QUOTE_FIELD["market_cap"]),
        float_market_cap=opt_float(data, QUOTE_FIELD["float_market_cap"]),
        industry=industry,
        limit_up=opt_float(data, QUOTE_FIELD["limit_up"]),
        limit_down=opt_float(data, QUOTE_FIELD["limit_down"]),
        as_of=None,
    )


def is_sector_quote(payload: object) -> bool:
    root = as_mapping(payload)
    if root is None:
        return False
    data = require_mapping(root, "data")
    if data is None:
        return False
    flag = opt_float(data, QUOTE_FIELD["sec_type_flag"])
    return flag == 90.0
