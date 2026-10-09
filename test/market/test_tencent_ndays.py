"""腾讯分钟 K 拼五日分时：不打网络。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import Bar, SymbolRef, IntradaySeries
from SayuStock.utils.market.adapters.tencent.parse import (
    intraday_from_minute_bars,
    tencent_symbol_from_secid,
)


def _symbol() -> SymbolRef:
    return SymbolRef(
        code="600519",
        name="贵州茅台",
        asset_class=AssetClass.EQUITY,
        exchange="SH",
        provider_symbol="1.600519",
    )


def _bar(ts: datetime, close: float, amount: float | None) -> Bar:
    return Bar(
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        amount=amount,
        amplitude=None,
        change_pct=None,
        change_amount=None,
        turnover_rate=None,
    )


def test_symbol_maps_csi_unsupported_and_bj_index() -> None:
    # 2026-10-09 腾讯 qt 与检索都没有中证2000。
    assert tencent_symbol_from_secid("2.932000") is None
    assert tencent_symbol_from_secid("0.899050") == "bj899050"


def test_two_days_synthesize_quote_when_quote_missing() -> None:
    bars = [
        _bar(datetime(2026, 10, 7, 15, 0), 90.0, 1000.0),
        _bar(datetime(2026, 10, 8, 15, 0), 100.0, 1000.0),
        _bar(datetime(2026, 10, 9, 9, 31), 102.0, 500.0),
        _bar(datetime(2026, 10, 9, 15, 0), 104.0, 700.0),
    ]
    series = intraday_from_minute_bars(bars, symbol=_symbol(), quote=None, ndays=2)
    assert isinstance(series, IntradaySeries)
    assert series.ndays == 2
    assert series.ref_close == 90.0
    assert series.quote is not None
    assert series.quote.price == 104.0
    assert series.quote.change_pct == 4.0
    assert series.quote.amount == 1200.0
    assert series.quote.turnover_rate is None


def test_missing_amount_stays_none() -> None:
    bars = [
        _bar(datetime(2026, 10, 8, 15, 0), 100.0, None),
        _bar(datetime(2026, 10, 9, 15, 0), 101.0, None),
    ]
    series = intraday_from_minute_bars(bars, symbol=_symbol(), quote=None, ndays=5)
    assert isinstance(series, IntradaySeries)
    assert series.quote is not None
    assert series.quote.amount is None
    assert series.quote.change_pct == 1.0


def test_one_day_is_not_five_day() -> None:
    bars = [_bar(datetime(2026, 10, 9, 15, 0), 100.0, 1.0)]
    result = intraday_from_minute_bars(bars, symbol=_symbol(), quote=None, ndays=5)
    assert is_market_error(result)
    assert result.code == "empty"
