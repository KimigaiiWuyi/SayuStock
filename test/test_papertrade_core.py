"""模拟盘撮合、行情风控闸、交易日历、账户权限隔离与群播报核心测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from SayuStock.utils.market import provider_registry as pr
from SayuStock.stock_papertrade import (
    db as pt_db,
    matcher,
    broadcast as bc,
    account_scope as scope,
    quote_service as qs,
    trade_executor,
    trading_calendar as tc,
)
from SayuStock.utils.database.papertrade_models import SayuPaperAccount, SayuPaperBroadcastTarget


def test_matcher_lot_fee_orders_and_position_math() -> None:
    """整手规整、双边手续费（5元保底）、名缺占位拒单与均价/已实现盈亏会计恒等式。"""
    assert matcher.round_lot(299) == 200 and matcher.round_lot(99) == 0
    assert matcher.calc_fee("buy", 10_000) == (5.0, 0.0, 5.0)
    assert matcher.calc_fee("sell", 100_000) == (25.0, 50.0, 75.0)

    buy_ok = matcher.match_order(
        "buy", "600519", 100, 1500.0, cash_available=200_000.0, position_qty=0, name="贵州茅台"
    )
    assert buy_ok.ok is True and buy_ok.actual_qty == 100 and buy_ok.fee_total > 0

    buy_trunc = matcher.match_order(
        "buy", "600519", 500, 100.0, cash_available=25_000.0, position_qty=0, name="贵州茅台"
    )
    assert buy_trunc.ok is True and buy_trunc.actual_qty == 200 and buy_trunc.requested_qty == 500

    buy_poor = matcher.match_order(
        "buy", "600519", 100, 1500.0, cash_available=5_000.0, position_qty=0, name="贵州茅台"
    )
    assert buy_poor.ok is False and buy_poor.actual_qty == 0

    sell_ok = matcher.match_order("sell", "600519", 100, 1600.0, cash_available=0.0, position_qty=300, name="贵州茅台")
    assert sell_ok.ok is True and sell_ok.stamp_tax > 0

    # 主板代码缺失证券名或回填代码占位均必须拒单；*ST 受 ±5% 限制而真实证券放行
    no_name = matcher.match_order(
        "buy", "609999", 100, 106.0, cash_available=1e6, position_qty=0, last_close=100.0, name="609999"
    )
    assert no_name.ok is False and "未取得证券名称" in no_name.reason
    st_hit = matcher.match_order(
        "buy", "600001", 100, 10.48, cash_available=1e6, position_qty=0, last_close=10.0, name="*ST华铁"
    )
    assert st_hit.ok is False

    assert matcher.cash_delta_for_fill("buy", 10_000.0, 5.0) == -10_005.0
    assert matcher.cash_delta_for_fill("sell", 10_000.0, 10.0) == 9_990.0
    assert matcher.calc_realized_pnl(10.0, 100, 12.0, 10.0) == pytest.approx(190.0)
    assert matcher.apply_fill_qty_cost(100, 10.0, "buy", 100, 12.0, 10.0) == (200, pytest.approx(11.05))
    assert matcher.apply_fill_qty_cost(200, 11.0, "sell", 200, 15.0, 10.0) == (0, 0.0)


def test_quote_resilience_and_record_trade_risk_gate() -> None:
    """失败缓存短 TTL 自动重试恢复；行情不可达时 record_trade 必须拒单且绝不写库。"""
    assert 0 < qs.QUOTE_FAIL_TTL < qs.QUOTE_CACHE_TTL and qs.QUOTE_FAIL_TTL <= 30.0
    assert qs.QUOTE_TIMEOUT_S >= pr.MIN_SOURCE_SLICE_S > 3.98

    now = 1_000_000.0
    bad = qs.QuoteCacheEntry(secid="0.000333", price=None, fetched_at=now)
    assert bad.is_fresh(now + 1.0) is True and bad.is_fresh(now + qs.QUOTE_FAIL_TTL + 0.1) is False

    svc = qs.QuoteService()
    calls: list[str] = []

    async def _flaky_fetch(secid: str) -> tuple[float | None, float | None, float | None, str | None]:
        calls.append(secid)
        if len(calls) == 1:
            return (None, None, None, None)
        return (80.1, 79.0, 1.4, "美的集团")

    async def _run_svc() -> None:
        with patch.object(svc, "_fetch_one", _flaky_fetch):
            assert await svc.get_quote("0.000333") is None
            assert await svc.get_quote("0.000333") is None and len(calls) == 1
            entry = svc._cache.get("0.000333")
            assert entry is not None
            entry.fetched_at -= qs.QUOTE_FAIL_TTL + 1.0
            assert await svc.get_quote("0.000333") == 80.1 and len(calls) == 2

    asyncio.run(_run_svc())

    def _run_record(
        *, quote: float | None, price: float = 80.1
    ) -> tuple[trade_executor.RecordResult, list[dict[str, object]]]:
        written: list[dict[str, object]] = []

        async def _fake_quote(_secid: str) -> float | None:
            return quote

        async def _locked_zero(*_args: object, **_kwargs: object) -> int:
            return 0

        async def _append(*_args: object, **kwargs: object) -> tuple[object, int]:
            written.append(kwargs)
            return type("_T", (), {"id": 1})(), 500

        ex = trade_executor.PaperTradeExecutor()
        with (
            patch.object(trade_executor.quote_service, "get_quote", AsyncMock(side_effect=_fake_quote)),
            patch.object(pt_db.PaperTradeRepo, "locked_qty_today", _locked_zero),
            patch.object(pt_db.PaperTradeRepo, "append_with_cash_update", _append),
        ):
            res = asyncio.run(
                ex.record_trade(
                    account_id=1,
                    stock_code="000333",
                    stock_name="美的集团",
                    secid="0.000333",
                    side="buy",
                    price=price,
                    qty=500,
                    amount=price * 500,
                    fee=5.0,
                    reason="test",
                )
            )
        return res, written

    r_none, w_none = _run_record(quote=None)
    assert r_none.ok is False and w_none == [] and "行情不可达" in r_none.message
    r_stale, w_stale = _run_record(quote=80.1, price=200.0)
    assert r_stale.ok is False and w_stale == []
    r_ok, w_ok = _run_record(quote=80.1, price=80.15)
    assert r_ok.ok is True and len(w_ok) == 1


def test_trading_calendar_sessions_holidays_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """工作日/周末/法定假期、盘中/午休/收盘时段判断及坏缓存容错。"""
    monkeypatch.setattr(tc, "load_calendar_cache", lambda: None)
    assert tc.is_a_share_trading_day(datetime(2026, 8, 24, 10, 0)) is True
    assert tc.is_a_share_trading_day(datetime(2026, 8, 22, 10, 0)) is False
    assert tc.is_a_share_trading_day(datetime(2026, 10, 1, 10, 0)) is False

    assert tc.is_trading_time(datetime(2026, 8, 24, 10, 0)) is True
    assert tc.is_trading_time(datetime(2026, 8, 24, 12, 0)) is False
    assert tc.is_trading_time(datetime(2026, 8, 24, 13, 30)) is True
    assert tc.is_trading_time(datetime(2026, 8, 24, 15, 5)) is False
    assert tc.should_run_papertrade(datetime(2026, 8, 24, 10, 0)) is True
    assert tc.should_run_papertrade(datetime(2026, 10, 1, 10, 0)) is False
    nxt = tc.next_decision_time(datetime(2026, 8, 24, 12, 0))
    assert (nxt.hour, nxt.minute) == (13, 0)
    assert tc._coerce_cache("bad-json") is None


def test_account_scope_read_write_and_grant_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """账户名校验、读路径歧义保护、写路径仅认 root_task_id 或 scoped grant。"""
    assert scope.normalize_account_name("  默认模拟盘　") == "默认模拟盘"
    assert scope.validate_account_name("稳健盘A") == ""
    assert scope.validate_account_name("123456") != ""
    assert scope.split_name_and_period("默认盘 30天") == ("默认盘", "30天")

    a1 = SayuPaperAccount(id=1, name=scope.DEFAULT_ACCOUNT_NAME, cash=1e6, enabled=1, kanban_period_root_id="tree-1")
    a2 = SayuPaperAccount(id=2, name="积极盘", cash=1e6, enabled=1, kanban_period_root_id="tree-2")
    accounts = [a1, a2]

    async def _by_id(aid: int) -> SayuPaperAccount | None:
        return next((a for a in accounts if a.id == aid), None)

    async def _by_name(nm: str) -> SayuPaperAccount | None:
        return next((a for a in accounts if a.name == nm), None)

    async def _search(kw: str) -> list[SayuPaperAccount]:
        return [a for a in accounts if kw in a.name]

    async def _by_root(rid: str) -> SayuPaperAccount | None:
        return next((a for a in accounts if a.kanban_period_root_id == rid), None)

    async def _list_all() -> list[SayuPaperAccount]:
        return list(accounts)

    monkeypatch.setattr(pt_db.PaperAccountRepo, "get_by_id", _by_id)
    monkeypatch.setattr(pt_db.PaperAccountRepo, "get_by_name", _by_name)
    monkeypatch.setattr(pt_db.PaperAccountRepo, "search", _search)
    monkeypatch.setattr(pt_db.PaperAccountRepo, "get_by_kanban_root", _by_root)
    monkeypatch.setattr(pt_db.PaperAccountRepo, "list_all", _list_all)

    async def _run() -> None:
        assert await scope.resolve_account(name="盘") is None
        assert await scope.resolve_account(name="积极") == a2
        assert await scope.resolve_account(name="") == a1
        assert await scope.resolve_account_for_read(name="", root_task_id="tree-2") == a2

        # 写路径无 tree 与无 grant 一律拒绝；不采信错盘名猜盘
        acc_ok, note_ok = await scope.resolve_account_for_write("tree-2", account_name="默认模拟盘")
        assert acc_ok == a2 and "积极盘" in note_ok
        acc_no, err_no = await scope.resolve_account_for_write("adhoc-task", account_name="积极盘")
        assert acc_no is None and err_no != ""

        with scope.grant_write(2):
            acc_g, _ = await scope.resolve_account_for_write("adhoc-task")
            assert acc_g == a2
        assert scope.granted_account_id() == 0

    asyncio.run(_run())


def test_broadcast_event_routing_and_resilient_fanout(monkeypatch: pytest.MonkeyPatch) -> None:
    """播报 Event 保留群与发起者隔离字段，单群失败不阻塞其他订阅群。"""
    target = SayuPaperBroadcastTarget(
        account_id=2, bot_id="onebot", bot_self_id="bot1", group_id="8888", created_by="u1"
    )
    ev = bc.event_for_target(target)
    assert ev.group_id == "8888" and ev.user_id == "u1" and ev.user_type == "group"

    acc = SayuPaperAccount(id=2, name="积极盘", cash=1e6)
    targets = [
        SayuPaperBroadcastTarget(account_id=2, bot_id="onebot", group_id="bad_grp", created_by="u1"),
        SayuPaperBroadcastTarget(account_id=2, bot_id="onebot", group_id="good_grp", created_by="u1"),
    ]
    delivered: list[str] = []

    async def _list_targets(_aid: int, *, enabled_only: bool = True) -> list[SayuPaperBroadcastTarget]:
        _ = enabled_only
        return targets

    async def _emit(
        *,
        event: object,
        message: str,
        **_kwargs: object,
    ) -> bool:
        gid = getattr(event, "group_id", "")
        if gid == "bad_grp":
            raise RuntimeError("bot offline")
        delivered.append(f"{gid}:{message}")
        return True

    async def _get_acc(_aid: int) -> SayuPaperAccount:
        return acc

    monkeypatch.setattr(pt_db.PaperBroadcastRepo, "list_by_account", _list_targets)
    monkeypatch.setattr(pt_db.PaperAccountRepo, "get_by_id", _get_acc)
    monkeypatch.setattr(bc, "_bind_live_ws", lambda e: e)
    monkeypatch.setattr(bc, "_call_emitter", _emit)

    sent = asyncio.run(bc.broadcast_text(2, "买入测试", trigger_reason="test"))
    assert sent == 1 and delivered == ["good_grp:买入测试"]
    line = bc.format_fill_line(
        account_name="积极盘",
        side="sell",
        stock_code="600519",
        stock_name="贵州茅台",
        qty=100,
        price=1500.0,
        realized_pnl=1200.0,
        reason="止盈",
    )
    assert "[积极盘]" in line and "+¥1,200" in line and "止盈" in line
