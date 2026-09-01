"""OKX MarketDataPort：原生 candle → 领域模型。"""

from __future__ import annotations

from datetime import date

from .parse import (
    make_symbol,
    build_kline_series,
    build_intraday_series,
    quote_from_index_ticker,
)
from .._base import PartialMarketData
from .client import (
    normalize_inst_id,
    fetch_index_ticker,
    fetch_history_candles,
    fetch_today_1m_candles,
)
from ...enums import KlinePeriod
from ...errors import MarketError, not_found, unsupported
from ...models import Quote, SymbolRef, KlineSeries, IntradaySeries

PROVIDER = "okx"


def is_crypto_query(query: str) -> bool:
    return normalize_inst_id(query) is not None


class OkxMarketData(PartialMarketData):
    provider_name = PROVIDER

    async def resolve(self, query: str) -> SymbolRef | None:
        inst_id = normalize_inst_id(query)
        if inst_id is None:
            return None
        return make_symbol(inst_id)

    async def quote(self, query: str) -> Quote | MarketError:
        inst_id = normalize_inst_id(query)
        if inst_id is None:
            return not_found("非加密货币标的", provider=PROVIDER)
        # 优先用今日 1m 聚合（含 high/low/amount）；失败再降级 index-ticker
        candles = await fetch_today_1m_candles(inst_id)
        if not isinstance(candles, MarketError):
            series = build_intraday_series(inst_id, candles)
            if not isinstance(series, MarketError) and series.quote is not None:
                return series.quote
        ticker = await fetch_index_ticker(inst_id)
        if isinstance(ticker, MarketError):
            return ticker
        return quote_from_index_ticker(
            inst_id,
            price=ticker["price"],
            open_24h=ticker["open_24h"],
            open_utc8=ticker["open_utc8"],
        )

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        if ndays != 1:
            return unsupported("该市场不支持五日分时，请使用 个股 xxx 查看当日分时", provider=PROVIDER)
        inst_id = normalize_inst_id(query)
        if inst_id is None:
            return not_found("非加密货币标的", provider=PROVIDER)
        candles = await fetch_today_1m_candles(inst_id)
        if isinstance(candles, MarketError):
            return candles
        return build_intraday_series(inst_id, candles)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        inst_id = normalize_inst_id(query)
        if inst_id is None:
            return not_found("非加密货币标的", provider=PROVIDER)
        candles = await fetch_history_candles(inst_id, period, start=start, end=end)
        if isinstance(candles, MarketError):
            return candles
        return build_kline_series(inst_id, candles, period)
