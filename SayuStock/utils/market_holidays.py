"""各市场休市日（不含周末）。按当地日历、按年份即时生成，不必每年手改。

优先用 ``holidays`` 库（A 股含国务院调休、美股用 NYSE 日历）。
未安装时仅保留外汇元旦/圣诞的固定月日判断。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
from functools import lru_cache

from .time_range import Market

_BJT = ZoneInfo("Asia/Shanghai")
_NY = ZoneInfo("America/New_York")
_LON = ZoneInfo("Europe/London")
_TKY = ZoneInfo("Asia/Tokyo")
_SEL = ZoneInfo("Asia/Seoul")

try:
    import holidays as _holidays
except ImportError:  # pragma: no cover
    _holidays = None

_MARKET_CAL: dict[Market, str] = {
    Market.A_SHARE: "CN",
    Market.BOND: "CN",
    Market.CN_FUTURE_DAY: "CN",
    Market.CN_FUTURE_NIGHT: "CN",
    Market.TLM: "CN",
    Market.SPOT: "CN",
    Market.HK_STOCK: "HK",
    Market.US_STOCK: "US",
    Market.US_FUTURE: "US",
    Market.COMMODITY: "US",
    Market.COMMODITY_SPOT: "US",
    Market.CA_INDEX: "US",
    Market.LATAM_INDEX: "US",
    Market.JP_INDEX: "JP",
    Market.KR_INDEX: "KR",
    Market.KR_STOCK: "KR",
    Market.EU_INDEX: "EU",
    Market.FX: "FX",
}


def _as_bjt(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=_BJT)
    return now.astimezone(_BJT)


def holiday_local_date(market: Market, now_bjt: datetime) -> date:
    aware = _as_bjt(now_bjt)
    if market in {
        Market.US_STOCK,
        Market.US_FUTURE,
        Market.COMMODITY,
        Market.COMMODITY_SPOT,
        Market.CA_INDEX,
        Market.LATAM_INDEX,
        Market.FX,
    }:
        return aware.astimezone(_NY).date()
    if market == Market.EU_INDEX:
        return aware.astimezone(_LON).date()
    if market == Market.JP_INDEX:
        return aware.astimezone(_TKY).date()
    if market in {Market.KR_INDEX, Market.KR_STOCK}:
        return aware.astimezone(_SEL).date()
    return aware.date()


@lru_cache(maxsize=64)
def _holiday_dates(cal_id: str, year: int) -> frozenset[str]:
    if _holidays is None:
        return frozenset()
    if cal_id == "US":
        cal = _holidays.NYSE(years=year)
    elif cal_id == "EU":
        cal = _holidays.country_holidays("GB", years=year)
        cal.update(_holidays.country_holidays("DE", years=year))
    else:
        cal = _holidays.country_holidays(cal_id, years=year)
    return frozenset(day.isoformat() for day in cal)


def is_market_holiday(market: Market, now_bjt: datetime) -> bool:
    """该市场在 now 对应的当地日历日是否因节假日休市（不含周末）。"""
    if market in {Market.CRYPTO, Market.UNKNOWN}:
        return False
    local = holiday_local_date(market, now_bjt)
    if market == Market.FX:
        return (local.month, local.day) in {(1, 1), (12, 25)}
    cal_id = _MARKET_CAL.get(market)
    if not cal_id:
        return False
    return local.isoformat() in _holiday_dates(cal_id, local.year)
