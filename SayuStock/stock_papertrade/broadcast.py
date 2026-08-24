"""模拟盘播报：把一个账户的成交/通知扇出到它订阅的所有群。

**为什么不能直接复用触发上下文的 Event**（老实现的做法）：多盘之后，成交是
Kanban cron 里发生的，此时根本没有"触发上下文"——心跳树的 Event 是初始化那一刻
的那个群。而一个盘可能要同时推 3 个群，Event 只有一个 group_id。

所以播报目标必须是**数据**（``SayuPaperBroadcastTarget`` 表），每个目标独立
造一个 Event 去投递。造 Event 时三个字段少一个都会投错：

- ``ws_bot_id`` → ``emit_proactive_message`` **只按** ``Event.WS_BOT_ID`` 在
  ``gss.active_bot`` 里挑连接；缺了或过期会返回 False，群里看起来像静默。
  迁移种子 / 控制台手填常常没有这个字段。本模块在投递前会回退到当前在线连接，
  并把选中的 id 写回 Event。多适配器时仍应在目标群重发「模拟盘推送添加」锁死连接。
- ``bot_self_id`` → 进 ``Event.session_id``。缺了 session_id 会与真实会话对不上，
  主动消息同步进错的 AI 会话历史。
- ``bot_id`` → 适配器类型（onebot/discord/...），路由的第一跳。

三者都在建目标那一刻从真实 Event 上抄下来存库，见 ``PaperBroadcastRepo.add``。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from gsuid_core.logger import logger
from gsuid_core.models import Event

from . import db
from ..utils.database.papertrade_models import (
    SayuPaperAccount,
    SayuPaperBroadcastTarget,
)

ProactiveSource = Literal["heartbeat", "scheduled_task", "kanban", "tool"]

__all__ = [
    "event_for_target",
    "broadcast_text",
    "broadcast_fill",
    "format_fill_line",
]


def event_for_target(target: SayuPaperBroadcastTarget) -> Event:
    """按播报目标造一个可投递的 ``Event``。

    ``user_id`` 用 ``created_by``（加这个群的人）而不是空串：``emit_proactive_message``
    会把主动消息同步进 ``user_id`` 的 AI 会话历史，空串会让所有盘的播报堆进同一条
    "空用户"会话里。
    """
    return Event(
        bot_id=target.bot_id or "Bot",
        bot_self_id=target.bot_self_id or "",
        user_type="group",
        group_id=target.group_id or None,
        user_id=target.created_by or "",
        user_pm=0,
        WS_BOT_ID=target.ws_bot_id or None,
        real_bot_id=target.ws_bot_id or "",
    )


async def _targets(account_id: int) -> List[SayuPaperBroadcastTarget]:
    try:
        return await db.PaperBroadcastRepo.list_by_account(account_id, enabled_only=True)
    except Exception as e:
        logger.warning(f"[SayuStock][PaperTrade] 读播报目标失败 account_id={account_id}: {e}")
        return []


def _bind_live_ws(event: Event) -> object | None:
    """给 Event 补上当前能用的 WS 连接。

    ``emit_proactive_message`` 在 ``bot is None`` 时只认 ``event.WS_BOT_ID``；
    迁移种子把 ws_bot_id 留空，直接投会「无可用 Bot」然后静默。
    单连接部署回退到那一条；多连接时先用第一条并打 warning，运维应在群里补「推送添加」。
    """
    from gsuid_core.gss import gss

    bots = gss.active_bot
    wanted = (event.WS_BOT_ID or "").strip()
    if wanted and wanted in bots:
        return bots[wanted]
    if not bots:
        return None
    ws_id, raw = next(iter(bots.items()))
    logger.warning(
        f"[SayuStock][PaperTrade] 播报 Event 无有效 ws_bot_id（存的是 {wanted!r}），"
        f"回退到在线连接 {ws_id}；请在目标群重发「模拟盘推送添加」锁死路由"
    )
    event.WS_BOT_ID = ws_id
    event.real_bot_id = ws_id
    return raw


async def _call_emitter(
    *,
    event: Event,
    message: str,
    source: ProactiveSource,
    trigger_reason: str,
    suppress_when_heartbeat_recent: bool,
    bot: object,
) -> bool:
    from gsuid_core.ai_core.proactive.emitter import emit_proactive_message

    return bool(
        await emit_proactive_message(
            event=event,
            message=message,
            source=source,
            trigger_reason=trigger_reason,
            suppress_when_heartbeat_recent=suppress_when_heartbeat_recent,
            bot=bot,
        )
    )


async def _emit(
    *,
    event: Event,
    message: str,
    source: ProactiveSource,
    trigger_reason: str,
    suppress_when_heartbeat_recent: bool,
) -> bool:
    from gsuid_core.bot import Bot

    raw = _bind_live_ws(event)
    if raw is None:
        logger.warning(
            f"[SayuStock][PaperTrade] 无在线 Bot，跳过播报 group={event.group_id} ws_bot_id={event.WS_BOT_ID!r}"
        )
        return False
    bot = Bot(raw, event)
    return await _call_emitter(
        event=event,
        message=message,
        source=source,
        trigger_reason=trigger_reason,
        suppress_when_heartbeat_recent=suppress_when_heartbeat_recent,
        bot=bot,
    )


async def broadcast_text(
    account_id: int,
    message: str,
    *,
    trigger_reason: str,
    source: ProactiveSource = "tool",
    suppress_when_heartbeat_recent: bool = False,
) -> int:
    """把一段文本推给账户订阅的所有群，返回成功条数。

    **绝不抛异常**：播报失败不能连累已经落库的成交。单个群失败（bot 掉线 / 被踢）
    记 warning 并继续推下一个群 —— 一个群挂掉不该让其它群收不到。
    """
    if account_id <= 0 or not message:
        return 0
    targets = await _targets(account_id)
    if not targets:
        logger.warning(f"[SayuStock][PaperTrade] 账户 {account_id} 无启用中的播报目标，跳过播报")
        return 0

    ok: int = 0
    for t in targets:
        try:
            sent = await _emit(
                event=event_for_target(t),
                message=message,
                source=source,
                trigger_reason=trigger_reason,
                suppress_when_heartbeat_recent=suppress_when_heartbeat_recent,
            )
            if sent:
                ok += 1
            else:
                logger.warning(
                    f"[SayuStock][PaperTrade] 播报到群 {t.group_id} 未发出 "
                    f"(ws_bot_id={t.ws_bot_id!r} bot_self_id={t.bot_self_id!r})"
                )
        except Exception as e:
            logger.warning(f"[SayuStock][PaperTrade] 播报到群 {t.group_id} 失败（已跳过）: {e}")
    if ok == 0:
        logger.warning(f"[SayuStock][PaperTrade] 账户 {account_id} 成交播报 0/{len(targets)} 成功：{message[:80]}")
    return ok


def format_fill_line(
    *,
    account_name: str,
    side: str,
    stock_code: str,
    stock_name: str,
    qty: int,
    price: float,
    realized_pnl: float,
    show_account: bool = True,
) -> str:
    """成交冒泡文案。

    多盘之后**必须带盘名前缀**：同一个群可能同时订阅 2 个盘的播报，不带前缀
    用户看到两条"🟢 买入 XX"根本分不清是哪个策略下的手。
    """
    name: str = stock_name or stock_code
    prefix: str = f"[{account_name}] " if show_account and account_name else ""
    if side == "sell":
        sign: str = "+" if realized_pnl >= 0 else "-"
        return f"{prefix}🔴 卖出 {name}({stock_code}) {qty} 股 @¥{price:.2f}（{sign}¥{abs(realized_pnl):,.0f}）"
    return f"{prefix}🟢 买入 {name}({stock_code}) {qty} 股 @¥{price:.2f}"


async def broadcast_fill(
    account: Optional[SayuPaperAccount],
    *,
    side: str,
    stock_code: str,
    stock_name: str,
    qty: int,
    price: float,
    realized_pnl: float,
) -> int:
    """成交后的确定性播报（buy/sell 都推，一行冒泡）。

    这是**系统级播报**，不依赖决策代理的最终输出（代理最终永远只出
    ``<<NO_BROADCAST>>``）。每次 ``papertrade_trade_insert`` 成功即调一次。
    """
    if account is None or account.id is None:
        return 0
    line = format_fill_line(
        account_name=account.name,
        side=side,
        stock_code=stock_code,
        stock_name=stock_name,
        qty=qty,
        price=price,
        realized_pnl=realized_pnl,
    )
    return await broadcast_text(
        account.id,
        line,
        trigger_reason=f"papertrade_fill:{account.name}:{stock_code}:{side}",
        source="tool",
        suppress_when_heartbeat_recent=False,  # 成交播报是关键信息，不被心跳抑制
    )
