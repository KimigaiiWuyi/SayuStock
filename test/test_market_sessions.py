"""各市场 BJT 开收盘边界：亮灯用 [start, end)，收盘整点已关。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.time_range import (
    Market,
    get_market_sessions,
    is_market_active_now,
)


def _dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 24, hour, minute, second)


def test_a_share_off_at_official_close() -> None:
    code = "1.000001"
    assert is_market_active_now(code, datetime(2026, 8, 24, 9, 29, 59)) is False
    assert is_market_active_now(code, _dt(9, 30)) is True
    assert is_market_active_now(code, datetime(2026, 8, 24, 11, 29, 59)) is True
    assert is_market_active_now(code, _dt(11, 30)) is False
    assert is_market_active_now(code, _dt(12, 0)) is False
    assert is_market_active_now(code, _dt(13, 0)) is True
    assert is_market_active_now(code, datetime(2026, 8, 24, 14, 59, 59)) is True
    assert is_market_active_now(code, _dt(15, 0)) is False
    assert is_market_active_now(code, datetime(2026, 8, 24, 15, 0, 1)) is False
    assert is_market_active_now(code, _dt(15, 30)) is False


def test_hk_close_is_1600_not_1530() -> None:
    code = "100.HSI"
    assert is_market_active_now(code, _dt(12, 0)) is False
    assert is_market_active_now(code, _dt(13, 0)) is True
    assert is_market_active_now(code, _dt(15, 0)) is True
    assert is_market_active_now(code, _dt(15, 30)) is True
    assert is_market_active_now(code, datetime(2026, 8, 24, 15, 59, 59)) is True
    assert is_market_active_now(code, _dt(16, 0)) is False


def test_jp_has_lunch_and_closes_1430_bjt() -> None:
    code = "100.N225"
    assert get_market_sessions(Market.JP_INDEX, _dt(10, 0)) == [
        ("08:00", "10:30"),
        ("11:30", "14:30"),
    ]
    assert is_market_active_now(code, _dt(8, 0)) is True
    assert is_market_active_now(code, _dt(10, 30)) is False
    assert is_market_active_now(code, _dt(11, 0)) is False
    assert is_market_active_now(code, _dt(11, 30)) is True
    assert is_market_active_now(code, datetime(2026, 8, 24, 14, 29, 59)) is True
    assert is_market_active_now(code, _dt(14, 30)) is False
    assert is_market_active_now(code, _dt(15, 30)) is False


def test_kr_closes_1430_bjt() -> None:
    code = "100.KOSPI200"
    assert is_market_active_now(code, _dt(8, 0)) is True
    assert is_market_active_now(code, datetime(2026, 8, 24, 14, 29, 59)) is True
    assert is_market_active_now(code, _dt(14, 30)) is False
    assert is_market_active_now(code, _dt(15, 30)) is False


def test_eu_and_us_dst_bounds() -> None:
    sxxp = "100.SXXP"
    ndx = "100.NDX"
    assert is_market_active_now(sxxp, datetime(2026, 8, 24, 14, 59, 59)) is False
    assert is_market_active_now(sxxp, _dt(15, 0)) is True
    assert is_market_active_now(sxxp, datetime(2026, 8, 24, 23, 29, 59)) is True
    assert is_market_active_now(sxxp, _dt(23, 30)) is False
    assert is_market_active_now(ndx, _dt(10, 0)) is False
    assert is_market_active_now(ndx, _dt(21, 30)) is True
    tue_close = datetime(2026, 8, 25, 4, 0)
    assert is_market_active_now(ndx, datetime(2026, 8, 25, 3, 59, 59)) is True
    assert is_market_active_now(ndx, tue_close) is False
    winter = datetime(2026, 1, 15, 16, 0)
    assert is_market_active_now(sxxp, winter) is True
    assert is_market_active_now(sxxp, datetime(2026, 1, 16, 0, 30)) is False
    assert is_market_active_now(ndx, datetime(2026, 1, 15, 22, 30)) is True
    assert is_market_active_now(ndx, datetime(2026, 1, 16, 5, 0)) is False


def test_weekend_regular_markets_off_crypto_on() -> None:
    saturday = datetime(2026, 8, 22, 10, 0)
    sunday = datetime(2026, 8, 23, 15, 0)
    for code in ("1.000001", "100.HSI", "100.N225", "100.KOSPI200", "100.SXXP", "100.NDX"):
        assert is_market_active_now(code, saturday) is False, code
        assert is_market_active_now(code, sunday) is False, code
    assert is_market_active_now("BTC", saturday) is True
    assert is_market_active_now("BTC", sunday) is True
    us_sat_tail = datetime(2026, 8, 22, 2, 0)
    assert is_market_active_now("100.NDX", us_sat_tail) is True


def test_ca_index_follows_us_equity_hours() -> None:
    summer = get_market_sessions(Market.CA_INDEX, _dt(12, 0))
    winter = get_market_sessions(Market.CA_INDEX, datetime(2026, 1, 15, 12, 0))
    assert summer == get_market_sessions(Market.US_STOCK, _dt(12, 0))
    assert winter == get_market_sessions(Market.US_STOCK, datetime(2026, 1, 15, 12, 0))
