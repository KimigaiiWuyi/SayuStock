"""策略层单测：注册表 / 参数归一 / 量能函数 / 硬闸。"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from SayuStock.stock_papertrade import strategies as reg  # noqa: E402
from SayuStock.stock_papertrade.strategies.base import GateInput  # noqa: E402
from SayuStock.stock_papertrade.strategies.volume_extremum import (  # noqa: E402
    evaluate_location,
    measure_from_ohlcv,
)

MULTI = "multi_factor"
VOLUME = "volume_extremum"

_MACRO_OK = {"macro_regime": "中性", "macro_note": "油价回落但关税战仍在进行"}
_GOOD_MULTI = {"plan_stop_pct": -0.08, "roe": 15.2, **_MACRO_OK}
_GOOD_VOLUME_BUY = {
    "plan_stop_pct": -0.05,
    "rel_volume": 2.4,
    "close_percentile": 0.10,
    "bullish_close": True,
}
_GOOD_VOLUME_SELL = {"rel_volume": 2.4, "close_percentile": 0.92, "bullish_close": False}


def _gate(
    strategy_id: str,
    indicators: dict,
    *,
    score: float = 0.9,
    source: str = "decision",
    side: str = "buy",
) -> str:
    strategy, params = reg.resolve_with_params(strategy_id)
    return strategy.validate_entry(
        params,
        GateInput(stock_code="600519", indicators=indicators, score=score, source=source, side=side),
    )


def _ohlcv(*, closes: list[float], last_volume: float, last_open: float | None = None) -> pd.DataFrame:
    n = len(closes)
    vol = [100.0] * (n - 1) + [last_volume]
    opens = [closes[0], *closes[:-1]]
    if last_open is not None:
        opens[-1] = last_open
    return pd.DataFrame(
        {
            "open": opens,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vol,
        }
    )


def test_registry_contains_both_strategies():
    ids = reg.strategy_ids()
    assert MULTI in ids and VOLUME in ids
    assert reg.DEFAULT_STRATEGY_ID == MULTI


def test_unknown_strategy_falls_back_instead_of_raising():
    assert reg.resolve("nope_not_a_strategy").id == MULTI
    assert reg.get_strategy("homemade_alpha") is None


def test_describe_all_is_the_only_menu():
    text = reg.describe_all()
    assert "只能从下面选" in text
    assert MULTI in text and VOLUME in text
    assert "min_buy_score" in text
    assert "vol_ratio_min" in text


def test_params_fill_defaults():
    _, params = reg.resolve_with_params(MULTI, None)
    assert params["min_buy_score"] == 0.30
    _, vparams = reg.resolve_with_params(VOLUME, None)
    assert vparams["vol_ratio_min"] == 2.0
    assert vparams["bottom_pct"] == 0.15


def test_params_are_clamped_not_rejected():
    _, params = reg.resolve_with_params(VOLUME, {"vol_ratio_min": 999, "bottom_pct": -5})
    assert params["vol_ratio_min"] == 10.0
    assert params["bottom_pct"] == 0.05


def test_params_drop_foreign_keys():
    _, params = reg.resolve_with_params(MULTI, {"vol_ratio_min": 5.0, "min_buy_score": 0.4})
    assert "vol_ratio_min" not in params


def test_volume_prompt_says_functions_not_agent():
    strategy, params = reg.resolve_with_params(VOLUME)
    block = strategy.prompt_block(params)
    assert "系统函数" in block
    assert "不要自己编" in block
    assert "plan_stop" in block


def test_multi_factor_prompt_still_requires_stop():
    strategy, params = reg.resolve_with_params(MULTI)
    block = strategy.prompt_block(params)
    assert "plan_stop_pct" in block
    assert "macro_regime" in block and "macro_event_list" in block
    assert "防御档" in block and "进攻档" in block


def test_strategies_use_different_research_tools():
    multi = reg.resolve(MULTI)
    vol = reg.resolve(VOLUME)
    assert "stock_financials" in multi.decision_tools()
    assert "papertrade_volume_scan" not in multi.decision_tools()
    assert "papertrade_volume_scan" in vol.decision_tools()
    assert "stock_financials" not in vol.decision_tools()
    assert "papertrade_trade_insert" in multi.decision_tools()
    assert "papertrade_trade_insert" in vol.decision_tools()
    # 宏观事件表所有盘都能读；登记 / 更新只给多因子盘
    assert "macro_event_list" in multi.decision_tools()
    assert "macro_event_list" in vol.decision_tools()
    assert "macro_event_upsert" in multi.decision_tools()
    assert "macro_event_upsert" not in vol.decision_tools()


def test_kanban_task_mentions_strategy_tools():
    multi, mp = reg.resolve_with_params(MULTI)
    vol, vp = reg.resolve_with_params(VOLUME)
    assert "stock_financials" in multi.kanban_decision_task("默认模拟盘", mp)
    assert "papertrade_volume_scan" in vol.kanban_decision_task("放量盘", vp)
    assert multi.agent_profile != vol.agent_profile


def test_volume_pool_skips_bluechip():
    pref = reg.resolve(VOLUME).pool_preference({})
    assert pref.seed_bluechip is False
    assert pref.source_weights["quality"] == 0.0


def test_measure_from_ohlcv_detects_bottom_volume():
    """价格走低到窗口底部 + 尾量放大 → 底部放量。"""
    closes = [100.0 - i * 0.4 for i in range(60)]
    df = _ohlcv(closes=closes, last_volume=400.0, last_open=closes[-1] - 1.0)
    _, params = reg.resolve_with_params(VOLUME)
    measured = measure_from_ohlcv(df, params)
    assert not isinstance(measured, str)
    assert measured.close_percentile <= 0.15
    assert measured.rel_volume >= 2.0
    assert measured.bullish_close is True


def test_measure_from_ohlcv_detects_top_volume():
    closes = [80.0 + i * 0.5 for i in range(60)]
    df = _ohlcv(closes=closes, last_volume=400.0, last_open=closes[-1] + 1.0)
    _, params = reg.resolve_with_params(VOLUME)
    measured = measure_from_ohlcv(df, params)
    assert not isinstance(measured, str)
    assert measured.close_percentile >= 0.85
    assert measured.rel_volume >= 2.0


def test_measure_from_ohlcv_rejects_short_history():
    df = _ohlcv(closes=[10.0, 11.0, 12.0], last_volume=400.0)
    _, params = reg.resolve_with_params(VOLUME)
    assert isinstance(measure_from_ohlcv(df, params), str)


def test_month_screen_rejects_without_daily():
    """五年月K在中间区位时不应再依赖日K。"""
    # 五年月线高低拉开，最新收在正中 → 粗筛不应当底部
    mid_closes = [80.0, 120.0] * 29 + [80.0, 100.0]
    month = _ohlcv(closes=mid_closes, last_volume=100.0)
    _, params = reg.resolve_with_params(VOLUME)
    out = evaluate_location(month, None, params, "buy")
    assert not isinstance(out, str)
    assert out.month_passed is False
    assert out.used_daily is False


def test_month_bottom_then_daily_confirms():
    month = _ohlcv(closes=[200.0 - i * 2.0 for i in range(60)], last_volume=100.0)
    day = _ohlcv(
        closes=[100.0 - i * 0.4 for i in range(60)],
        last_volume=400.0,
        last_open=76.0,
    )
    _, params = reg.resolve_with_params(VOLUME)
    coarse = evaluate_location(month, None, params, "buy")
    assert not isinstance(coarse, str) and coarse.month_passed is True
    full = evaluate_location(month, day, params, "buy")
    assert not isinstance(full, str)
    assert full.used_daily is True
    assert full.rel_volume is not None and full.rel_volume >= 2.0
    assert full.close_percentile is not None and full.close_percentile <= 0.15


def test_multi_factor_gates_plan_stop_and_macro():
    assert _gate(MULTI, {"plan_stop_pct": -0.08, **_MACRO_OK}, score=0.01) == ""
    assert _gate(MULTI, {"stop_pct": -0.08, **_MACRO_OK}, score=0.01) == ""
    assert _gate(MULTI, {"stop_price": 780.15, **_MACRO_OK}, score=0.01) == ""
    # 止损仍是第一道闸
    assert "plan_stop" in _gate(MULTI, {"roe": 1.0, **_MACRO_OK})
    # 有止损没宏观档位 → 拒，且理由告诉模型该填什么
    no_regime = _gate(MULTI, {"plan_stop_pct": -0.08})
    assert "macro_regime" in no_regime and "macro_event_list" in no_regime
    # 档位有了但依据太短 → 拒
    short_note = _gate(MULTI, {"plan_stop_pct": -0.08, "macro_regime": "进攻", "macro_note": "涨"})
    assert "macro_note" in short_note
    # 同义写法可被归一
    assert _gate(MULTI, {"plan_stop_pct": -0.08, "macro_regime": "risk_off", "macro_note": "油价冲高美债飙升"}) == ""
    # 卖出不卡宏观
    assert _gate(MULTI, {"rsi": 90}, side="sell") == ""


def test_multi_factor_rejects_offense_when_severe_risk_off_open():
    from SayuStock.stock_papertrade.strategy import MACRO_SEVERE_FLAG_KEY, MACRO_SEVERE_TITLES_KEY

    severe = {MACRO_SEVERE_FLAG_KEY: True, MACRO_SEVERE_TITLES_KEY: "中美关税战(sev4)"}
    base = {"plan_stop_pct": -0.08, "macro_note": "关税战进行中但个股超跌"}
    rejected = _gate(MULTI, {**base, **severe, "macro_regime": "进攻"})
    assert "中美关税战" in rejected and "进攻" in rejected
    assert _gate(MULTI, {**base, **severe, "macro_regime": "防御"}) == ""
    assert _gate(MULTI, {**base, **severe, "macro_regime": "中性"}) == ""
    # 表里没有高危事件时进攻放行
    assert _gate(MULTI, {**base, MACRO_SEVERE_FLAG_KEY: False, "macro_regime": "进攻"}) == ""


def test_volume_buy_uses_structure_fields():
    assert _gate(VOLUME, _GOOD_VOLUME_BUY) == ""
    assert "未放量" in _gate(VOLUME, {**_GOOD_VOLUME_BUY, "rel_volume": 1.1})
    assert "日K不在近" in _gate(VOLUME, {**_GOOD_VOLUME_BUY, "close_percentile": 0.80})


def test_volume_sell_uses_structure_unless_stop():
    assert _gate(VOLUME, _GOOD_VOLUME_SELL, side="sell") == ""
    assert "日K不在近" in _gate(VOLUME, {**_GOOD_VOLUME_SELL, "close_percentile": 0.40}, side="sell")
    assert _gate(VOLUME, {"stop_triggered": True}, side="sell") == ""


def test_volume_missing_structure_is_rejected():
    assert "未能算出" in _gate(VOLUME, {"plan_stop_pct": -0.05})


def test_load_structure_does_not_cache_kline_errors():
    """拉 K 失败不能进 30 分钟结构缓存，否则止损窗口会被拉长。"""
    import asyncio
    from unittest.mock import patch

    from SayuStock.stock_papertrade.strategies import volume_structure as vs

    _, params = reg.resolve_with_params(VOLUME)
    vs._struct_cache.clear()

    async def _fail_kline(*_a: object, **_k: object) -> str:
        return "⚠️ 拉K线失败(mon): boom"

    async def _run() -> None:
        with patch.object(vs, "_cached_kline", _fail_kline):
            first = await vs.load_structure("600519", params, intent="sell")
            second = await vs.load_structure("600519", params, intent="sell")
        assert first == "⚠️ 拉K线失败(mon): boom"
        assert second == first
        assert not any(isinstance(item[1], str) for item in vs._struct_cache.values())

    try:
        asyncio.run(_run())
    finally:
        vs._struct_cache.clear()


def test_gate_entry_stop_sell_skips_failed_kline():
    """止损卖必须在拉 K 之前放行，不能被 load_structure 失败短路。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from SayuStock.stock_papertrade.ai_tools import _gate_entry

    account = SimpleNamespace(strategy_id=VOLUME, strategy_params="{}")
    loader = AsyncMock(return_value="⚠️ 拉K线失败(day): timeout")

    async def _run() -> str:
        with patch(
            "SayuStock.stock_papertrade.strategies.volume_extremum.load_structure",
            loader,
        ):
            return await _gate_entry(
                account,
                stock_code="600519",
                indicators={"stop_triggered": True},
                score=0.1,
                source="decision",
                side="sell",
            )

    assert asyncio.run(_run()) == ""
    loader.assert_not_called()
