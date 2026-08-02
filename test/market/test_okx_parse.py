"""OKX 原生 candle → 领域模型。"""

from __future__ import annotations

from SayuStock.utils.market.enums import KlinePeriod
from SayuStock.utils.market.compat import kline_to_em_dict, quote_with_intraday_to_em
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.adapters.okx.parse import (
    candles_to_bars,
    build_kline_series,
    build_intraday_series,
    quote_from_index_ticker,
)

# ts_ms, o, h, l, c, vol, volCcy, volCcyQuote
_SAMPLE = [
    ["1722470400000", "60000", "61000", "59500", "60500", "10", "0", "600000"],
    ["1722470460000", "60500", "61200", "60400", "61000", "12", "0", "732000"],
    ["1722470520000", "61000", "61500", "60800", "61200", "8", "0", "489600"],
]


def test_candles_to_bars_and_kline_series() -> None:
    bars = candles_to_bars(_SAMPLE)
    assert len(bars) == 3
    assert bars[0].open == 60000.0
    assert bars[-1].close == 61200.0
    series = build_kline_series("BTC-USDT", _SAMPLE, KlinePeriod.D1)
    assert not is_market_error(series)
    assert series.symbol.code == "BTC-USDT"
    assert series.symbol.exchange == "OKX"
    em = kline_to_em_dict(series)
    assert "klines" in em["data"]
    assert len(em["data"]["klines"]) == 3


def test_intraday_and_quote() -> None:
    series = build_intraday_series("BTC-USDT", _SAMPLE)
    assert not is_market_error(series)
    assert len(series.points) == 3
    assert series.quote is not None
    assert series.quote.price == 61200.0
    assert series.quote.high == 61500.0
    assert series.quote.low == 59500.0
    em = quote_with_intraday_to_em(series)
    assert em["data"]["f43"] == 61200.0
    assert em["data"]["f170"] is not None
    assert len(em["trends"]) == 3


def test_index_ticker_quote() -> None:
    q = quote_from_index_ticker("BTC-USD", price=100.0, open_24h=90.0, open_utc8=95.0)
    assert q.price == 100.0
    assert q.prev_close == 95.0
    assert q.change_pct is not None
    assert abs(q.change_pct - round((100 - 95) / 95 * 100, 4)) < 1e-9
