"""模拟盘播报扇出单测。

多盘之后成交发生在 Kanban cron 里，没有"触发上下文"可以复用，而一个盘可能要同时
推 3 个群——Event 只有一个 group_id。所以播报目标是**数据**，每个目标独立造一个
Event 去投递。造 Event 时 ``ws_bot_id`` / ``bot_self_id`` / ``bot_id`` 三者少一个
就会静默投错连接（消息发不出去，还没有报错），这是最难排查的一类故障，因此这里
逐字段钉死。

另外两条不变量：单个群失败不能连累其它群，播报失败不能连累已经落库的成交。
"""

import sys
import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from SayuStock.stock_papertrade import db as pt_db, broadcast as bc  # noqa: E402


def _target(
    group_id: str,
    *,
    bot_id: str = "onebot",
    bot_self_id: str = "10001",
    ws_bot_id: str = "ws-1",
    created_by: str = "u1",
) -> Any:
    return SimpleNamespace(
        group_id=group_id,
        bot_id=bot_id,
        bot_self_id=bot_self_id,
        ws_bot_id=ws_bot_id,
        created_by=created_by,
        enabled=1,
    )


class _Emitter:
    """替身 emit_proactive_message；``fail_groups`` 里的群会抛异常。"""

    def __init__(self, *, fail_groups: tuple = (), returns: bool = True) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.fail_groups = set(fail_groups)
        self.returns = returns

    async def __call__(self, *, event, message, source, trigger_reason, suppress_when_heartbeat_recent):
        if event.group_id in self.fail_groups:
            raise RuntimeError("bot 掉线")
        self.calls.append(
            {
                "group_id": event.group_id,
                "session_id": event.session_id,
                "message": message,
                "source": source,
                "trigger_reason": trigger_reason,
                "suppress": suppress_when_heartbeat_recent,
            }
        )
        return self.returns


def _run(targets: List[Any], emitter: _Emitter, coro_factory):
    """替换掉 Repo 与 emitter，跑一次播报。"""
    original_list = pt_db.PaperBroadcastRepo.list_by_account
    original_emit = bc._emit

    async def fake_list(account_id: int, *, enabled_only: bool = True):
        return list(targets)

    pt_db.PaperBroadcastRepo.list_by_account = staticmethod(fake_list)  # type: ignore[assignment]
    bc._emit = emitter  # type: ignore[assignment]
    try:
        return asyncio.run(coro_factory())
    finally:
        pt_db.PaperBroadcastRepo.list_by_account = original_list  # type: ignore[assignment]
        bc._emit = original_emit  # type: ignore[assignment]


# ============================================================
# 1) Event 组装：三个字段少一个都会投错
# ============================================================
def test_event_carries_the_routing_fields():
    ev = bc.event_for_target(_target("111"))
    # ws_bot_id 决定 _resolve_active_bot 挑哪条连接；缺了会随便挑一个，
    # 多适配器部署下能把 QQ 的消息发进 Discord 的连接
    assert ev.WS_BOT_ID == "ws-1"
    assert ev.bot_id == "onebot"
    assert ev.bot_self_id == "10001"
    assert ev.group_id == "111"
    assert ev.user_type == "group"


def test_event_session_id_matches_a_real_group_session():
    """session_id 由 (ws_bot_id, bot_id, bot_self_id, group_id) 拼成；错一位就会
    把主动消息同步进另一条 AI 会话历史。"""
    ev = bc.event_for_target(_target("111"))
    assert ev.session_id == "ws-1:onebot:10001:group:111"


def test_event_user_id_is_the_subscriber():
    """空 user_id 会让所有盘的播报堆进同一条"空用户"会话里。"""
    assert bc.event_for_target(_target("111", created_by="u42")).user_id == "u42"


def test_event_degrades_gracefully_when_fields_missing():
    """迁移种子出来的目标没有 ws_bot_id / bot_self_id，也必须造得出 Event。"""
    ev = bc.event_for_target(_target("111", ws_bot_id="", bot_self_id="", bot_id=""))
    assert ev.WS_BOT_ID is None
    assert ev.bot_id == "Bot"
    assert ev.session_id.endswith(":group:111")


# ============================================================
# 2) 扇出
# ============================================================
def test_broadcast_reaches_every_subscribed_group():
    emitter = _Emitter()
    targets = [_target("111"), _target("222"), _target("333")]
    sent = _run(targets, emitter, lambda: bc.broadcast_text(1, "hi", trigger_reason="t"))
    assert sent == 3
    assert [c["group_id"] for c in emitter.calls] == ["111", "222", "333"]


def test_one_dead_group_does_not_block_the_others():
    """一个群把 bot 踢了，其它群照样要收到成交播报。"""
    emitter = _Emitter(fail_groups=("222",))
    targets = [_target("111"), _target("222"), _target("333")]
    sent = _run(targets, emitter, lambda: bc.broadcast_text(1, "hi", trigger_reason="t"))
    assert sent == 2
    assert [c["group_id"] for c in emitter.calls] == ["111", "333"]


def test_all_groups_failing_returns_zero_without_raising():
    """播报失败不能连累已经落库的成交——绝不向上抛。"""
    emitter = _Emitter(fail_groups=("111", "222"))
    targets = [_target("111"), _target("222")]
    assert _run(targets, emitter, lambda: bc.broadcast_text(1, "hi", trigger_reason="t")) == 0


def test_no_targets_means_no_emit():
    emitter = _Emitter()
    assert _run([], emitter, lambda: bc.broadcast_text(1, "hi", trigger_reason="t")) == 0
    assert emitter.calls == []


def test_invalid_input_short_circuits():
    emitter = _Emitter()
    assert _run([_target("111")], emitter, lambda: bc.broadcast_text(0, "hi", trigger_reason="t")) == 0
    assert _run([_target("111")], emitter, lambda: bc.broadcast_text(1, "", trigger_reason="t")) == 0
    assert emitter.calls == []


# ============================================================
# 3) 成交文案
# ============================================================
def test_fill_line_carries_the_account_name():
    """同一个群可能订阅 2 个盘，不带盘名前缀用户分不清是哪个策略下的手。"""
    line = bc.format_fill_line(
        account_name="放量盘",
        side="buy",
        stock_code="600519",
        stock_name="贵州茅台",
        qty=100,
        price=1600.0,
        realized_pnl=0.0,
    )
    assert line.startswith("[放量盘] ")
    assert "买入" in line and "600519" in line and "100" in line


def test_fill_line_shows_realized_pnl_sign_on_sell():
    win = bc.format_fill_line(
        account_name="放量盘",
        side="sell",
        stock_code="600519",
        stock_name="贵州茅台",
        qty=100,
        price=1700.0,
        realized_pnl=9800.0,
    )
    loss = bc.format_fill_line(
        account_name="放量盘",
        side="sell",
        stock_code="600519",
        stock_name="贵州茅台",
        qty=100,
        price=1500.0,
        realized_pnl=-9800.0,
    )
    assert "+¥9,800" in win
    assert "-¥9,800" in loss


def test_fill_line_falls_back_to_code_when_name_missing():
    line = bc.format_fill_line(
        account_name="",
        side="buy",
        stock_code="600519",
        stock_name="",
        qty=100,
        price=10.0,
        realized_pnl=0.0,
    )
    assert line.startswith("🟢")  # 无盘名时不加空前缀
    assert "600519" in line


# ============================================================
# 4) broadcast_fill
# ============================================================
def test_broadcast_fill_pushes_a_line_per_group():
    emitter = _Emitter()
    account = SimpleNamespace(id=7, name="放量盘")
    sent = _run(
        [_target("111"), _target("222")],
        emitter,
        lambda: bc.broadcast_fill(
            account,
            side="buy",
            stock_code="600519",
            stock_name="贵州茅台",
            qty=100,
            price=1600.0,
            realized_pnl=0.0,
        ),
    )
    assert sent == 2
    assert all("[放量盘]" in c["message"] for c in emitter.calls)
    # 成交播报是关键信息，不能被"最近有心跳"抑制掉
    assert all(c["suppress"] is False for c in emitter.calls)
    assert all("papertrade_fill:放量盘:600519:buy" == c["trigger_reason"] for c in emitter.calls)


def test_broadcast_fill_ignores_missing_account():
    emitter = _Emitter()
    sent = _run(
        [_target("111")],
        emitter,
        lambda: bc.broadcast_fill(
            None,
            side="buy",
            stock_code="600519",
            stock_name="贵州茅台",
            qty=100,
            price=1600.0,
            realized_pnl=0.0,
        ),
    )
    assert sent == 0
    assert emitter.calls == []


# ============================================================
# 5) 迁移种子空 ws_bot_id：必须回退到在线连接，不能静默
# ============================================================
def test_bind_live_ws_keeps_explicit_connection(monkeypatch):
    live = object()
    monkeypatch.setattr(
        "gsuid_core.gss.gss",
        SimpleNamespace(active_bot={"ws-1": live, "ws-2": object()}),
    )
    ev = bc.event_for_target(_target("111", ws_bot_id="ws-1"))
    assert bc._bind_live_ws(ev) is live
    assert ev.WS_BOT_ID == "ws-1"


def test_bind_live_ws_falls_back_when_empty(monkeypatch):
    live = object()
    monkeypatch.setattr("gsuid_core.gss.gss", SimpleNamespace(active_bot={"ws-live": live}))
    ev = bc.event_for_target(_target("111", ws_bot_id=""))
    assert ev.WS_BOT_ID is None
    assert bc._bind_live_ws(ev) is live
    assert ev.WS_BOT_ID == "ws-live"
    assert ev.real_bot_id == "ws-live"


def test_bind_live_ws_returns_none_without_connections(monkeypatch):
    monkeypatch.setattr("gsuid_core.gss.gss", SimpleNamespace(active_bot={}))
    ev = bc.event_for_target(_target("111", ws_bot_id=""))
    assert bc._bind_live_ws(ev) is None


def test_emit_passes_fallback_bot_into_proactive(monkeypatch):
    """空 ws_bot_id 时必须把 Bot 实例交给 emitter，否则 Core 会「无可用 Bot」直接 False。"""
    live = object()
    monkeypatch.setattr("gsuid_core.gss.gss", SimpleNamespace(active_bot={"ws-live": live}))

    class FakeBot:
        def __init__(self, raw: object, ev: object) -> None:
            self.raw = raw
            self.ev = ev

    captured: Dict[str, Any] = {}

    async def fake_call(**kwargs: Any) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr("gsuid_core.bot.Bot", FakeBot)
    monkeypatch.setattr(bc, "_call_emitter", fake_call)
    ev = bc.event_for_target(_target("666249732", ws_bot_id=""))
    ok = asyncio.run(
        bc._emit(
            event=ev,
            message="hi",
            source="tool",
            trigger_reason="papertrade_fill:默认模拟盘:688981:sell",
            suppress_when_heartbeat_recent=False,
        )
    )
    assert ok is True
    assert captured["bot"].raw is live
    assert captured["event"].WS_BOT_ID == "ws-live"
    assert captured["suppress_when_heartbeat_recent"] is False
    assert captured["source"] == "tool"
