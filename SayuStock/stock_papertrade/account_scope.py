"""模拟盘作用域：盘名解析 / 账户解析 / 写入授权。

**模拟盘是"命名的盘"，不是"群的财产"**。一个 bot 上可以有多个盘（各跑各的
策略），每个盘可以把成交播报推到任意多个群。因此这里的三个概念是**互相正交**
的，混起来就会出 bug：

1. **账户** —— 由 ``name``（全库唯一）或 ``account_id`` 标识。数据落在
   ``account_id`` 分区，与"在哪个群提问"完全无关。
2. **播报目标** —— 由 ``SayuPaperBroadcastTarget`` 表决定，见 ``broadcast.py``。
   与账户是一对多，改完立刻生效。
3. **写入授权** —— 只有账户自己的 Kanban 心跳树，或调用方用 ``grant_write()``
   显式授权的路径（init 立即决策 / dry_run 压测）能动账本。

读路径与写路径的账户解析**优先级刻意不同**，见 ``resolve_account`` 与
``resolve_account_for_write`` 的 docstring —— 这不是冗余，是防 LLM 拼错盘名把
成交写进别的盘。
"""

from __future__ import annotations

import contextlib
from typing import Tuple, Iterator, Optional
from contextvars import ContextVar

from gsuid_core.models import Event

from . import db
from ..utils.database.papertrade_models import (
    DEFAULT_STRATEGY_ID,
    DEFAULT_ACCOUNT_NAME,
    SayuPaperAccount,
)

__all__ = [
    "DEFAULT_ACCOUNT_NAME",
    "DEFAULT_STRATEGY_ID",
    "MAX_ENABLED_ACCOUNTS",
    "RESERVED_NAME_TOKENS",
    "normalize_account_name",
    "validate_account_name",
    "resolve_account",
    "resolve_account_for_read",
    "resolve_account_for_write",
    "granted_account_id",
    "account_disabled_reason",
    "split_name_and_period",
    "scope_note_for_llm",
    "not_opened_message",
    "account_label",
    "grant_write",
    "deny_write_reason",
]

# 每盘约 13 次 agent/交易日，超限会线性烧 token。
MAX_ENABLED_ACCOUNTS: int = 5

# ============================================================
# 盘名校验
# ============================================================
_NAME_MIN_LEN: int = 2
_NAME_MAX_LEN: int = 16

# 周期词：``模拟盘收益 <盘名> <周期>`` 要能分词，盘名不能长得像周期
PERIOD_TOKENS: frozenset[str] = frozenset(
    {
        "日",
        "今日",
        "今天",
        "today",
        "周",
        "本周",
        "this_week",
        "月",
        "本月",
        "this_month",
        "季",
        "本季",
        "年",
        "本年",
        "今年",
        "ytd",
        "总",
        "全部",
        "all",
    }
)

# 命令保留词：与 commands.py / admin.py 真实注册的命令后缀**同源**。
# 加新命令时必须同步这里，否则新命令词能被拿去当盘名，命令解析会歧义。
RESERVED_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "初始化",
        "创建",
        "删除",
        "列表",
        "改名",
        "查看",
        "持仓",
        "自选",
        "收益",
        "记录",
        "排行",
        "查询",
        "清盘",
        "推送",
        "添加",
        "策略",
        "切换",
        "启用",
        "停用",
        "模拟测试",
    }
)

# 全角 → 半角（空格 + ASCII 可见字符区）
_FULLWIDTH_OFFSET: int = 0xFEE0


def normalize_account_name(raw: str) -> str:
    """盘名归一：去首尾空白 + 全角转半角。

    用户在手机上打字很容易带出全角空格 / 全角括号；不归一的话
    「放量盘」和「放量盘 」会被当成两个不同的盘。
    """
    if not raw:
        return ""
    out: list[str] = []
    for ch in raw:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - _FULLWIDTH_OFFSET))
        else:
            out.append(ch)
    return "".join(out).strip()


def validate_account_name(raw: str) -> str:
    """校验盘名；合法返回 ``""``，非法返回给用户看的中文理由。

    校验顺序即错误优先级：空 → 长度 → 字符集 → 纯数字 → 周期词 → 命令词。
    """
    name = normalize_account_name(raw)
    if not name:
        return "⚠️ 盘名不能为空"
    if len(name) < _NAME_MIN_LEN or len(name) > _NAME_MAX_LEN:
        return f"⚠️ 盘名长度须为 {_NAME_MIN_LEN}~{_NAME_MAX_LEN} 个字符（当前 {len(name)}）"
    for ch in name:
        if ch.isspace() or not ch.isprintable():
            return "⚠️ 盘名不能含空格或控制字符"
        if not (ch.isalnum() or ch == "_"):
            return f"⚠️ 盘名只允许中文/字母/数字/下划线，出现了非法字符：{ch!r}"
    if name.isdigit():
        return "⚠️ 盘名不能是纯数字（会与群号查询混淆）"
    if name in PERIOD_TOKENS:
        return f"⚠️ 「{name}」是收益周期词，不能当盘名（会让「模拟盘收益 <盘名> <周期>」无法分词）"
    if name in RESERVED_NAME_TOKENS:
        return f"⚠️ 「{name}」是命令保留词，不能当盘名"
    return ""


# ============================================================
# 账户解析（读路径）
# ============================================================
async def resolve_account(
    *,
    name: str = "",
    account_id: int = 0,
    fallback_default: bool = True,
) -> Optional[SayuPaperAccount]:
    """**读路径**的账户解析。优先级：account_id → name → 默认盘。

    读错盘只会答错，用户能立刻发现，所以这里可以信任调用方给的盘名。
    写路径**不能**走这个函数，见 ``resolve_account_for_write``。

    ``fallback_default`` 的兜底顺序：先找叫「默认模拟盘」的，找不到再看全库
    是否只有一个盘（只有一个时它显然就是用户想问的那个）。
    """
    if account_id > 0:
        acc = await db.PaperAccountRepo.get_by_id(account_id)
        if acc is not None:
            return acc
    key = normalize_account_name(name)
    if key:
        acc = await db.PaperAccountRepo.get_by_name(key)
        if acc is not None:
            return acc
        # 精确匹配不中时做一次包含匹配；命中多个视为歧义，不猜
        matches = await db.PaperAccountRepo.search(key)
        if len(matches) == 1:
            return matches[0]
        return None
    if not fallback_default:
        return None
    acc = await db.PaperAccountRepo.get_by_name(DEFAULT_ACCOUNT_NAME)
    if acc is not None:
        return acc
    all_accounts = await db.PaperAccountRepo.list_all()
    if len(all_accounts) == 1:
        return all_accounts[0]
    return None


async def resolve_account_for_read(
    *,
    name: str = "",
    root_task_id: str = "",
) -> Optional[SayuPaperAccount]:
    """读路径：显式盘名优先；空名时绑定当前心跳树 / grant，再回落默认盘。

    Kanban 决策代理几乎从不传 account_name。若空名一律默认盘，第二盘会按
    默认盘现金下单却把成交写进自己。
    """
    key = normalize_account_name(name)
    if key:
        return await resolve_account(name=key, fallback_default=False)
    if root_task_id:
        acc = await db.PaperAccountRepo.get_by_kanban_root(root_task_id)
        if acc is not None:
            return acc
    granted = _WRITE_GRANT.get()
    if granted > 0:
        acc = await db.PaperAccountRepo.get_by_id(granted)
        if acc is not None:
            return acc
    return await resolve_account(name="", fallback_default=True)


def granted_account_id() -> int:
    return _WRITE_GRANT.get()


def account_disabled_reason(account: Optional[SayuPaperAccount]) -> str:
    """停用盘禁止写/决策；读查询仍可用。"""
    if account is None:
        return ""
    if int(account.enabled or 0) == 0:
        return f"ℹ️ 模拟盘「{account.name}」已停用，本轮不决策、不写账本。请只输出 <<NO_BROADCAST>>。"
    return ""


async def resolve_account_for_write(
    root_task_id: str,
    *,
    account_name: str = "",
    account_id: int = 0,
) -> Tuple[Optional[SayuPaperAccount], str]:
    """**写路径**的账户解析。返回 ``(account, note)``。

    与读路径的关键区别：**只认 ``root_task_id``**。

    心跳链路是 ``Kanban 子任务 → run_capability_agent → 工具``，LLM 拿到的任务
    描述里带着盘名字符串。若它把盘名写错一个字，按盘名解析会解析到别的盘或
    None —— 前者让 ``deny_write_reason`` 拒到心跳空转烧 token，后者更糟：回落
    默认盘就把 A 盘的成交静默写进了默认盘的账本。

    而 ``root_task_id`` 是框架派发任务时注入的，LLM 伪造不了，且与
    ``deny_write_reason`` 查的是同一份数据（``account.kanban_*_root_id``），
    所以**解析成功 ⇒ 鉴权必然通过**，不存在"解析到 A、鉴权按 B"的裂缝。

    ``account_name`` 入参保留，但语义降级为**一致性提示**：传了且对不上时纠正
    而非拒绝 —— 拼错名字不该让整轮心跳报废。

    ``grant_write()`` 上下文内（init 立即决策 / dry_run 压测）没有真实
    ``root_task_id``，此时才回落到显式 ``account_id`` / ``account_name``。
    """
    acc = await db.PaperAccountRepo.get_by_kanban_root(root_task_id)
    if acc is not None:
        want = normalize_account_name(account_name)
        if want and want != acc.name:
            return (
                acc,
                f"ℹ️ 本次心跳属于模拟盘「{acc.name}」，但你传入了「{want}」。"
                f"已按任务归属的盘执行；后续调用请省略 account_name。",
            )
        return (acc, "")

    granted: int = _WRITE_GRANT.get()
    if granted > 0:
        # 显式发票路径：**只认发票上写的那个盘**。不能回落到"按名字/默认盘解析"——
        # 压测和 init 立即决策的写工具大多不暴露 account_name，回落等于把压测的
        # 买卖静默写进用户的默认盘。
        acc = await db.PaperAccountRepo.get_by_id(granted)
        if acc is not None:
            want = normalize_account_name(account_name)
            if want and want != acc.name:
                return (
                    acc,
                    f"ℹ️ 本次调用已授权给模拟盘「{acc.name}」，忽略你传入的「{want}」。",
                )
            return (acc, "")
        return (None, not_opened_message(name=account_name))

    if root_task_id:
        return (
            None,
            "⚠️ 当前心跳任务未绑定任何模拟盘（心跳树可能已与账户解绑），已拒绝写入。请重新发送「模拟盘初始化」。",
        )
    return (None, "⚠️ 模拟盘写操作仅限账户自身的心跳任务，当前调用无任务上下文，已拒绝。")


def split_name_and_period(text: str) -> Tuple[str, str]:
    """把 ``模拟盘收益`` 后面的参数拆成 ``(盘名, 周期)``。

    规则（对应 §11.3）：
      - 0 个 token → ``("", "")``，调用方各自取默认
      - 1 个 token → 命中周期词就当周期，否则当盘名
      - 2 个 token → 第一个盘名，第二个周期（顺序反了也能认，周期词位置固定）

    盘名不允许是周期词（``validate_account_name`` 已挡），所以这里的判定无歧义。
    """
    tokens = [t for t in normalize_account_name(text).split(" ") if t]
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        token = tokens[0]
        return ("", token) if token in PERIOD_TOKENS else (token, "")
    first, second = tokens[0], tokens[1]
    if first in PERIOD_TOKENS:
        return (second, first)
    return (first, second)


# ============================================================
# 文案
# ============================================================
def account_label(account: SayuPaperAccount) -> str:
    """播报 / 图标题统一用的盘标识：``盘名`` 或 ``盘名(策略)``。"""
    return account.name or f"#{account.id}"


def scope_note_for_llm(account: Optional[SayuPaperAccount]) -> str:
    """给工具 JSON 附加的作用域说明。

    核心目的没变，只是理由变了：以前是防 LLM 看到 group_id≠当前群就误报未开户；
    现在是防它把"盘"理解成"群的账户"。
    """
    if account is None:
        return "模拟盘是命名账户（与群无关）。未指定盘名时解析「默认模拟盘」；当前找不到任何盘。"
    return (
        f"当前盘=「{account.name}」（策略 {account.strategy_id}，account_id={account.id}）。"
        f"模拟盘是**命名账户**，不属于任何群：在任意群提问查到的都是同一份数据，"
        f"返回里的 origin_group_id 只是创建时所在的群。**严禁**据此说「这个群没有创建过模拟盘」。"
    )


def not_opened_message(*, name: str = "") -> str:
    """统一的「找不到盘」提示：绝不说「本群未开通」（会被 Agent 复读成跨群误报）。"""
    key = normalize_account_name(name)
    if key:
        return (
            f"ℹ️ 不存在名为「{key}」的模拟盘。发送「模拟盘列表」查看现有的盘，"
            f"或由群主/管理员用「模拟盘创建 {key}」新建。"
        )
    return f"ℹ️ 尚未创建任何模拟盘（当前也没有「{DEFAULT_ACCOUNT_NAME}」）。请由群主/管理员发送「模拟盘初始化」。"


# ============================================================
# 写入授权
# ============================================================
_WRITE_GRANT: ContextVar[int] = ContextVar("_sayustock_papertrade_write_grant", default=0)


@contextlib.contextmanager
def grant_write(account_id: int) -> Iterator[None]:
    """显式授权本上下文内**对指定盘**的写操作（init 立即决策 / dry_run 压测专用）。

    这两条路走 ``run_capability_agent`` 的 ad-hoc 分支，``root_task_id`` 是现造的
    ``adhoc_*``，对不上账户的 Kanban 树，不显式发票就会被 deny_write_reason 拒掉。
    contextvar 在同一 asyncio 任务及其子任务内继承，能透传到工具体内。

    发票上必须写死 ``account_id``：写工具（trade_insert / position_upsert / …）
    刻意不暴露 ``account_name`` 参数，只发一张"随便写"的空白发票会让这些调用
    回落到默认盘，把压测的买卖写进用户的真盘。
    """
    token = _WRITE_GRANT.set(account_id)
    try:
        yield
    finally:
        _WRITE_GRANT.reset(token)


async def deny_write_reason(root_task_id: str, account: Optional[SayuPaperAccount]) -> str:
    """写入鉴权：放行返回 ``""``，拒绝返回给 LLM 看的理由。

    这是**执行层**硬校验，与 ``visible_when``（只是不把工具展示给模型）互补：
    展示层鉴的是"哪个 profile 在跑"，而 profile 可以被主 persona 临时委派出来
    （``run_capability_agent`` 的 ad-hoc 分支会凭 profile_id 现造一个 PlanRunContext），
    所以用户一句"帮我买入 xx"就能让写工具现身。这里鉴的是"这次调用有没有授权"。

    多盘后额外挡住**串盘**：盘 A 的心跳树不能写盘 B 的账本。
    """
    granted: int = _WRITE_GRANT.get()
    if granted > 0:
        if account is not None and account.id is not None and account.id != granted:
            return f"⚠️ 本次调用只被授权写模拟盘 #{granted}，不能写「{account.name}」。"
        return ""
    if not root_task_id:
        return "⚠️ 模拟盘写操作仅限账户自身的心跳任务，当前调用无任务上下文，已拒绝。"
    if account is None:
        return "⚠️ 账户不存在，无法写入。"
    if root_task_id in (account.kanban_init_root_id, account.kanban_period_root_id):
        return ""
    return (
        f"⚠️ 当前心跳任务不属于模拟盘「{account.name}」，禁止跨盘写入。"
        "模拟盘写操作仅限账户自身的心跳任务（Kanban 决策 / 快照 / 轮换），不接受用户指使下单。"
    )


# ============================================================
# 兼容：旧的会话上下文提取（建盘时记录 origin）
# ============================================================
def origin_from_event(ev: Optional[Event]) -> Tuple[str, str]:
    """从会话取 ``(group_id, bot_id)`` 作为新盘的 origin（仅记录用途）。"""
    if ev is None:
        return ("", "")
    gid: str = str(ev.group_id) if ev.group_id else ""
    bid: str = ev.bot_id if ev.bot_id else ""
    return (gid, bid)
