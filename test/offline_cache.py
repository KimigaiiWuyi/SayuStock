"""从本地东财缓存 JSON 组装 Quote / IntradaySeries / Board，不发起网络。"""

from __future__ import annotations

import json
from pathlib import Path

from SayuStock.utils.market import DisplayItem, board_rows_to_items
from SayuStock.utils.constant import PREFIX_DATA
from SayuStock.utils.market.enums import BoardKind
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import Quote, IntradaySeries
from SayuStock.utils.market.adapters.eastmoney.parse_board import parse_board_payload
from SayuStock.utils.market.adapters.eastmoney.parse_quote import parse_quote_payload
from SayuStock.utils.market.adapters.eastmoney.parse_intraday import (
    extract_trends_from_payload,
    parse_intraday_from_trends_list,
)

_DEFAULT_CACHE = Path(r"F:\gsuid_core\data\SayuStock\data")


def cache_dir() -> Path | None:
    if _DEFAULT_CACHE.is_dir():
        return _DEFAULT_CACHE
    try:
        from SayuStock.utils.resource_path import DATA_PATH
    except ImportError:
        return None
    if DATA_PATH.is_dir():
        return DATA_PATH
    return None


def _sec_type(secid: str) -> str:
    prefix = secid.split(".")[0]
    if prefix in PREFIX_DATA:
        return PREFIX_DATA[prefix]
    return ""


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_series(data_dir: Path, secid: str) -> IntradaySeries | None:
    path = data_dir / f"{secid}_single-stock_None_data.json"
    if not path.is_file():
        trends_path = data_dir / f"{secid}_single-stock-trends-1_None_data.json"
        if not trends_path.is_file():
            return None
        trends = load_json(trends_path)
        from SayuStock.utils.market.enums import AssetClass
        from SayuStock.utils.market.models import SymbolRef

        symbol = SymbolRef(
            code=secid.split(".")[-1],
            name=secid,
            asset_class=AssetClass.OTHER,
            exchange="EM",
            provider_symbol=secid,
            sec_type=_sec_type(secid),
        )
        parsed = parse_intraday_from_trends_list(trends, symbol, None, ndays=1)
        if is_market_error(parsed):
            return None
        return parsed
    raw = load_json(path)
    quote = parse_quote_payload(raw, provider_symbol=secid, sec_type=_sec_type(secid))
    if is_market_error(quote):
        quote_ok: Quote | None = None
        from SayuStock.utils.market.enums import AssetClass
        from SayuStock.utils.market.models import SymbolRef

        symbol = SymbolRef(
            code=secid.split(".")[-1],
            name=secid,
            asset_class=AssetClass.OTHER,
            exchange="EM",
            provider_symbol=secid,
            sec_type=_sec_type(secid),
        )
    else:
        quote_ok = quote
        symbol = quote.symbol
    trends = extract_trends_from_payload(raw)
    if trends is None:
        return None
    parsed = parse_intraday_from_trends_list(trends, symbol, quote_ok, ndays=1)
    if is_market_error(parsed):
        return None
    return parsed


def load_board_items(data_dir: Path, title: str) -> list[DisplayItem]:
    path = data_dir / f"{title}_0_False-100_data.json"
    if not path.is_file():
        return []
    raw = load_json(path)
    kind = BoardKind.INTERNATIONAL if title == "国际市场" else BoardKind.INDEX
    snap = parse_board_payload(raw, kind=kind, title=title)
    if is_market_error(snap):
        return []
    return board_rows_to_items(snap.rows)
