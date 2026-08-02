"""kline / board / finance 解析。"""

from __future__ import annotations

import json
from pathlib import Path

from SayuStock.utils.market.enums import BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.compat import board_to_em_dict, kline_to_em_dict
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import SymbolRef
from SayuStock.utils.market.convert.dataframe import board_to_df, kline_to_df
from SayuStock.utils.market.adapters.eastmoney.parse_board import parse_board_payload
from SayuStock.utils.market.adapters.eastmoney.parse_kline import parse_kline_payload
from SayuStock.utils.market.adapters.eastmoney.parse_finance import parse_financial_snapshot_payload

FIX = Path(__file__).parent / "fixtures"


def _sym() -> SymbolRef:
    return SymbolRef(
        code="600519",
        name="贵州茅台",
        asset_class=AssetClass.EQUITY,
        exchange="SSE",
        provider_symbol="1.600519",
    )


def test_kline_parse_and_roundtrip() -> None:
    payload = json.loads((FIX / "kline_600519.json").read_text(encoding="utf-8"))
    series = parse_kline_payload(payload, symbol=_sym(), period=KlinePeriod.D1)
    assert not is_market_error(series)
    assert len(series.bars) == 3
    assert series.bars[-1].close == 1650.0
    assert series.symbol.name == "贵州茅台"
    df = kline_to_df(series)
    assert list(df["close"])[-1] == 1650.0
    em = kline_to_em_dict(series)
    assert "klines" in em["data"]
    assert len(em["data"]["klines"]) == 3


def test_board_parse() -> None:
    payload = json.loads((FIX / "board_clist.json").read_text(encoding="utf-8"))
    snap = parse_board_payload(payload, kind=BoardKind.A_SHARE, title="沪深A")
    assert not is_market_error(snap)
    assert len(snap.rows) == 2
    assert snap.rows[0].code == "600519"
    assert snap.rows[0].change_pct == 1.82
    df = board_to_df(snap)
    assert len(df) == 2
    em = board_to_em_dict(snap)
    assert em["data"]["total"] == 2
    assert em["data"]["diff"][0]["f12"] == "600519"


def test_finance_snapshot() -> None:
    rows = json.loads((FIX / "finance_main.json").read_text(encoding="utf-8"))
    snap = parse_financial_snapshot_payload("600519", rows)
    assert not is_market_error(snap)
    assert snap.roe == 30.5
    assert snap.eps == 55.2
    assert snap.industry_type == "standard"
