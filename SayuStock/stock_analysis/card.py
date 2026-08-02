"""单股交易卡片数据装配。"""

from __future__ import annotations

import re
import asyncio
from typing import Any
from dataclasses import field, dataclass

from gsuid_core.logger import logger

from .universe import fetch_industry_pct_map
from .technical import TechnicalReport, build_technical_report
from ..utils.market import (
    KlinePeriod,
    get_market,
    kline_to_df,
    is_market_error,
)
from ..utils.market.models import FinancialSnapshot


@dataclass(slots=True)
class TradeCardData:
    name: str
    code: str
    secid: str
    price: float | None
    pct: float | None
    open_price: float | None
    prev_close: float | None
    amount: float | None
    turnover: float | None
    industry: str
    industry_pct: float | None
    technical: TechnicalReport | None
    finance: dict[str, Any] = field(default_factory=dict)
    pe: float | None = None
    pb: float | None = None
    mv: float | None = None
    high: float | None = None
    low: float | None = None


def _clean_name(name: str) -> str:
    return re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", name).strip() or name


def _fin_to_dict(snap: FinancialSnapshot) -> dict[str, Any]:
    return {
        "roe": snap.roe,
        "revenue_yoy": snap.revenue_yoy,
        "profit_yoy": snap.profit_yoy,
        "gross_margin": snap.gross_margin,
        "net_margin": snap.net_margin,
        "debt_ratio": snap.debt_ratio,
        "eps": snap.eps,
        "bps": snap.bps,
        "net_interest_margin": snap.net_interest_margin,
        "report_date": snap.report_date,
        "_industry_type": snap.industry_type,
        "_gap": list(snap.missing_fields),
    }


async def build_trade_card(query: str) -> TradeCardData | str:
    query = query.strip()
    if not query:
        return "❌请后跟股票代码或名称，例如：股票卡片 茅台"

    market = get_market()

    async def _spot() -> Any:
        return await market.quote(query)

    async def _kline() -> Any:
        return await market.kline(query, KlinePeriod.D1)

    async def _fin() -> dict[str, Any]:
        # 财务仅 A 股 6 位代码有意义
        q = await market.quote(query)
        if is_market_error(q):
            return {}
        code6 = q.symbol.code
        if not code6.isdigit() or len(code6) > 6:
            pure = code6.split(".")[-1]
            code6 = pure[-6:] if pure.isdigit() else pure
        if not code6.isdigit():
            return {}
        try:
            snap = await market.financial_snapshot(code6)
        except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
            logger.warning(f"[card] finance fail: {e}")
            return {}
        if is_market_error(snap):
            return {}
        return _fin_to_dict(snap)

    spot, kline, fin, ind_map = await asyncio.gather(
        _spot(),
        _kline(),
        _fin(),
        fetch_industry_pct_map(),
    )

    if is_market_error(spot):
        if is_market_error(kline):
            return spot.message
        # 仅有 K 线时仍可出卡片
        name = _clean_name(kline.symbol.name)
        code = kline.symbol.code
        secid = kline.symbol.provider_symbol
        price = kline.bars[-1].close if kline.bars else None
        pct = kline.bars[-1].change_pct if kline.bars else None
        open_price = kline.bars[-1].open if kline.bars else None
        prev_close = None
        amount = kline.bars[-1].amount if kline.bars else None
        turnover = kline.bars[-1].turnover_rate if kline.bars else None
        high = kline.bars[-1].high if kline.bars else None
        low = kline.bars[-1].low if kline.bars else None
        pe = None
        pb = None
        mv = None
        industry = "未分类"
    else:
        name = _clean_name(spot.symbol.name)
        code = spot.symbol.code
        secid = spot.symbol.provider_symbol
        price = spot.price
        pct = spot.change_pct
        open_price = spot.open
        prev_close = spot.prev_close
        amount = spot.amount
        turnover = spot.turnover_rate
        high = spot.high
        low = spot.low
        pe = spot.pe
        pb = spot.pb
        mv = spot.market_cap
        industry = spot.industry or "未分类"

    industry_pct = ind_map[industry] if industry in ind_map else None

    technical: TechnicalReport | None = None
    if not is_market_error(kline):
        df = kline_to_df(kline)
        rep = build_technical_report(
            name=_clean_name(kline.symbol.name) or name,
            code=kline.symbol.code or code,
            period_code="101",
            ohlcv_df=df,
        )
        if isinstance(rep, TechnicalReport):
            technical = rep

    return TradeCardData(
        name=name,
        code=code,
        secid=secid,
        price=price,
        pct=pct,
        open_price=open_price,
        prev_close=prev_close,
        amount=amount,
        turnover=turnover,
        industry=industry,
        industry_pct=industry_pct,
        technical=technical,
        finance=fin if isinstance(fin, dict) else {},
        pe=pe,
        pb=pb,
        mv=mv,
        high=high,
        low=low,
    )
