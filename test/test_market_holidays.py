"""休市日日历：外汇固定月日不依赖 holidays；A 股/美股要装库。"""

from __future__ import annotations

from datetime import datetime

import pytest

from SayuStock.utils.time_range import Market
from SayuStock.utils.market_holidays import is_market_holiday


def test_crypto_is_never_a_holiday() -> None:
    assert is_market_holiday(Market.CRYPTO, datetime(2026, 1, 1, 10, 0)) is False
    assert is_market_holiday(Market.CRYPTO, datetime(2026, 10, 1, 10, 0)) is False


def test_unmapped_market_is_not_a_holiday() -> None:
    assert is_market_holiday(Market.SG_FUTURE, datetime(2026, 10, 1, 10, 0)) is False


def test_fx_closes_on_new_year_and_christmas_without_holidays_lib() -> None:
    assert is_market_holiday(Market.FX, datetime(2026, 1, 1, 20, 0)) is True
    assert is_market_holiday(Market.FX, datetime(2026, 12, 25, 20, 0)) is True
    assert is_market_holiday(Market.FX, datetime(2026, 8, 24, 10, 0)) is False


def test_cn_national_day_and_us_memorial_day() -> None:
    pytest.importorskip("holidays")
    assert is_market_holiday(Market.A_SHARE, datetime(2026, 10, 1, 10, 0)) is True
    assert is_market_holiday(Market.A_SHARE, datetime(2026, 8, 24, 10, 0)) is False
    assert is_market_holiday(Market.US_STOCK, datetime(2026, 5, 25, 23, 0)) is True
    assert is_market_holiday(Market.US_STOCK, datetime(2026, 8, 24, 23, 0)) is False
