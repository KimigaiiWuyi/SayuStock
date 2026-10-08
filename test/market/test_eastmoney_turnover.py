"""东财两市成交额（trends2）字段映射与失败兜底测试。

``get_hours_from_em`` 返回的是 ``(今日成交额, 今日-昨日, 日期)``——见
``utils/stock/utils.calculate_difference`` 的 ``return all_today_data,
all_today_data - all_yestoday_data, actual_date``。名字像「昨日」的 ``ya``
其实是**今日成交额**，昨日要用 ``今日 - 差值`` 反解。

这条错位长期没暴露：``draw_info`` 改造前是直接调 ``get_hours_from_em`` 并按下标
取用的（``all_f6, f6diff = ...``），走端口之后才会消费 adapter 的映射。
映射写反的症状是「大盘概览的成交额被显示成差值」。
"""

from __future__ import annotations

import sys
import asyncio
from types import ModuleType
from datetime import datetime
from unittest.mock import patch

from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.adapters.eastmoney.provider import EastMoneyMarketData

# 今日两市成交额 / (今日 - 昨日)；两者相减即昨日
TODAY = 1_000_000_000_000.0
DIFF = 200_000_000_000.0


def _turnover(today_amount: float, diff: float, ltd: datetime | None = None):
    """替换 adapter 函数内 ``from ....stock.request import get_hours_from_em`` 的来源。

    用替身模块而不是 ``patch("...get_hours_from_em")``：后者依赖真实的可导入
    包路径，在包壳式测试里会先炸；替身模块对两种跑法都成立。
    """
    stand_in = ModuleType("SayuStock.utils.stock.request")

    async def fake_hours() -> tuple[float, float, datetime | None]:
        return (today_amount, diff, ltd)

    stand_in.get_hours_from_em = fake_hours

    async def run():
        with patch.dict(sys.modules, {"SayuStock.utils.stock.request": stand_in}):
            return await EastMoneyMarketData().market_turnover()

    return asyncio.run(run())


def test_amount_is_today_turnover_not_the_difference() -> None:
    """第一项是今日成交额，不是差值——写反会让成交额少一个数量级。"""
    result = _turnover(TODAY, DIFF)
    assert not is_market_error(result)
    assert result.amount == TODAY


def test_prev_amount_is_derived_by_subtracting_the_difference() -> None:
    """函数不回昨日成交额，要由「今日 - 差值」反解，不能直接拿第一项当昨日。"""
    result = _turnover(TODAY, DIFF)
    assert not is_market_error(result)
    assert result.prev_amount == TODAY - DIFF
    assert result.prev_amount != TODAY, "把今日当昨日会让放量/缩量恒为负"


def test_diff_sign_is_recoverable() -> None:
    """放量（差值 > 0）时今日 > 昨日，渲染层据此判「放量」。"""
    result = _turnover(TODAY, DIFF)
    assert not is_market_error(result)
    assert result.prev_amount is not None
    assert result.amount - result.prev_amount == DIFF


def test_failed_pull_is_network_error_not_zero_amount() -> None:
    """trends2 两个市场都失败时 get_hours_from_em 回 (0, 0, None)。

    必须报错而不是「成功但成交额 0 亿」：否则优先级链不会顺延，
    大盘概览会在东财限流期间静默显示 0 亿（实测 -400016 时的表现）。
    """
    result = _turnover(0.0, 0.0)
    assert is_market_error(result), "拉取失败不能伪装成成功的 0 亿"
    assert result.code == "network"


def test_off_session_date_is_passed_through() -> None:
    """非交易日返回数据所属日期，渲染层据此显示「休市(N日前)」。"""
    day = datetime(2026, 9, 30, 15, 0)
    result = _turnover(TODAY, DIFF, day)
    assert not is_market_error(result)
    assert result.last_trade_date == day
