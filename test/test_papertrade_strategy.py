"""模拟盘策略、候选池、计划持久化、迁徙与账本自愈核心单测。"""

from __future__ import annotations

import ast
import json
import asyncio
import inspect
import textwrap
from typing import Tuple, Callable, Awaitable
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pandas as pd
from sqlalchemy import text
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from SayuStock.utils.database import papertrade_migration as mig
from SayuStock.stock_papertrade import (
    db,
    ai_tools,
    strategies as reg,
    holiday_heal as heal,
    candidate_pool as pool,
    trade_executor,
)
from SayuStock.stock_papertrade.matcher import apply_fill_qty_cost
from SayuStock.stock_papertrade.strategy import (
    MACRO_SEVERE_FLAG_KEY,
    MACRO_SEVERE_TITLES_KEY,
    Decision,
    FundSignals,
    NewsSignals,
    TechSignals,
    StockContext,
    AccountContext,
    PositionContext,
    score_stock,
    decide_action,
    apply_risk_check,
    resolve_plan_prices,
    tech_from_indicators,
    parse_llm_json_object,
    normalize_plan_indicators,
    indicators_have_entry_stop,
    indicators_have_entry_take,
)
from SayuStock.stock_papertrade.strategies.base import GateInput
from SayuStock.utils.database.papertrade_models import SayuPaperAccount, SayuPaperPosition
from SayuStock.stock_papertrade.strategies.volume_extremum import (
    evaluate_location,
    measure_from_ohlcv,
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


def _gate(
    strategy_id: str,
    indicators: dict[str, object],
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


def test_strategies_registry_params_and_volume_location() -> None:
    """策略注册表兜底、参数钳位/剔除、量能底顶检测及月日两段过滤。"""
    ids = reg.strategy_ids()
    assert "multi_factor" in ids and "volume_extremum" in ids
    assert reg.DEFAULT_STRATEGY_ID == "multi_factor"
    assert reg.resolve("nope_not_a_strategy").id == "multi_factor"
    assert reg.get_strategy("homemade_alpha") is None

    menu = reg.describe_all()
    assert "只能从下面选" in menu and "min_buy_score" in menu and "vol_ratio_min" in menu

    _, clamped = reg.resolve_with_params("volume_extremum", {"vol_ratio_min": 999, "bottom_pct": -5})
    assert clamped["vol_ratio_min"] == 10.0 and clamped["bottom_pct"] == 0.05
    _, dropped = reg.resolve_with_params("multi_factor", {"vol_ratio_min": 5.0, "min_buy_score": 0.4})
    assert "vol_ratio_min" not in dropped and dropped["min_buy_score"] == 0.4

    multi, mp = reg.resolve_with_params("multi_factor")
    vol, vp = reg.resolve_with_params("volume_extremum")
    assert "stock_financials" in multi.decision_tools()
    assert "papertrade_volume_scan" in vol.decision_tools()
    assert "macro_event_upsert" in multi.decision_tools()
    assert "macro_event_upsert" not in vol.decision_tools()
    assert "stock_financials" in multi.kanban_decision_task("默认模拟盘", mp)
    assert "papertrade_volume_scan" in vol.kanban_decision_task("放量盘", vp)

    bottom_closes = [100.0 - i * 0.4 for i in range(60)]
    bottom = measure_from_ohlcv(_ohlcv(closes=bottom_closes, last_volume=400.0, last_open=bottom_closes[-1] - 1.0), vp)
    assert not isinstance(bottom, str)
    assert bottom.close_percentile is not None and bottom.close_percentile <= 0.15
    assert bottom.rel_volume is not None and bottom.rel_volume >= 2.0
    assert bottom.bullish_close is True

    top_closes = [80.0 + i * 0.5 for i in range(60)]
    top = measure_from_ohlcv(_ohlcv(closes=top_closes, last_volume=400.0, last_open=top_closes[-1] + 1.0), vp)
    assert not isinstance(top, str) and top.close_percentile is not None and top.close_percentile >= 0.85
    assert isinstance(measure_from_ohlcv(_ohlcv(closes=[10.0, 11.0, 12.0], last_volume=400.0), vp), str)

    mid_month = _ohlcv(closes=[80.0, 120.0] * 29 + [80.0, 100.0], last_volume=100.0)
    mid_out = evaluate_location(mid_month, None, vp, "buy")
    assert not isinstance(mid_out, str) and mid_out.month_passed is False and mid_out.used_daily is False

    low_month = _ohlcv(closes=[200.0 - i * 2.0 for i in range(60)], last_volume=100.0)
    day = _ohlcv(closes=bottom_closes, last_volume=400.0, last_open=76.0)
    full = evaluate_location(low_month, day, vp, "buy")
    assert not isinstance(full, str) and full.month_passed is True and full.used_daily is True


def test_strategy_entry_gates() -> None:
    """多因子止损/止盈与宏观三档硬闸、重磅风险拦截，以及量能extremum结构硬闸。"""
    macro_ok: dict[str, object] = {"macro_regime": "中性", "macro_note": "油价回落但关税战仍在进行"}
    take_ok: dict[str, object] = {"plan_take_pct": 0.10}
    assert _gate("multi_factor", {"plan_stop_pct": -0.08, **take_ok, **macro_ok}, score=0.01) == ""
    assert _gate("multi_factor", {"stop_price": 780.15, "plan_take_price": 920.0, **macro_ok}, score=0.01) == ""
    assert "plan_stop" in _gate("multi_factor", {"roe": 1.0, **take_ok, **macro_ok})
    assert "plan_take" in _gate("multi_factor", {"plan_stop_pct": -0.08, **macro_ok})
    no_regime = _gate("multi_factor", {"plan_stop_pct": -0.08, **take_ok})
    assert "macro_regime" in no_regime and "macro_event_list" in no_regime
    assert "macro_note" in _gate(
        "multi_factor", {"plan_stop_pct": -0.08, **take_ok, "macro_regime": "进攻", "macro_note": "涨"}
    )
    assert (
        _gate(
            "multi_factor",
            {"plan_stop_pct": -0.08, **take_ok, "macro_regime": "risk_off", "macro_note": "油价冲高美债飙升"},
        )
        == ""
    )
    assert _gate("multi_factor", {"rsi": 90}, side="sell") == ""

    severe: dict[str, object] = {
        MACRO_SEVERE_FLAG_KEY: True,
        MACRO_SEVERE_TITLES_KEY: "中美关税战(sev4)",
        "plan_stop_pct": -0.08,
        "plan_take_pct": 0.12,
        "macro_note": "关税战进行中但个股超跌",
    }
    rejected = _gate("multi_factor", {**severe, "macro_regime": "进攻"})
    assert "中美关税战" in rejected and "进攻" in rejected
    assert _gate("multi_factor", {**severe, "macro_regime": "防御"}) == ""
    assert _gate("multi_factor", {**severe, "macro_regime": "中性"}) == ""
    assert _gate("multi_factor", {**severe, MACRO_SEVERE_FLAG_KEY: False, "macro_regime": "进攻"}) == ""

    good_vol_buy: dict[str, object] = {
        "plan_stop_pct": -0.05,
        "rel_volume": 2.4,
        "close_percentile": 0.10,
        "bullish_close": True,
    }
    good_vol_sell: dict[str, object] = {"rel_volume": 2.4, "close_percentile": 0.92, "bullish_close": False}
    assert _gate("volume_extremum", good_vol_buy) == ""
    assert "未放量" in _gate("volume_extremum", {**good_vol_buy, "rel_volume": 1.1})
    assert "日K不在近" in _gate("volume_extremum", {**good_vol_buy, "close_percentile": 0.80})
    assert _gate("volume_extremum", good_vol_sell, side="sell") == ""
    assert "日K不在近" in _gate("volume_extremum", {**good_vol_sell, "close_percentile": 0.40}, side="sell")
    assert _gate("volume_extremum", {"stop_triggered": True}, side="sell") == ""
    assert "未能算出" in _gate("volume_extremum", {"plan_stop_pct": -0.05})


def test_strategy_scoring_decision_and_risk() -> None:
    """score_stock 边界、decide_action 风控优先/信号买卖、apply_risk_check 与 plan 解析工具。"""
    bull_tech = tech_from_indicators(
        {
            "macd_dif": 1.2,
            "macd_dea": 0.8,
            "macd_bar": 0.4,
            "macd_golden_cross_in_3d": True,
            "ma_bull_alignment": True,
            "close_above_ma20": True,
            "cmf20": 0.2,
            "volume_ratio": 2.5,
            "turnover_pct": 2.0,
            "rsi6": 25.0,
            "atr_pct": 0.01,
        }
    )
    bull_fund = FundSignals(
        roe=0.25, revenue_yoy=0.35, profit_yoy=0.5, gross_margin=0.5, pe_ttm=10.0, industry_pe_median=20.0
    )
    bull_news = NewsSignals(positive_count=5, negative_count=0, has_forecast_up=True)
    sc, reasons = score_stock(bull_tech, bull_fund, bull_news)
    assert sc == 1.0 and len(reasons) >= 5

    bear_tech = TechSignals(
        macd_death_cross_in_3d=True,
        macd_bar_negative_and_dif_below_dea=True,
        rsi6=85.0,
        ma_bear_alignment=True,
        close_below_ma20=True,
        cmf20=-0.25,
        atr_pct=0.02,
    )
    bear_fund = FundSignals(
        roe=0.01, revenue_yoy=-0.1, profit_yoy=-0.3, debt_ratio=0.85, pe_ttm=40.0, industry_pe_median=20.0
    )
    sc_bear, _ = score_stock(bear_tech, bear_fund, NewsSignals(negative_count=5, has_reduction_or_negative=True))
    assert sc_bear == -1.0

    stock = StockContext("600519", "贵州茅台", 100.0)
    acc = AccountContext(cash=500000.0, total_equity=1000000.0, holdings_count=1)
    d_stop = decide_action(
        StockContext("600519", "贵州茅台", 90.0), 0.8, [], acc, PositionContext(qty=1000, avg_cost=100.0), "balanced"
    )
    assert d_stop.action == "sell" and d_stop.qty == 1000
    d_half = decide_action(
        StockContext("600519", "贵州茅台", 135.0), 0.8, [], acc, PositionContext(qty=1000, avg_cost=100.0), "balanced"
    )
    assert d_half.action == "sell" and d_half.qty == 500
    d_buy = decide_action(stock, 0.50, [], acc, PositionContext(), "balanced")
    assert d_buy.action == "buy" and d_buy.qty >= 100
    d_drawdown = decide_action(
        stock,
        0.5,
        [],
        AccountContext(cash=500000.0, total_equity=800000.0, total_pnl_pct=-0.20),
        PositionContext(qty=500, avg_cost=100.0),
        "balanced",
    )
    assert d_drawdown.action == "sell" and d_drawdown.qty == 500

    proposed = Decision("buy", 200, "test buy", score=0.5)
    assert apply_risk_check(acc, proposed, "600519", "balanced").action == "buy"
    blocked_cnt = apply_risk_check(
        AccountContext(cash=500000.0, total_equity=1000000.0, daily_trade_count=6), proposed, "600519", "balanced"
    )
    assert blocked_cnt.action == "hold" and blocked_cnt.blocked_by == "max_daily_trades"
    blocked_hold = apply_risk_check(
        AccountContext(cash=500000.0, total_equity=1000000.0, holdings_count=8), proposed, "600519", "balanced"
    )
    assert blocked_hold.action == "hold" and blocked_hold.blocked_by == "max_holdings"
    blocked_reentry = apply_risk_check(
        AccountContext(cash=500000.0, total_equity=1000000.0, reentry_count_today={"600519": 1}),
        proposed,
        "600519",
        "balanced",
    )
    assert blocked_reentry.action == "hold" and blocked_reentry.blocked_by == "reentry_limit"

    parsed = parse_llm_json_object('{"indicators": {"stop_pct": -5.0, "take_pct": 10.0}}')
    flat = normalize_plan_indicators(parsed)
    assert flat["plan_stop_pct"] == -0.05 and flat["plan_take_pct"] == 0.1
    assert indicators_have_entry_stop(parsed) is True
    assert indicators_have_entry_take(parsed) is True
    assert indicators_have_entry_stop({"plan_stop_pct": 0.05}) is False
    assert indicators_have_entry_take({"plan_take_pct": -0.05}) is False
    assert indicators_have_entry_take({"plan_take_pct": 500.0}) is True
    assert indicators_have_entry_take({"plan_take_pct": 501.0}) is False
    sp, spct, tp, _ = resolve_plan_prices(parsed, entry_price=50.0)
    assert sp == 47.5 and spct == -0.05 and tp == 55.0


def test_candidate_pool_merge_dedup_priority() -> None:
    """多源候选池空源兼容、去重与按来源优先级排序。"""

    async def _empty(*_a: object, **_kw: object) -> list[str]:
        return []

    def _source(items: list[str]) -> Callable[..., Awaitable[list[str]]]:
        async def _fn(*_a: object, **_kw: object) -> list[str]:
            return list(items)

        return _fn

    names = (
        "_from_position",
        "_from_watchlist",
        "_from_agent_pool",
        "_from_sector_top_picks",
        "_from_concept_top_picks",
        "_from_hotmap_top_n",
        "_from_market_gainers",
        "_from_market_laggards",
        "_from_amount_leaders",
        "_from_quality_roe",
        "_from_news_extract_tickers",
    )
    with patch.multiple(pool, **{n: _empty for n in names}):
        assert asyncio.run(pool.build_candidate_pool(1)) == []

    overrides: dict[str, object] = {n: _empty for n in names}
    overrides.update(
        {
            "_from_position": _source(["600519", "000001"]),
            "_from_watchlist": _source(["600519", "300750"]),
            "_from_agent_pool": _source(["600005"]),
            "_from_sector_top_picks": _source(["600006"]),
            "_from_hotmap_top_n": _source(["600008"]),
            "_from_news_extract_tickers": _source(["600009"]),
        }
    )
    with patch.multiple(pool, **overrides):
        merged = asyncio.run(pool.build_candidate_pool(1))
    assert merged == ["600519", "000001", "300750", "600005", "600006", "600008", "600009"]


def test_holiday_heal_and_reconciliation(tmp_path: Path) -> None:
    """休市自愈：时间解析不兜底now()、当天不删、双向持仓对账、AST防早退及归档复活持久化。"""
    assert heal._as_datetime("2026-10-16 10:00:00") == datetime(2026, 10, 16, 10, 0, 0)
    assert heal._as_datetime("not-a-timestamp") is None

    t1 = heal._Trade(
        1,
        1,
        "600519",
        "贵州茅台",
        "1.600519",
        "buy",
        100.0,
        200,
        20000.0,
        10.0,
        0.0,
        datetime(2026, 10, 15, 10, 0),
        date(2026, 10, 15),
    )
    t2 = heal._Trade(
        2,
        1,
        "600519",
        "贵州茅台",
        "1.600519",
        "sell",
        120.0,
        200,
        24000.0,
        15.0,
        3975.0,
        datetime(2026, 10, 16, 10, 0),
        date(2026, 10, 16),
    )
    t3 = heal._Trade(
        3,
        2,
        "000333",
        "美的集团",
        "0.000333",
        "buy",
        80.0,
        100,
        8000.0,
        5.0,
        0.0,
        datetime(2026, 10, 16, 10, 5),
        date(2026, 10, 16),
    )
    expected_q1, expected_c1 = apply_fill_qty_cost(0, 0.0, "buy", 200, 100.0, 10.0)
    s1 = heal._replay_final([t1])["600519"]
    assert s1.qty == expected_q1 and abs(s1.avg - expected_c1) < 1e-6
    assert heal._replay_final([t1, t2])["600519"].qty == 0
    assert heal._replay_final([t1, t2])["600519"].opened_on is None

    dirty = heal._dirty_trades([t2, t3], frozenset({"2026-10-15", "2026-10-16"}), date(2026, 10, 16))
    assert dirty == []
    dirty_past = heal._dirty_trades([t1, t2], frozenset({"2026-10-15", "2026-10-16"}), date(2026, 10, 17))
    assert [t.id for t in dirty_past] == [1, 2]

    # 双向持仓对账：含“只有持仓零流水”账户删幽灵仓 + 缺持仓建仓
    fixes = heal._plan_positions(
        [t3],
        {(1, "600036"): (500, 35.0, 36.0)},
        frozenset({1, 2}),
    )
    by_pair = {(f["account_id"], f["stock_code"]): f for f in fixes}
    assert by_pair[(1, "600036")]["removed"] is True
    assert by_pair[(2, "000333")]["created"] is True and by_pair[(2, "000333")]["new_qty"] == 100

    # AST 防回归：pos_fixes / cash_fixes 必须排在 not dirty 早退之前
    tree = ast.parse(textwrap.dedent(inspect.getsource(heal.heal_holiday_trades)))
    plan_line = cash_line = guard_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            ids = [n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name)]
            if plan_line is None and "pos_fixes" in ids:
                plan_line = node.lineno
            if cash_line is None and "cash_fixes" in ids:
                cash_line = node.lineno
        elif isinstance(node, ast.If) and guard_line is None:
            if any(
                isinstance(sub, ast.UnaryOp)
                and isinstance(sub.op, ast.Not)
                and isinstance(sub.operand, ast.Name)
                and sub.operand.id == "dirty"
                for sub in ast.walk(node.test)
            ):
                guard_line = node.lineno
    assert plan_line is not None and cash_line is not None and guard_line is not None
    assert plan_line < guard_line and cash_line < guard_line

    # 归档复活：交易日行复活并 commit 后在独立连接可见，休市日/已存在跳过且复跑幂等
    db_file = tmp_path / "heal_probe.db"
    archive = tmp_path / "papertrade_holiday_archive_20261102-165755.json"
    archive.write_text(
        json.dumps(
            {
                "archived_at": "2026-11-02T16:57:55",
                "dirty_days": ["2026-11-02"],
                "count": 3,
                "trades": [
                    {
                        "id": 49,
                        "account_id": 1,
                        "stock_code": "600028",
                        "stock_name": "中国石化",
                        "secid": "1.600028",
                        "side": "sell",
                        "price": 5.87,
                        "qty": 500,
                        "amount": 2935.0,
                        "fee": 10.0,
                        "realized_pnl": 178.0,
                        "decided_at": "2026-11-02T10:00:00.123456",
                    },
                    {
                        "id": 40,
                        "account_id": 1,
                        "stock_code": "600028",
                        "stock_name": "中国石化",
                        "secid": "1.600028",
                        "side": "buy",
                        "price": 5.8,
                        "qty": 100,
                        "amount": 580.0,
                        "fee": 10.0,
                        "realized_pnl": 0.0,
                        "decided_at": "2026-10-01T10:00:00.123456",
                    },
                    {
                        "id": 41,
                        "account_id": 1,
                        "stock_code": "600028",
                        "stock_name": "中国石化",
                        "secid": "1.600028",
                        "side": "buy",
                        "price": 5.5,
                        "qty": 9000,
                        "amount": 49500.0,
                        "fee": 10.0,
                        "realized_pnl": 0.0,
                        "decided_at": "2026-10-16T10:00:00.123456",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def _fake_trading() -> Tuple[frozenset[str], str]:
        return frozenset({"2026-10-16", "2026-11-02"}), "test"

    async def _run_restore() -> Tuple[int, list[str], int, int]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE sayupaperaccount (id INTEGER PRIMARY KEY, group_id TEXT, bot_id TEXT, mode TEXT)")
            )
            await conn.execute(
                text(
                    "CREATE TABLE sayupapertrade (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "account_id INTEGER, group_id TEXT, bot_id TEXT, stock_code TEXT, stock_name TEXT, "
                    "secid TEXT, side TEXT, price REAL, qty INTEGER, amount REAL, fee REAL, realized_pnl REAL, "
                    "reason TEXT, snapshot TEXT, decided_at DATETIME, executed_at DATETIME, mode TEXT)"
                )
            )
            await conn.execute(text("INSERT INTO sayupaperaccount VALUES (1, 'g1', 'b1', 'balanced')"))
            await conn.execute(
                text(
                    "INSERT INTO sayupapertrade (account_id, stock_code, stock_name, secid, side, price, qty, "
                    "amount, fee, realized_pnl, reason, snapshot, decided_at, executed_at, mode) "
                    "VALUES (1, '600028', '中国石化', '1.600028', 'buy', 5.5, 9000, 49500.0, 25.0, 0.0, "
                    "'', '', '2026-10-16 10:00:00.123456', '2026-10-16 10:00:00.123456', 'balanced')"
                )
            )
        async with maker() as s:
            existing, _ = await heal._load_trades(s)
            n1, days = await heal._restore_archived(s, existing, dry_run=False)
            await s.commit()
        async with maker() as s:
            rows = (await s.execute(text("SELECT id FROM sayupapertrade ORDER BY id"))).all()
            existing2, _ = await heal._load_trades(s)
            n2, _ = await heal._restore_archived(s, existing2, dry_run=False)
        await engine.dispose()
        return n1, days, len(rows), n2

    with patch.object(heal, "resolve_trading_days", _fake_trading), patch.object(heal, "DATA_PATH", tmp_path):
        n1, days, row_count, n2 = asyncio.run(_run_restore())
    assert n1 == 1 and days == ["2026-11-02"] and row_count == 2 and n2 == 0


def test_plan_persistence_and_atomic_write(tmp_path: Path) -> None:
    """入场计划字段定义、长决策理由不截断、trade_insert 透传 snapshot、原子写持仓计划。"""
    import gsuid_core.utils.database.base_models as base_models

    for col in (
        "plan_stop_price",
        "plan_stop_pct",
        "plan_take_price",
        "plan_thesis",
        "plan_updated_at",
        "plan_rev_count",
    ):
        assert col in SayuPaperPosition.model_fields

    long_reason = "完整决策逻辑。" + "量化推理与宏观分析，" * 20
    fake_acc = SayuPaperAccount(id=1, name="默认模拟盘", cash=1000000.0)
    saved_decisions: list[dict[str, object]] = []

    async def _fake_decision_append(_aid: int, **kwargs: object) -> AsyncMock:
        saved_decisions.append(kwargs)
        return AsyncMock(id=101)

    with (
        patch.object(ai_tools, "_write_account", AsyncMock(return_value=(fake_acc, ""))),
        patch.object(ai_tools, "_deny_write", AsyncMock(return_value="")),
        patch.object(db.PaperDecisionRepo, "append", _fake_decision_append),
    ):
        asyncio.run(
            ai_tools.papertrade_decision_insert(
                ctx=AsyncMock(),
                action="hold",
                stock_code="000333",
                stock_name="美的集团",
                reason=long_reason,
                indicators="{}",
            )
        )
    assert saved_decisions[0]["reason"] == long_reason

    record_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        async def record_trade(self, **kwargs: object) -> AsyncMock:
            record_calls.append(kwargs)
            res = AsyncMock()
            res.ok = True
            res.message = "ok"
            return res

    plan_json = '{"plan_stop_price": 83.36, "plan_stop_pct": -0.055, "plan_thesis": "白电龙头底仓"}'
    with (
        patch.object(ai_tools, "_write_account", AsyncMock(return_value=(fake_acc, ""))),
        patch.object(ai_tools, "_deny_write", AsyncMock(return_value="")),
        patch.object(ai_tools, "_gate_entry", AsyncMock(return_value="")),
        patch.object(trade_executor, "get_executor", return_value=_FakeExecutor()),
        patch.object(ai_tools.broadcast, "broadcast_fill", AsyncMock()),
    ):
        asyncio.run(
            ai_tools.papertrade_trade_insert(
                ctx=AsyncMock(),
                stock_code="000333",
                stock_name="美的集团",
                secid="0.000333",
                side="buy",
                price=88.21,
                qty=500,
                amount=44105.0,
                fee=5.0,
                indicators=plan_json,
                snapshot="",
            )
        )
    parsed_snap = json.loads(str(record_calls[0]["snapshot"]))
    assert parsed_snap["plan_thesis"] == "白电龙头底仓" and abs(float(parsed_snap["plan_stop_pct"]) + 0.055) < 1e-4

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/pt_plan.db")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _atomic() -> tuple[object, object, object]:
        ddl = str(CreateTable(SayuPaperAccount.__table__).compile(dialect=sqlite.dialect()))
        for t in ("sayupaperposition", "sayupapertrade", "sayupaperdecision"):
            ddl += ";" + str(CreateTable(SayuPaperAccount.metadata.tables[t]).compile(dialect=sqlite.dialect()))
        async with engine.begin() as conn:
            for stmt in ddl.split(";"):
                await conn.execute(text(stmt))
        orig_maker = base_models.async_maker
        base_models.async_maker = maker
        try:
            async with maker() as s:
                acc = SayuPaperAccount(
                    name="测试盘",
                    group_id="111",
                    bot_id="onebot",
                    cash=1000000.0,
                    initial_cash=1000000.0,
                    principal=1000000.0,
                )
                s.add(acc)
                await s.commit()
                acc_id = int(acc.id or 0)
            snap = json.dumps(
                {
                    "plan_stop_pct": -5.0,
                    "plan_take_pct": 10.0,
                    "plan_thesis": "白电龙头底仓",
                },
                ensure_ascii=False,
            )
            await db.PaperTradeRepo.append_with_cash_update(
                acc_id,
                stock_code="000333",
                stock_name="美的集团",
                secid="0.000333",
                side="buy",
                price=88.21,
                qty=500,
                amount=44105.0,
                fee=5.0,
                snapshot=snap,
            )
            async with maker() as s:
                row_buy = (
                    await s.execute(
                        text(
                            "SELECT plan_stop_price, plan_stop_pct, plan_take_price, plan_thesis "
                            "FROM sayupaperposition WHERE account_id = :a"
                        ),
                        {"a": acc_id},
                    )
                ).first()
            pos_updated, audit_id = await db.PaperPositionRepo.update_plan(
                acc_id,
                "000333",
                plan_stop_price=90.0,
                clear_stop_pct=True,
                plan_take_price=105.0,
                increment_rev=True,
                audit_reason="[计划修订:trail_stop] 浮盈超10%，止损上移保本锁定利润",
                audit_indicators='{"plan_revision":{"type":"trail_stop"}}',
            )
            async with maker() as s:
                row_rev = (
                    await s.execute(
                        text(
                            "SELECT plan_stop_price, plan_stop_pct, plan_take_price, plan_rev_count, plan_updated_at "
                            "FROM sayupaperposition WHERE account_id = :a"
                        ),
                        {"a": acc_id},
                    )
                ).first()
                row_audit = (
                    await s.execute(
                        text("SELECT id, action, reason FROM sayupaperdecision WHERE id = :did"),
                        {"did": audit_id},
                    )
                ).first()
            assert pos_updated is not None and audit_id > 0
            return row_buy, row_rev, row_audit
        finally:
            base_models.async_maker = orig_maker
            await engine.dispose()

    pos_row, rev_row, audit_row = asyncio.run(_atomic())
    assert pos_row is not None and pos_row[3] == "白电龙头底仓"
    assert abs(float(pos_row[1]) + 0.05) < 1e-4 and abs(float(pos_row[2]) - 88.22 * 1.1) < 0.02
    assert rev_row is not None and abs(float(rev_row[0]) - 90.0) < 0.001 and rev_row[1] is None and int(rev_row[3]) == 1
    assert audit_row is not None and audit_row[1] == "hold" and "trail_stop" in str(audit_row[2])

    # 计划修订硬闸：backfill 豁免冷却 / 禁覆盖已有 / 跨日冷却 / 禁放宽止损 / 2%台阶 / 临近止盈禁上调
    now = datetime(2026, 11, 19, 14, 30)
    old_pos = SayuPaperPosition(
        account_id=1,
        stock_code="600900",
        stock_name="长江电力",
        qty=1000,
        avg_cost=28.0,
        opened_at=now - timedelta(hours=1),
        plan_stop_price=None,
        plan_stop_pct=None,
        plan_take_price=None,
    )
    ok_err, ok_payload = ai_tools._validate_plan_update(
        old_pos,
        mode="balanced",
        current_price=30.8,
        revision_type="backfill",
        reason="老仓建仓未写止盈止损，现补齐结构化防守与压力位止盈",
        plan_stop_price=28.0,
        plan_take_price=33.5,
        now=now,
    )
    assert ok_err == "" and ok_payload["increment_rev"] is False

    planned = SayuPaperPosition(
        account_id=1,
        stock_code="601988",
        stock_name="中国银行",
        qty=2000,
        avg_cost=6.91,
        opened_at=now - timedelta(days=5),
        plan_stop_price=6.50,
        plan_stop_pct=-0.06,
        plan_take_price=7.46,
        plan_updated_at=now - timedelta(hours=2),
    )
    err_bf, _ = ai_tools._validate_plan_update(
        planned,
        mode="balanced",
        current_price=7.30,
        revision_type="backfill",
        reason="尝试用backfill覆盖已有计划应该被硬闸直接拒绝",
        plan_take_price=7.80,
        now=now,
    )
    assert "已有止盈计划" in err_bf
    err_cd, _ = ai_tools._validate_plan_update(
        planned,
        mode="balanced",
        current_price=7.30,
        revision_type="trail_stop",
        reason="刚改过又来上移止损应该触发跨日冷却硬闸拦截",
        plan_stop_price=6.91,
        now=now,
    )
    assert "冷却中" in err_cd

    cooled = SayuPaperPosition(
        account_id=1,
        stock_code="601988",
        stock_name="中国银行",
        qty=2000,
        avg_cost=6.91,
        opened_at=now - timedelta(days=5),
        plan_stop_price=6.50,
        plan_stop_pct=-0.06,
        plan_take_price=7.46,
        plan_updated_at=now - timedelta(days=2),
    )
    err_widen, _ = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.20,
        revision_type="trail_stop",
        reason="下跌后企图把止损往下挪动放宽应该被硬闸拒绝",
        plan_stop_price=6.40,
        now=now,
    )
    assert "严禁放宽止损" in err_widen
    err_step, _ = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.20,
        revision_type="trail_stop",
        reason="止损仅微调不到百分之一应该被最小台阶门槛拦截",
        plan_stop_price=6.55,
        now=now,
    )
    assert "变动过小" in err_step
    err_near, _ = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.38,
        revision_type="take_adjust",
        reason="距离原止盈不足2%临门抬高止盈逃避落袋应该拒绝",
        plan_take_price=8.00,
        now=now,
    )
    assert "临门抬高止盈" in err_near
    err_macro_up, _ = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.10,
        revision_type="macro_tighten",
        reason="宏观收紧模式下不得反而上调止盈目标应该被拒绝",
        plan_take_price=8.00,
        now=now,
    )
    assert "不得上调止盈价" in err_macro_up
    err_macro_empty, _ = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.10,
        revision_type="macro_tighten",
        reason="宏观收紧却只改逻辑文本不收紧任何止损止盈应该被拒绝",
        plan_thesis="仅改文字不收紧价格",
        now=now,
    )
    assert "不能只改 plan_thesis" in err_macro_empty
    err_same_stop, same_payload = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.10,
        revision_type="take_adjust",
        reason="重传未变更的原止损价6.50同时调整止盈价应视为未改止损并放行",
        plan_stop_price=6.50,
        plan_take_price=8.20,
        now=now,
    )
    assert err_same_stop == "" and same_payload["plan_stop_price"] is None and same_payload["plan_take_price"] == 8.2
    err_trail_ok, trail_payload = ai_tools._validate_plan_update(
        cooled,
        mode="balanced",
        current_price=7.30,
        revision_type="trail_stop",
        reason="浮盈接近6%且跨日冷却已满，将止损上移至成本保本位",
        plan_stop_price=6.91,
        now=now,
    )
    assert (
        err_trail_ok == ""
        and trail_payload["plan_stop_pct"] is None
        and trail_payload["clear_stop_pct"] is True
        and trail_payload["increment_rev"] is True
    )


def test_v2_migration_and_orphan_ledger_heal(tmp_path: Path) -> None:
    """v2去群主键迁徙（拆表级UNIQUE、回填account_id、孤儿归零、幂等）与启动账本/入场计划自愈。"""
    account_ddl = """
    CREATE TABLE sayupaperaccount (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id TEXT NOT NULL DEFAULT '',
        group_id TEXT NOT NULL DEFAULT '',
        cash REAL NOT NULL DEFAULT 0,
        initial_cash REAL NOT NULL DEFAULT 0,
        principal REAL NOT NULL DEFAULT 0,
        mode TEXT NOT NULL DEFAULT 'balanced',
        enabled INTEGER NOT NULL DEFAULT 1,
        kanban_init_root_id TEXT,
        kanban_period_root_id TEXT,
        created_at TIMESTAMP,
        name TEXT DEFAULT '',
        strategy_id TEXT DEFAULT '',
        strategy_params TEXT DEFAULT '',
        schema_migrated_v2 INTEGER DEFAULT 0,
        CONSTRAINT ux_sayupaperaccount_gid_bid UNIQUE (group_id, bot_id)
    )
    """
    child_ddl = """
    CREATE TABLE {table} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id TEXT NOT NULL DEFAULT '',
        group_id TEXT NOT NULL DEFAULT '',
        stock_code TEXT NOT NULL DEFAULT '',
        account_id INTEGER
    )
    """
    broadcast_ddl = """
    CREATE TABLE sayupaperbroadcasttarget (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL DEFAULT 0,
        bot_id TEXT NOT NULL DEFAULT '',
        bot_self_id TEXT NOT NULL DEFAULT '',
        ws_bot_id TEXT NOT NULL DEFAULT '',
        group_id TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP
    )
    """
    heal_alters = (
        "ALTER TABLE sayupaperposition ADD COLUMN qty INTEGER DEFAULT 0",
        "ALTER TABLE sayupaperposition ADD COLUMN stock_name TEXT DEFAULT ''",
        "ALTER TABLE sayupaperposition ADD COLUMN avg_cost REAL DEFAULT 0",
        "ALTER TABLE sayupaperposition ADD COLUMN last_quote_price REAL",
        "ALTER TABLE sayupaperposition ADD COLUMN opened_at TIMESTAMP",
        "ALTER TABLE sayupaperposition ADD COLUMN plan_stop_price REAL",
        "ALTER TABLE sayupaperposition ADD COLUMN plan_stop_pct REAL",
        "ALTER TABLE sayupaperposition ADD COLUMN plan_take_price REAL",
        "ALTER TABLE sayupaperposition ADD COLUMN plan_thesis TEXT",
        "ALTER TABLE sayupapertrade ADD COLUMN side TEXT DEFAULT 'buy'",
        "ALTER TABLE sayupapertrade ADD COLUMN qty INTEGER DEFAULT 0",
        "ALTER TABLE sayupapertrade ADD COLUMN price REAL DEFAULT 0",
        "ALTER TABLE sayupapertrade ADD COLUMN amount REAL DEFAULT 0",
        "ALTER TABLE sayupapertrade ADD COLUMN fee REAL DEFAULT 0",
        "ALTER TABLE sayupapertrade ADD COLUMN realized_pnl REAL DEFAULT 0",
        "ALTER TABLE sayupapertrade ADD COLUMN executed_at TIMESTAMP",
        "ALTER TABLE sayupapertrade ADD COLUMN snapshot TEXT DEFAULT ''",
        "ALTER TABLE sayupaperdecision ADD COLUMN action TEXT DEFAULT ''",
        "ALTER TABLE sayupaperdecision ADD COLUMN indicators TEXT DEFAULT ''",
        "ALTER TABLE sayupapersnapshot ADD COLUMN trade_date DATE",
        "ALTER TABLE sayupapersnapshot ADD COLUMN cash REAL DEFAULT 0",
        "ALTER TABLE sayupapersnapshot ADD COLUMN position_value REAL DEFAULT 0",
        "ALTER TABLE sayupapersnapshot ADD COLUMN total_equity REAL DEFAULT 0",
        "ALTER TABLE sayupapersnapshot ADD COLUMN day_pnl REAL DEFAULT 0",
        "ALTER TABLE sayupapersnapshot ADD COLUMN day_pnl_pct REAL DEFAULT 0",
        "ALTER TABLE sayupapersnapshot ADD COLUMN total_pnl REAL DEFAULT 0",
        "ALTER TABLE sayupapersnapshot ADD COLUMN total_pnl_pct REAL DEFAULT 0",
    )

    async def _run_on_db(name: str, body: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]) -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/{name}.db")
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.execute(text(account_ddl))
            for t in mig._CHILD_TABLES:
                await conn.execute(text(child_ddl.format(table=t)))
            await conn.execute(text(broadcast_ddl))
        with patch.object(mig, "async_maker", maker):
            try:
                await body(maker)
            finally:
                await engine.dispose()

    async def _test_v2(maker: async_sessionmaker[AsyncSession]) -> None:
        async with maker() as s:
            a_old = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO sayupaperaccount (group_id, bot_id, created_at) "
                            "VALUES ('111', 'onebot', '2025-01-01 09:00:00') RETURNING id"
                        )
                    )
                ).scalar()
                or 0
            )
            a_new = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO sayupaperaccount (group_id, bot_id, created_at) "
                            "VALUES ('222', 'onebot', '2026-01-01 09:00:00') RETURNING id"
                        )
                    )
                ).scalar()
                or 0
            )
            await s.execute(
                text(
                    "INSERT INTO sayupapertrade (group_id, bot_id, stock_code) "
                    "VALUES ('111', 'onebot', '600519'), ('999', 'onebot', '000001')"
                )
            )
            await s.commit()
        assert await mig.run_papertrade_v2_migration() is None
        async with maker() as s:
            accs = {
                int(r[0]): (r[1], r[2], r[3])
                for r in (
                    await s.execute(text("SELECT id, name, strategy_id, schema_migrated_v2 FROM sayupaperaccount"))
                ).all()
            }
            assert accs[a_old][0] == mig.DEFAULT_ACCOUNT_NAME and accs[a_new][0] == f"历史盘-{a_new}"
            assert all(v[1] == mig.DEFAULT_STRATEGY_ID and v[2] == 1 for v in accs.values())
            trades = (await s.execute(text("SELECT stock_code, account_id FROM sayupapertrade ORDER BY id"))).all()
            assert [tuple(r) for r in trades] == [("600519", a_old), ("000001", 0)]
            assert await mig._legacy_unique_alive(s) is False
            b_cnt = (
                await s.execute(
                    text("SELECT COUNT(*) FROM sayupaperbroadcasttarget WHERE account_id = :a"), {"a": a_old}
                )
            ).scalar()
            assert int(b_cnt or 0) == 1
        assert await mig.run_papertrade_v2_migration() is None

    async def _test_heal(maker: async_sessionmaker[AsyncSession]) -> None:
        async with maker() as s:
            for sql in heal_alters:
                await s.execute(text(sql))
            acc = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO sayupaperaccount (group_id, bot_id, created_at, cash, initial_cash) "
                            "VALUES ('111', 'onebot', '2025-01-01 09:00:00', 797860.0, 1000000.0) RETURNING id"
                        )
                    )
                ).scalar()
                or 0
            )
            await s.execute(
                text(
                    "INSERT INTO sayupaperposition (account_id, group_id, bot_id, stock_code, stock_name, "
                    "qty, avg_cost, last_quote_price, opened_at, plan_stop_price) VALUES "
                    "(:a, '111', 'onebot', '000333', '美的集团', 500, 88.21, 89.0, '2026-07-07', NULL),"
                    "(:a, '111', 'onebot', '600519', '贵州茅台', 100, 1600.0, 1600.0, '2026-07-07', NULL),"
                    "(:a, '111', 'onebot', '300308', '中际旭创', 100, 847.99, 846.0, '2026-08-25 14:31:37', NULL)"
                ),
                {"a": acc},
            )
            snap_midea = json.dumps(
                {"plan_stop_price": 83.36, "plan_stop_pct": -5.5, "plan_take_pct": 10.0, "plan_thesis": "白电蓝筹"},
                ensure_ascii=False,
            )
            ind_moutai = json.dumps(
                {"plan_stop_price": 1480.0, "plan_stop_pct": -0.075, "plan_thesis": "白酒龙头"}, ensure_ascii=False
            )
            await s.execute(
                text(
                    "INSERT INTO sayupapertrade (account_id, group_id, bot_id, stock_code, side, qty, price, "
                    "amount, fee, realized_pnl, executed_at, snapshot) VALUES "
                    "(:a,'111','onebot','000333','buy',500,88.21,44105.0,5.0,0.0,'2026-07-01 10:00:00',:snap),"
                    "(:a,'111','onebot','600519','buy',100,1600.0,160000.0,5.0,0.0,'2026-07-01 10:00:00',''),"
                    "(:a,'111','onebot','000001','buy',100,100.0,10000.0,5.0,0.0,'2026-07-01 10:00:00',''),"
                    "(:a,'111','onebot','000001','sell',100,110.0,11000.0,10.0,990.0,'2026-07-02 10:00:00','')"
                ),
                {"a": acc, "snap": snap_midea},
            )
            await s.execute(
                text(
                    "INSERT INTO sayupaperdecision (account_id, group_id, bot_id, stock_code, action, indicators) "
                    "VALUES (:a, '111', 'onebot', '600519', 'buy', :ind)"
                ),
                {"a": acc, "ind": ind_moutai},
            )
            await s.execute(
                text(
                    "INSERT INTO sayupapersnapshot (account_id, group_id, bot_id, stock_code, trade_date, "
                    "cash, position_value, total_equity, day_pnl, day_pnl_pct, total_pnl, total_pnl_pct) "
                    "VALUES (:a, '111', 'onebot', '', '2026-08-25', 1001975.0, 84600.0, 1086575.0, "
                    "0.0, 0.0, 0.0, 0.0)"
                ),
                {"a": acc},
            )
            await s.commit()

        first = await mig.heal_orphan_ledger()
        second = await mig.heal_orphan_ledger()
        assert first["orphans"][0]["stock_code"] == "300308" and second["orphans"] == []
        assert first["cash_restated"] and second["cash_restated"] == []
        assert first["positions_plan_healed"] == 2 and second["positions_plan_healed"] == 0
        async with maker() as s:
            codes = [
                str(r[0])
                for r in (await s.execute(text("SELECT stock_code FROM sayupaperposition ORDER BY stock_code"))).all()
            ]
            assert codes == ["000333", "600519"]
            midea_plan = (
                await s.execute(
                    text(
                        "SELECT plan_stop_price, plan_stop_pct, plan_take_price "
                        "FROM sayupaperposition WHERE stock_code = '000333'"
                    )
                )
            ).first()
            moutai_stop = (
                await s.execute(text("SELECT plan_stop_price FROM sayupaperposition WHERE stock_code = '600519'"))
            ).scalar()
            assert midea_plan is not None
            assert abs(float(midea_plan[0]) - 83.36) < 0.001
            assert abs(float(midea_plan[1]) + 0.055) < 1e-4
            assert abs(float(midea_plan[2]) - 88.21 * 1.1) < 0.01
            assert abs(float(moutai_stop) - 1480.0) < 0.001

    asyncio.run(_run_on_db("v2", _test_v2))
    asyncio.run(_run_on_db("heal", _test_heal))
