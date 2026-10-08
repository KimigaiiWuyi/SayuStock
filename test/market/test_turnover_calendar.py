"""休市日两市成交额：取数据里**最近的那个交易日**，而不是「回退 4 天探不到就算失败」。

回归背景：原实现按「几号」比较日期、且最多回退 4 天。国庆这类连休 >4 天的长假
一律探不到 → 回 ``(0, 0, None)`` → adapter 把它当成拉取失败 → 大盘概览在休市日
**悄悄换源**（新浪给的是上一交易日成交额、prev_amount=None、日期口径也不同）。
现在休市日照样由东财自己给出「上一交易日成交额 + 实际日期」。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from SayuStock.utils.stock.utils import calculate_difference

# trends2 一行：f51 时间, f52 开, f53 收, f54 高, f55 低, f56 量, f57 成交额
_ROW_AMOUNT_INDEX = 6


def _trends(day: str, minutes: int, amount: float) -> list[str]:
    rows = []
    for i in range(minutes):
        parts = ["0"] * 8
        parts[0] = f"{day} 09:{30 + i:02d}"
        parts[_ROW_AMOUNT_INDEX] = str(amount)
        rows.append(",".join(parts))
    return rows


def _with_today(day: datetime, data: list[str]) -> tuple[float, float, datetime | None]:
    with patch("SayuStock.utils.stock.utils.get_adjusted_date", return_value=day):
        return calculate_difference(data)


def test_long_holiday_returns_previous_trading_day() -> None:
    """国庆连休（10-01~10-07）后取 09-30，而不是回 (0,0,None)。"""
    data = _trends("2026-09-29", 3, 100.0) + _trends("2026-09-30", 3, 200.0)
    amount, diff, actual_date = _with_today(datetime(2026, 10, 7, 10, 0), data)
    assert amount == 600.0
    assert diff == 600.0 - 300.0
    assert actual_date == datetime(2026, 9, 30)


def test_holiday_within_four_days_also_works() -> None:
    """短假（原来靠 4 天回退能探到）行为不变，避免修出回归。"""
    data = _trends("2026-09-29", 3, 100.0) + _trends("2026-09-30", 3, 200.0)
    amount, diff, actual_date = _with_today(datetime(2026, 10, 2, 10, 0), data)
    assert amount == 600.0
    assert diff == 300.0
    assert actual_date == datetime(2026, 9, 30)


def test_trading_day_reports_none_date() -> None:
    """数据里有今天 → 正常交易日，日期回 None（渲染层据此不显示休市）。"""
    data = _trends("2026-09-29", 3, 100.0) + _trends("2026-09-30", 3, 200.0)
    amount, diff, actual_date = _with_today(datetime(2026, 9, 30, 10, 0), data)
    assert amount == 600.0
    assert diff == 300.0
    assert actual_date is None


def test_comparison_uses_same_time_of_day() -> None:
    """昨日只取与今日同样多的点数相比，不让早盘拿全天量当基准。"""
    today = _trends("2026-09-30", 2, 200.0)
    yesterday = _trends("2026-09-29", 6, 100.0)  # 昨日有 6 个点，只应取前 2 个
    amount, diff, _ = _with_today(datetime(2026, 9, 30, 10, 0), today + yesterday)
    assert amount == 400.0
    assert diff == 400.0 - 200.0


def test_month_boundary_does_not_confuse_day_numbers() -> None:
    """跨月：按「几号」比较会把 30 号当成今天之后，必须按完整日期判定。"""
    data = _trends("2026-09-30", 3, 200.0) + _trends("2026-10-02", 3, 300.0)
    amount, diff, actual_date = _with_today(datetime(2026, 10, 5, 10, 0), data)
    # 最近交易日是 10-02，前一交易日是 09-30
    assert amount == 900.0
    assert diff == 300.0
    assert actual_date == datetime(2026, 10, 2)


def test_single_trading_day_has_no_comparison() -> None:
    """只有一个交易日时给不出放量/缩量 → (0,0,None)，由 adapter 报失败顺延。"""
    data = _trends("2026-09-30", 3, 200.0)
    assert _with_today(datetime(2026, 10, 7, 10, 0), data) == (0.0, 0.0, None)


def test_empty_trends_is_failure() -> None:
    assert _with_today(datetime(2026, 10, 7, 10, 0), []) == (0.0, 0.0, None)
