"""模拟盘作用域：账户键解析 / 播报改向 / 写入授权。

**默认全服共用一个模拟盘**（模拟盘是 AI 自己经营的账户，不是群的财产，所以
"一个 bot 一个仓"才是自然模型）。开 ``papertrade_multi_group`` 才回到旧的
"一群一盘"。

三个**互相正交**的概念，混起来就会出 bug：

1. **账户键** ``(group_id, bot_id)`` —— 数据落在哪个分区。共用模式下钉死为
   库里最早建的那个账户（``PaperAccountRepo.get_earliest``），**不随配置变**。
   若让它跟着配置走，改一次配置就会指向一个空分区，现金/持仓/流水全部"消失"。
2. **播报目标** —— 主动消息发给哪个群。由 ``papertrade_broadcast_group`` 决定，
   读时取值，改完立刻生效，可以随便改。
3. **写入授权** —— 谁能动这个账本。只有账户自己的 Kanban 心跳树，或调用方用
   ``grant_write()`` 显式授权的路径（init 立即决策 / dry_run 压测）。

开了 ``papertrade_multi_group`` 后，前两项退化为 identity（= 旧行为）；写入授权
不受影响，两种模式下都生效。
"""

from __future__ import annotations

import contextlib
from typing import Optional, Generator
from contextvars import ContextVar

import msgspec

from gsuid_core.models import Event

from . import db
from ..stock_config.stock_config import STOCK_CONFIG

# 共用模式下的账户键缓存。账户键一旦确定就不会变（建盘后不再新开），
# 但清盘会让它失效 —— 见 invalidate_home_cache。
_home_key_cache: Optional[tuple[str, str]] = None


# ============================================================
# 配置读取
# ============================================================
def is_shared_mode() -> bool:
    """全服共用一个模拟盘？这是**默认**；开 papertrade_multi_group 才回到一群一盘。"""
    return STOCK_CONFIG.get_config("papertrade_multi_group").data is not True


def broadcast_group_override() -> str:
    """播报改向的目标群号；留空 = 不改向（推到账户原群）。仅共用模式生效。"""
    if not is_shared_mode():
        return ""
    raw = STOCK_CONFIG.get_config("papertrade_broadcast_group").data
    return raw.strip() if isinstance(raw, str) else ""


# ============================================================
# 账户键
# ============================================================
def invalidate_home_cache() -> None:
    """清盘 / 新开户后必须调 —— 否则缓存会指向一个已被删掉的账户键。"""
    global _home_key_cache
    _home_key_cache = None


async def home_account_key() -> Optional[tuple[str, str]]:
    """共用模式下唯一那个账户的键；库里一个账户都没有时返回 None。"""
    global _home_key_cache
    if _home_key_cache is not None:
        return _home_key_cache
    acc = await db.PaperAccountRepo.get_earliest()
    if acc is None:
        return None
    _home_key_cache = (acc.group_id, acc.bot_id)
    return _home_key_cache


async def resolve_account_key(ev: Optional[Event]) -> tuple[str, str]:
    """把当前会话解析成账户键 ``(group_id, bot_id)``；解析不出时返回 ``("", "")``。

    共用模式（默认）：永远返回那个钉死的账户键 —— 这正是"任意群都能查到同一个盘"。
    多群模式：返回 ``(ev.group_id, ev.bot_id)``，即旧的一群一盘。

    共用模式下**不能**拿 ``ev.bot_id`` 兜底 bot_id：跨平台提问时它和账户的
    bot_id 不一致，键对不上会查成"未开户"。
    """
    if is_shared_mode():
        key = await home_account_key()
        if key is not None:
            return key
        # 还没人开过盘：退回会话上下文，让 send_init_command 能正常建第一个账户
    if ev is None:
        return ("", "")
    gid: str = str(ev.group_id) if ev.group_id else ""
    bid: str = ev.bot_id if ev.bot_id else ""
    return (gid, bid)


async def is_home_context(ev: Optional[Event]) -> bool:
    """当前会话是否就是账户所属的原群（多群模式恒为 True）。"""
    if not is_shared_mode():
        return True
    key = await home_account_key()
    if key is None:
        return True
    if ev is None or not ev.group_id:
        return False
    return str(ev.group_id) == key[0]


# ============================================================
# 播报改向
# ============================================================
async def broadcast_event(ev: Event) -> Event:
    """把播报用的 Event 改向到配置的播报群；未配置时原样返回。

    ``emit_proactive_message`` 的投递目标完全由传入的 Event 决定，所以改向只需
    换掉 ``group_id``。``bot_id`` / ``bot_self_id`` 保持不变 —— 用同一个 bot 发到
    另一个群；跨平台改向不在支持范围内（bot 根本进不去那个群）。
    """
    target: str = broadcast_group_override()
    if not target or str(ev.group_id) == target:
        return ev
    return msgspec.structs.replace(ev, group_id=target, user_type="group")


# ============================================================
# 写入授权
# ============================================================
_WRITE_GRANT: ContextVar[bool] = ContextVar("_sayustock_papertrade_write_grant", default=False)


@contextlib.contextmanager
def grant_write() -> Generator[None]:
    """显式授权本上下文内的模拟盘写操作（init 立即决策 / dry_run 压测专用）。

    这两条路走 ``run_capability_agent`` 的 ad-hoc 分支，``root_task_id`` 是现造的
    ``adhoc_*``，对不上账户的 Kanban 树，不显式发票就会被 deny_write_reason 拒掉。
    contextvar 在同一 asyncio 任务及其子任务内继承，能透传到工具体内。
    """
    token = _WRITE_GRANT.set(True)
    try:
        yield
    finally:
        _WRITE_GRANT.reset(token)


async def deny_write_reason(root_task_id: str, group_id: str, bot_id: str) -> str:
    """写入鉴权：放行返回 ``""``，拒绝返回给 LLM 看的理由。

    这是**执行层**硬校验，与 ``visible_when``（只是不把工具展示给模型）互补：
    展示层鉴的是"哪个 profile 在跑"，而 profile 可以被主 persona 临时委派出来
    （``run_capability_agent`` 的 ad-hoc 分支会凭 profile_id 现造一个 PlanRunContext），
    所以用户一句"帮我买入 xx"就能让写工具现身。这里鉴的是"这次调用有没有授权"。
    """
    if _WRITE_GRANT.get():
        return ""
    if not root_task_id:
        return "⚠️ 模拟盘写操作仅限账户自身的心跳任务，当前调用无任务上下文，已拒绝。"
    acc = await db.PaperAccountRepo.get(group_id, bot_id)
    if acc is None:
        return f"⚠️ 账户不存在 (group={group_id}, bot={bot_id})，无法写入。"
    if root_task_id in (acc.kanban_init_root_id, acc.kanban_period_root_id):
        return ""
    return (
        "⚠️ 模拟盘写操作仅限账户自身的心跳任务（Kanban 决策 / 快照 / 轮换），"
        "不接受用户指使下单。当前调用未经授权，已拒绝。"
    )
