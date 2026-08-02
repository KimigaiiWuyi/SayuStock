"""quote / intraday 解析与字段语义。"""

from __future__ import annotations

import json
from pathlib import Path

from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.adapters.eastmoney.parse_quote import parse_quote_payload
from SayuStock.utils.market.adapters.eastmoney.parse_intraday import (
    extract_trends_from_payload,
    parse_intraday_from_trends_list,
)

FIX = Path(__file__).parent / "fixtures"


def test_quote_maps_f45_to_low_not_change_pct() -> None:
    payload = json.loads((FIX / "quote_600519.json").read_text(encoding="utf-8"))
    q = parse_quote_payload(payload, provider_symbol="1.600519", sec_type="沪深A")
    assert not is_market_error(q)
    assert q.price == 1680.0
    assert q.low == 1660.0  # f45 = 最低
    assert q.change_pct == 1.82  # f170
    assert q.symbol.code == "600519"
    assert q.symbol.name == "贵州茅台"
    assert q.prev_close == 1650.0
    assert q.industry == "白酒"


def test_intraday_from_payload_trends() -> None:
    payload = json.loads((FIX / "quote_600519.json").read_text(encoding="utf-8"))
    q = parse_quote_payload(payload, provider_symbol="1.600519", sec_type="沪深A")
    assert not is_market_error(q)
    trends = extract_trends_from_payload(payload)
    series = parse_intraday_from_trends_list(trends, q.symbol, q)
    assert not is_market_error(series)
    assert len(series.points) == 2
    assert series.points[-1].price == 1680.0
    assert series.points[0].ts.year == 2026
