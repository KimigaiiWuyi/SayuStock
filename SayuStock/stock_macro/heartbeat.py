"""宏观事件心跳：定时踢 ``macro_event_agent`` 联网复核事件表。

为什么不挂在每个模拟盘的 Kanban 树上：事件表是全局的，N 个盘就会复核 N 遍；而且
Kanban 子任务是建树时快照，老盘不重建心跳树就拿不到新任务。用插件级 APScheduler
一份 cron 全局跑，老盘、新盘、持仓分析都读同一张刷新过的表，零迁移。

节奏：每天 08:40 / 12:40 / 20:40。关税、地缘类消息常在周末与夜间落地，
所以不按 A 股交易日过滤；下一次决策心跳开盘前一定能读到刷新过的表。
"""

from __future__ import annotations

import asyncio
from typing import Optional
from datetime import datetime

from gsuid_core.aps import scheduler
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .repo import MacroEventRepo
from .agent import MACRO_EVENT_AGENT_ID
from ..utils.database.macro_models import MACRO_STATUS_OPEN

__all__ = ["run_macro_heartbeat", "macro_heartbeat_enabled"]

_LOG = "[SayuStock][Macro]"
_HEARTBEAT_LOCK = asyncio.Lock()
_CRON_JOB_ID = "sayustock_macro_event_heartbeat"


def macro_heartbeat_enabled() -> bool:
    from ..stock_config.stock_config import STOCK_CONFIG

    raw = STOCK_CONFIG.get_config("macro_event_heartbeat").data
    return bool(raw) if isinstance(raw, bool) else True


async def _has_consumer() -> bool:
    """有启用中的模拟盘、或表里已有 open 事件，才值得花 token 复核。"""
    from ..stock_papertrade import db as _pt_db

    if await _pt_db.PaperAccountRepo.list_enabled():
        return True
    return bool(await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=1))


async def run_macro_heartbeat(*, force: bool = False, ev: Optional[Event] = None) -> str:
    """跑一轮宏观复核；返回给调用方看的一行状态（cron 只记日志，命令会回给用户）。

    ``force=True`` 跳过开关与消费者检查（手动命令用）。同一时刻只允许一轮在跑。
    """
    if not force and not macro_heartbeat_enabled():
        return "宏观事件心跳已在配置中关闭（macro_event_heartbeat=false）"
    if not force and not await _has_consumer():
        return "无启用中的模拟盘且事件表为空，本轮跳过"
    if _HEARTBEAT_LOCK.locked():
        return "上一轮宏观复核仍在进行，本轮跳过"

    from gsuid_core.ai_core.capability_agents.runner import run_capability_agent

    now = datetime.now()
    task = (
        f"执行一次宏观事件复核心跳（{now:%Y-%m-%d %H:%M}）。"
        "按你的步骤 1~5 依次执行：先 macro_event_refresh_due，复核到期事件并 upsert，"
        "再扫描新重大事件并登记。最终只输出 <<NO_BROADCAST>>。"
    )
    async with _HEARTBEAT_LOCK:
        logger.info(f"{_LOG} 宏观事件心跳开始 {now:%H:%M}")
        result = await run_capability_agent(
            profile_id=MACRO_EVENT_AGENT_ID,
            task=task,
            ev=ev,
            bot=None,
            session_id_suffix=f"macro_{now:%Y%m%d_%H}",
        )
    open_rows = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=100)
    summary = f"宏观事件心跳完成：当前进行中 {len(open_rows)} 件"
    logger.info(f"{_LOG} {summary}；代理返回 {result[:80]!r}")
    return summary


@scheduler.scheduled_job("cron", hour="8,12,20", minute=40, id=_CRON_JOB_ID)
async def _macro_heartbeat_job() -> None:
    status = await run_macro_heartbeat()
    logger.info(f"{_LOG} cron: {status}")
