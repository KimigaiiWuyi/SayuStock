"""宏观事件用户命令：查看 / 手动刷新。实际发送要带插件前缀（``a宏观事件``）。"""

from __future__ import annotations

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from .sv import sv_macro
from .repo import MacroEventRepo
from .ai_tools import format_macro_events_text
from ..utils.database.macro_models import MACRO_STATUS_OPEN, MACRO_STATUS_SETTLED
from ..stock_papertrade.permissions import check_admin


@sv_macro.on_fullmatch(
    ("宏观事件", "宏观事件列表", "模拟盘宏观事件"),
    to_ai="查看 AI 正在跟踪的宏观重大事件（关税战 / 地缘 / 央行 / 油价）及最新进展。无参数。",
)
async def send_macro_events(bot: Bot, ev: Event) -> None:
    open_rows = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=10)
    settled_rows = await MacroEventRepo.list_by_status(MACRO_STATUS_SETTLED, limit=3)
    text = format_macro_events_text(open_rows)
    if settled_rows:
        names = "、".join(r.title for r in settled_rows)
        text += f"\n\n最近已定论：{names}"
    text += "\n\n管理员可发「宏观事件刷新」立即联网复核。"
    await bot.send(text)


@sv_macro.on_fullmatch(
    ("宏观事件刷新", "刷新宏观事件"),
    to_ai="立即联网复核宏观重大事件表并登记新事件（仅群主 / 管理员）。无参数。",
)
async def send_macro_refresh(bot: Bot, ev: Event) -> None:
    if not await check_admin(ev):
        await bot.send("⚠️ 仅群主 / 管理员可触发宏观事件刷新")
        return
    from .heartbeat import run_macro_heartbeat

    await bot.send("⏳ 正在联网复核宏观事件，约 1~3 分钟…")
    status = await run_macro_heartbeat(force=True, ev=ev)
    rows = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=10)
    await bot.send(f"✅ {status}\n\n{format_macro_events_text(rows)}")
