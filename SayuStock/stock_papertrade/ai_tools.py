"""模拟盘 ai_tools 集合。

15 个 ai_tools，分三类（**没有重叠**，每个工具只做一件事）：
- 业务/账本（**6 个只读**，capability_domain="AI模拟盘"，category="common"）
  —— 主 persona + 能力代理都能用
  —— 含 ``papertrade_holdings_image``：仿「我的自选」的持仓简图（含今日涨跌+持仓浮盈）
- 能力代理私有（**6 个写**，capability_domain="AI模拟盘"，category="default" + visible_when）
  —— 仅 papertrade_*_agent 可见；防止主 persona 误调写操作
  —— decision_insert / trade_insert / position_upsert / candidate_refresh /
     match_order / snapshot_write
- 通用辅助（3 个，capability_domain="AI模拟盘"，category="common"）
  —— 财报 / 指标 / 交易日判断

**删除的工具**（已收敛到 trigger 或被废弃）：
- ~~papertrade_account_create~~ —— 与 trigger ``send_init_command`` 重叠；统一走 trigger
- ~~papertrade_account_update~~ —— 死代码（没有命令 / 流程使用）

**所有 group_id 参数**：默认（全服共用一个盘）一律解析到那个钉死的账户，与提问
来自哪个群无关；开了 ``papertrade_multi_group`` 才退回从 ctx.deps.ev.group_id 推断。

**写工具的两道闸**（见 ``account_scope``）：``visible_when`` 只是不把工具端到模型
面前，真正鉴权的是工具体内的 ``_deny_write`` —— 只有账户自己的 Kanban 心跳树、
或 ``grant_write()`` 显式授权的路径能写，用户指使一律拒。
"""

import json
import datetime as _dt
from typing import Any, Set, Dict, TypedDict

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.ai_core.planning.runtime import PlanRunContext, get_plan_context

from . import db, account_scope
from .indicators import compute_indicators
from ..utils.market import KlinePeriod, get_market, is_market_error
from .quote_service import quote_service
from .trading_calendar import (
    is_trading_time,
    trading_day_summary,
    is_a_share_trading_day,
)
from ..utils.eastmoney_finance import (
    get_cash_flow,
    get_balance_sheet,
    get_income_statement,
    get_financial_snapshot,
)
from ..utils.stock.request_utils import get_code_id
from ..utils.market.convert.dataframe import kline_to_df
from ..utils.database.papertrade_models import SayuPaperPosition

# ============================================================
# 语境工具池标签（context_tags）
#
# 2026-07-02 修复"@早柚问持仓却答空仓"的召回断链：主 persona 的工具是按用户
# 这句话**语义召回**装配的，"持仓 / 账户 / 收益"很容易被 send_my_stock（查
# 用户**个人自选股**的 trigger 命令）+ 通用 record_* 抢走名额，导致真正读
# SQLModel 三张表的 papertrade_account_query / papertrade_position_list
# **压根没进主 persona 的工具清单** → persona 只能瞎猜 record_list /
# artifact_get_recent（读错存储）→ 报"空仓"。
#
# 声明 context_tags 后，框架在**金融/股票语境的群**里会把这几个只读账本工具
# 直接**按群画像标签自动装配**（get_tools_by_context_tags），不再依赖单句
# 语义召回是否命中——问"你现在什么持仓"时它们已经在工具池里。
#
# 标签同时覆盖群画像常见的中英文写法（匹配大小写不敏感、按标签重叠数打分）。
# ============================================================
_PAPERTRADE_CTX_TAGS: list[str] = [
    "Stock",
    "Finance",
    "股票",
    "金融",
    "投资",
    "模拟盘",
    "持仓",
]


# ============================================================
# TypedDict：每个 ai_tool 的 JSON 输出契约，供下游 JSON 序列化
# ============================================================
class _AccountView(TypedDict):
    group_id: str
    bot_id: str
    shared_mode: bool
    scope_note: str
    cash: float
    initial_cash: float
    principal: float
    position_value: float
    total_equity: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float
    realized_pnl: float
    position_count: int
    quote_stale_count: int
    mode: str
    frequency_minutes: int
    enabled: int
    kanban_init_root_id: str | None
    kanban_period_root_id: str | None
    last_decided_at: str | None


class _PositionItem(TypedDict):
    stock_code: str
    stock_name: str
    secid: str
    qty: int
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    quote_age_seconds: int | None
    quote_source: str  # "live" | "db" | "cost"
    opened_at: str | None


class _TradeItem(TypedDict):
    id: int
    stock_code: str
    stock_name: str
    side: str
    price: float
    qty: int
    amount: float
    fee: float
    realized_pnl: float
    reason: str
    snapshot: str
    decision_id: int | None
    mode: str
    decided_at: str | None
    executed_at: str | None


class _WatchlistItem(TypedDict):
    stock_code: str
    stock_name: str
    user_id: str
    note: str
    created_at: str | None


class _MatchResultView(TypedDict):
    ok: bool
    side: str
    code: str
    requested_qty: int
    actual_qty: int
    price: float  # 实际成交价（= 撮合时刻实时行情价，不是调用方传的参考价）
    requested_price: float  # 调用方传入的参考价（0=未传）
    price_source: str  # "live"（实时行情） / "caller"（行情不可达时的降级，仅诊断）
    amount: float
    commission: float
    stamp_tax: float
    fee_total: float
    reason: str


class _TradingDayView(TypedDict):
    is_trading_day: bool
    is_trading_time: bool
    should_decide: bool
    desc: str


# ============================================================
# 上下文推断辅助
# ============================================================
# 写工具的 profile 白名单。**逐个枚举而非前缀匹配**：``startswith("papertrade_")``
# 意味着任何人新注册一个叫 papertrade_xxx 的画像就白拿下单权限。
_WRITE_AGENT_PROFILES: frozenset[str] = frozenset(
    {
        "papertrade_setup_agent",
        "papertrade_decision_agent",
        "papertrade_snapshot_agent",
        "papertrade_pool_refresh_agent",
        "papertrade_reporter_agent",
    }
)


async def _resolve_scope(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> tuple[str, str]:
    """解析本次调用的账户键。共用模式（默认）下恒为那个钉死的账户，与提问来自哪个群无关。

    显式传入的 group_id / bot_id 只在多群模式下生效（跨群查询用）；共用模式下
    忽略它们 —— 全服只有一个盘，让 LLM 传当前群号也不会改指向。
    """
    deps = getattr(ctx, "deps", None)
    ev = getattr(deps, "ev", None) if deps is not None else None
    if account_scope.is_shared_mode():
        return await account_scope.resolve_account_key(ev)
    gid: str = str(group_id) if group_id else (str(ev.group_id) if ev and ev.group_id else "")
    bid: str = bot_id if bot_id else (ev.bot_id if ev and ev.bot_id else "")
    return (gid, bid)


def _current_profile() -> str:
    """当前执行体画像；不在任务上下文里（如主 persona 直聊）时为空串。"""
    plan_ctx: PlanRunContext | None = get_plan_context()
    return plan_ctx.agent_profile if plan_ctx is not None else ""


def _visible_to_papertrade_agent(ctx: RunContext[ToolContext]) -> bool:
    """写工具的展示层过滤（visible_when）。

    只是"不把工具端到模型面前"，**不是鉴权** —— profile 能被主 persona 临时委派
    出来。真正的闸门是每个写工具体内的 ``_deny_write``。
    """
    return _current_profile() in _WRITE_AGENT_PROFILES


async def _deny_write(ctx: RunContext[ToolContext], group_id: str, bot_id: str) -> str:
    """写工具的执行层鉴权；放行返回 ``""``，否则返回给 LLM 的拒绝理由。"""
    plan_ctx: PlanRunContext | None = get_plan_context()
    root_task_id: str = plan_ctx.root_task_id if plan_ctx is not None else ""
    return await account_scope.deny_write_reason(root_task_id, group_id, bot_id)


# ============================================================
# 0) Enrich Helper：把持仓补上"现价 / 市值 / 浮盈"
#
# 2026-07-01 新增：``papertrade_position_list`` / ``papertrade_account_query``
# 之前不返回现价，agent 拿到数据后无法算持仓市值 / 浮盈 / 总资产。本 helper
# 自动：
#
#   1. 读 DB 持仓
#   2. 找出 ``last_quote_at`` 超过 ``max_stale_seconds`` 的行（含 None）
#   3. 把 secid 喂 ``quote_service.get_quotes_batch``（60s TTL 内存缓存 + 东财 push2）
#   4. 成功的报价 ``PaperPositionRepo.bulk_set_quote`` 写回 DB（让下次调用立即看到）
#   5. 失败的报价降级 ``last_quote_price → avg_cost``
#
# 返回值每条带 ``quote_source`` 字段："live" / "db" / "cost"，LLM 据此知道
# 数据新鲜度，避免拿着过期报价瞎决策。
# ============================================================
from gsuid_core.logger import logger as _gslogger  # noqa: E402  -- pyright 看不见根包导入


async def _broadcast_fill(
    ctx: RunContext[ToolContext],
    *,
    side: str,
    stock_code: str,
    stock_name: str,
    qty: int,
    price: float,
    realized_pnl: float,
) -> None:
    """成交后向群里推一行简洁冒泡（buy/sell 都推）。

    这是**系统级确定性播报**——不依赖决策代理的最终输出（代理最终永远只出
    ``<<NO_BROADCAST>>``）。每次 ``papertrade_trade_insert`` 成功即调一次，保证
    "全部买卖都在群里公布"，且只公布这一行、不带任何决策推理 / 账户汇总。
    失败只记 debug、绝不抛出（已落库的成交不能被播报失败连累）。

    投递目标由 ``account_scope.broadcast_event`` 决定：配了播报群就改向到那儿，
    没配就还是推到触发上下文的群（老行为）。配置读时取值，改完立刻生效。
    """
    ev = ctx.deps.ev
    if ev is None:
        return
    ev = await account_scope.broadcast_event(ev)
    name: str = stock_name or stock_code
    if side == "sell":
        sign: str = "+" if realized_pnl >= 0 else "-"
        line = f"🔴 卖出 {name}({stock_code}) {qty} 股 @¥{price:.2f}（{sign}¥{abs(realized_pnl):,.0f}）"
    else:
        line = f"🟢 买入 {name}({stock_code}) {qty} 股 @¥{price:.2f}"
    try:
        from gsuid_core.ai_core.proactive.emitter import emit_proactive_message

        await emit_proactive_message(
            event=ev,
            message=line,
            source="tool",
            trigger_reason=f"papertrade_fill:{stock_code}:{side}",
            suppress_when_heartbeat_recent=False,  # 成交播报是关键信息，不被心跳抑制
        )
    except Exception as e:
        _gslogger.debug(f"[SayuStock][PaperTrade] 成交播报失败（不影响落库）: {e}")


async def _get_enriched_positions(
    group_id: str,
    bot_id: str,
    *,
    max_stale_seconds: int = 60,
) -> list[tuple[SayuPaperPosition, dict]]:
    """拿 enriched 后的持仓列表。

    Args:
        group_id: 群号。
        bot_id: bot_id。
        max_stale_seconds: 报价超过多少秒就算 stale，触发刷新。

    Returns:
        ``[(position, enrichment_dict), ...]`` 其中 ``enrichment_dict``:
        ``current_price`` / ``market_value`` / ``unrealized_pnl`` /
        ``unrealized_pnl_pct`` / ``quote_age_seconds`` / ``quote_source``
        （"live"=60s 内/db=已有缓存但超龄/cost=未刷过用均价兜底）。
        ``quote_age_seconds`` 为 None 表示从未刷过价。
    """
    positions: list[SayuPaperPosition] = await db.PaperPositionRepo.list_by_account(group_id, bot_id)
    if not positions:
        return []

    now = _dt.datetime.now()
    # 1) 找出需要刷新的持仓
    to_refresh: list[SayuPaperPosition] = []
    for p in positions:
        if not p.last_quote_at:
            to_refresh.append(p)
            continue
        try:
            age = (now - p.last_quote_at).total_seconds()
        except TypeError:
            # 老库 last_quote_at 是 None / 字符串乱码等异常情况
            to_refresh.append(p)
            continue
        if age > max_stale_seconds:
            to_refresh.append(p)

    # 2) 批量拉一次报价（缓存优先；缺失项并发穿透）。结果**就地复用**给"写 DB"
    #    和"组装 enrichment"，避免双重 HTTP 调用。
    secid_to_fresh: Dict[str, float] = {}
    if to_refresh:
        secids = [p.secid for p in to_refresh if p.secid]
        if secids:
            try:
                fetched = await quote_service.get_quotes_batch(secids)
            except Exception as e:
                _gslogger.debug(f"[SayuStock][PaperTrade] quote_service.get_quotes_batch 异常：{e}")
                fetched = {}
            secid_to_fresh = {secid: float(price) for secid, price in fetched.items() if price is not None}
            # 3) 写回 DB（让下一次调用立即看到新价，省一次 API）
            if secid_to_fresh:
                writes: list[dict] = [
                    {
                        "stock_code": p.stock_code,
                        "price": secid_to_fresh[p.secid],
                        "at": now,
                    }
                    for p in to_refresh
                    if p.secid in secid_to_fresh
                ]
                if writes:
                    try:
                        await db.PaperPositionRepo.bulk_set_quote(writes, group_id, bot_id)
                    except Exception as e:
                        # 老库可能列未迁移完；这里 swallow 不影响主流程
                        _gslogger.debug(f"[SayuStock][PaperTrade] bulk_set_quote 写 DB 失败（降级）：{e}")

    # 4) 拼 enrichment——不再二次读 DB，组合原 position + secid_to_fresh
    enriched: list[tuple[SayuPaperPosition, dict]] = []
    for p in positions:
        fresh = secid_to_fresh.get(p.secid) if p.secid else None
        if fresh is not None:
            current_price = fresh
            quote_source = "live"
            quote_age = 0
            last_quote_at = now
        elif p.last_quote_price is not None and p.last_quote_at is not None:
            current_price = p.last_quote_price
            quote_source = "db"
            try:
                quote_age = int((now - p.last_quote_at).total_seconds())
            except TypeError:
                quote_age = None
            last_quote_at = p.last_quote_at
        else:
            # 用均价兜底；最后兜
            current_price = p.avg_cost if p.avg_cost else 0.0
            quote_source = "cost"
            quote_age = None
            last_quote_at = None

        cost_basis = p.avg_cost * p.qty if p.avg_cost else 0.0
        market_value = current_price * p.qty
        unrealized_pnl = (current_price - p.avg_cost) * p.qty if p.avg_cost else 0.0
        unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0

        enrichment: dict[str, Any] = {
            "current_price": round(current_price, 4),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 4),
            "quote_age_seconds": quote_age,
            "quote_source": quote_source,
            "last_quote_at": last_quote_at.isoformat() if last_quote_at else None,
        }
        enriched.append((p, enrichment))
    return enriched


def _aggregate_enriched(
    enriched: list[tuple[SayuPaperPosition, dict]],
) -> dict[str, float]:
    """聚合 enriched 持仓用于 ``papertrade_account_query`` 输出。"""
    position_value: float = 0.0
    total_unrealized_pnl: float = 0.0
    quote_stale_count: int = 0
    for _, e in enriched:
        if e["quote_source"] != "live":
            quote_stale_count += 1
        position_value += float(e["market_value"])
        total_unrealized_pnl += float(e["unrealized_pnl"])
    return {
        "position_value": round(position_value, 2),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "quote_stale_count": quote_stale_count,
    }


# ============================================================
# 1) 业务/账本工具（6 个，主人格 + 能力代理共用）
# ============================================================


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    context_tags=_PAPERTRADE_CTX_TAGS,
)
async def papertrade_account_query(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """查询 AI 自己的模拟盘（虚拟盘）账户：现金 / 总资产 / 浮盈 / 已实现盈亏 / 持仓数。

    ⚠️ **这是"你（早柚/AI）自己经营的模拟盘账户"的权威数据源**，不是用户个人
    自选股。当有人问「你的模拟盘 / 你现在账户怎么样 / 盈利多少 / 总资产多少 /
    仓位几成 / 今天赚了没」时走这个工具。账户与持仓落在 SQLModel 表里，**不在**
    framework 的 ``record:`` 集合、``state_*`` 或 init 阶段的 artifact 里——**严禁**用
    ``record_list`` / ``state_get`` / ``artifact_get_recent`` 代答账户状态。

    ⚠️ **作用域（默认全服共用）**：配置「多群模拟盘」关闭时，全服只有一个盘；
    在 A 群开户后，在 B 群提问也必须调本工具，返回的就是那同一份账户。
    ``group_id`` 字段是开户原群号，**不等于**「当前群没开盘」。**严禁**因为
    返回的 group_id 与当前会话群号不同就说「这个群没有创建过模拟盘」。
    只有工具明确返回「全服尚未开通 / 本群尚未开通」时，才是真的没有账户。

    2026-07-01 修复：``total_equity`` 现在是 **真·总资产 = 现金 + Σ持仓市值**，
    不再返回 cash-only；同时给出 ``total_unrealized_pnl`` / ``realized_pnl`` /
    ``position_value`` / ``position_count`` / ``quote_stale_count``，agent
    可以直接做盈亏推算。

    Args:
        group_id: 仅多群模式生效；共用模式下忽略（全服唯一账户）。留空即可。
        bot_id: 仅多群模式生效；共用模式下忽略。留空即可。
    """
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    acc = await db.PaperAccountRepo.get(gid, bid)
    if not acc:
        return account_scope.not_opened_message(gid, bid)

    # ── enriched 持仓聚合（自动刷报价，含浮盈 / 现价） ──
    enriched: list[tuple[SayuPaperPosition, dict]] = await _get_enriched_positions(gid, bid)
    agg: dict[str, float] = _aggregate_enriched(enriched)
    position_value: float = agg["position_value"]
    total_unrealized_pnl: float = agg["total_unrealized_pnl"]
    quote_stale_count: int = int(agg["quote_stale_count"])

    total_equity: float = round(acc.cash + position_value, 2)
    realized_pnl: float = round(acc.principal - acc.initial_cash, 2)
    total_unrealized_pnl_pct: float = (
        round(total_unrealized_pnl / acc.initial_cash * 100, 4) if acc.initial_cash else 0.0
    )

    last_decided: _dt.datetime | None = acc.last_decided_at
    shared = account_scope.is_shared_mode()
    view: _AccountView = {
        "group_id": gid,
        "bot_id": bid,
        "shared_mode": shared,
        "scope_note": account_scope.scope_note_for_llm(gid),
        "cash": acc.cash,
        "initial_cash": acc.initial_cash,
        "principal": acc.principal,
        "position_value": position_value,
        "total_equity": total_equity,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_unrealized_pnl_pct": total_unrealized_pnl_pct,
        "realized_pnl": realized_pnl,
        "position_count": len(enriched),
        "quote_stale_count": quote_stale_count,
        "mode": acc.mode,
        "frequency_minutes": acc.frequency_minutes,
        "enabled": acc.enabled,
        "kanban_init_root_id": acc.kanban_init_root_id,
        "kanban_period_root_id": acc.kanban_period_root_id,
        "last_decided_at": last_decided.isoformat() if last_decided else None,
    }
    return json.dumps(view, ensure_ascii=False)


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    context_tags=_PAPERTRADE_CTX_TAGS,
)
async def papertrade_position_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """列出 AI 自己的模拟盘（虚拟盘）当前持仓：持有哪些股票 / 现价 / 市值 / 浮盈。

    ⚠️ **问「你现在持有什么 / 你的持仓 / 买了哪些股 / 现在几只仓」时走这个**——
    这是"你（早柚/AI）自己经营的模拟盘"的权威持仓表（SQLModel），**不是**用户
    个人自选股（那是 ``send_my_stock``）。持仓落在 SQLModel 表里，**不在**
    framework 的 ``record:`` 集合、``state_*`` 或 init artifact 里；返回
    ``ℹ️ ...当前无持仓`` 才代表真的空仓。**严禁**用 ``record_list`` /
    ``artifact_get_recent`` 代答持仓——init artifact 的"0 持仓"是建账时的旧
    快照，成交后永不更新，据它回答会误报空仓。

    ⚠️ **作用域**：默认全服共用一个盘；在任意群提问都查同一份持仓。不要因为
    当前群不是开户群就说「这个群没有模拟盘」。

    2026-07-01 修复：每行新增 ``current_price`` / ``market_value`` /
    ``unrealized_pnl`` / ``unrealized_pnl_pct`` / ``quote_age_seconds`` /
    ``quote_source`` 字段。``quote_source`` 语义：
        ``"live"`` = 60s 内新鲜报价
        ``"db"``   = DB 有缓存但超过 max_stale_seconds（默认 60s）
        ``"cost"`` = 从未刷过价，用 avg_cost 兜底
    """
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"

    # 未开户与「空仓」必须区分：共用模式下 resolve 到开户原群；若仍无账户则明确说未开通
    acc = await db.PaperAccountRepo.get(gid, bid)
    if not acc:
        return account_scope.not_opened_message(gid, bid)

    enriched: list[tuple[SayuPaperPosition, dict]] = await _get_enriched_positions(gid, bid)
    if not enriched:
        note = account_scope.scope_note_for_llm(gid)
        return f"ℹ️ 当前模拟盘无持仓（账户 group_id={gid}）。{note}"

    items: list[_PositionItem] = []
    for p, e in enriched:
        opened_iso = p.opened_at.isoformat() if p.opened_at else None
        items.append(
            {
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "secid": p.secid,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "current_price": e["current_price"],
                "market_value": e["market_value"],
                "unrealized_pnl": e["unrealized_pnl"],
                "unrealized_pnl_pct": e["unrealized_pnl_pct"],
                "quote_age_seconds": e["quote_age_seconds"],
                "quote_source": e["quote_source"],
                "opened_at": opened_iso,
            }
        )
    return json.dumps(items, ensure_ascii=False)


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    context_tags=_PAPERTRADE_CTX_TAGS,
)
async def papertrade_holdings_image(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """把 AI **模拟盘当前持仓**渲染成「模拟盘自选」风格的**简化版持仓图**并发出。

    与用户命令「**模拟盘自选**」/「**模拟盘持仓**」同一张图（也可不经 agent、直接发该命令）。
    默认全服共用一个盘：任意群调用都出同一份持仓图。

    ⚠️ **这是简化快照，不是完整账户报告**：
    - **有**：账户摘要（现金 / 总资产 / 持仓市值 / 浮盈 / 累计盈亏）、
      每只持仓的数量 / 成本 / 现价 / 市值、**今日涨跌幅**、**持仓收益率（相对成本）**、
      顶部宽基指数参考。
    - **没有**：交易流水、决策日志、候选池、群友关注、风控模式细节、历史净值曲线。
      需要完整数据请分别调 ``papertrade_account_query`` /
      ``papertrade_position_list`` / ``papertrade_trade_list`` /
      ``papertrade_decision_list``，或命令「模拟盘查看」「模拟盘记录」。

    与用户「我的自选」的区别：自选只有当日涨跌；本图是 **AI 模拟盘真实持仓**，
    每条同时标 **今日涨跌** 与 **持仓浮盈%**。

    适用：有人问「模拟盘自选 / 模拟盘持仓 / 你现在持仓怎么样 / 发张持仓图 / 仓位图」
    时出图；主 persona 可直接调，不必先委派决策代理。

    Args:
        group_id / bot_id: 仅多群模式生效；共用模式忽略。留空即可。
    """
    from gsuid_core.segment import MessageSegment
    from gsuid_core.ai_core.trigger_bridge import ai_return

    from .render import build_holdings_snapshot_image

    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"

    bot = ctx.deps.bot
    if bot is None:
        return "❌ 当前上下文拿不到 Bot，无法发图；请改用 papertrade_position_list 文本查询。"

    try:
        result = await build_holdings_snapshot_image(gid, bid)
    except Exception as e:
        _gslogger.exception(f"[SayuStock][PaperTrade] holdings_image 渲染失败: {e}")
        return f"❌ 持仓简图渲染失败：{e}；请改用 papertrade_position_list。"

    if isinstance(result, str):
        return result

    try:
        scope = "全服共用账户" if account_scope.is_shared_mode() else f"群 {gid}"
        ai_return(f"【模拟盘自选·简化版】{scope}持仓图已发送：含今日涨跌与持仓浮盈，不含交易流水/决策日志。")
    except Exception as e:
        _gslogger.debug(f"[SayuStock][PaperTrade] holdings_image ai_return 失败: {e}")

    await bot.send(MessageSegment.image(result))
    return "✅ 已发送【模拟盘自选】简化版持仓图（今日涨跌+持仓浮盈，不含交易记录）。可再补一句点评，勿复述整表。"


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    context_tags=_PAPERTRADE_CTX_TAGS,
)
async def papertrade_trade_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
    stock_code: str = "",
    limit: int = 20,
    include_snapshot: bool = True,
) -> str:
    """查询 AI 模拟盘（虚拟盘）历史买卖流水（买入/卖出记录 + 已实现盈亏；默认全服共用账户）。

    问「你都买卖过什么 / 最近成交 / 交易记录 / 某只股什么时候买的」走这个。
    数据在 SQLModel 流水表，**不在** ``record:`` 集合 / ``state_*``。

    返回字段含 ``reason`` / ``snapshot``（买入时写入的指标与入场计划 JSON）/
    ``decision_id`` / ``mode``，供决策代理在卖出前回看入场止损止盈。

    Args:
        include_snapshot: snapshot 是大段入场指标 JSON；批量扫流水（非复核某笔卖出）
            时传 False 可大幅省 token。默认 True。
    """
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    rows = await db.PaperTradeRepo.list_by_account(
        gid,
        bid,
        limit=limit,
        stock_code=stock_code or None,
    )
    items: list[_TradeItem] = []
    for t in rows:
        decided_at: _dt.datetime | None = t.decided_at
        executed_at: _dt.datetime | None = t.executed_at
        items.append(
            {
                "id": t.id,
                "stock_code": t.stock_code,
                "stock_name": t.stock_name,
                "side": t.side,
                "price": t.price,
                "qty": t.qty,
                "amount": t.amount,
                "fee": t.fee,
                "realized_pnl": t.realized_pnl,
                "reason": t.reason,
                # 入场计划 / 指标快照：卖出复核时用（buy 时写入的 snapshot / decision_id）；
                # include_snapshot=False 时置空省 token（批量扫流水场景）
                "snapshot": (t.snapshot or "") if include_snapshot else "",
                "decision_id": t.decision_id,
                "mode": t.mode,
                "decided_at": decided_at.isoformat() if decided_at else None,
                "executed_at": executed_at.isoformat() if executed_at else None,
            }
        )
    return json.dumps(items, ensure_ascii=False)


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    context_tags=_PAPERTRADE_CTX_TAGS,
)
async def papertrade_decision_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
    stock_code: str = "",
    limit: int = 20,
    include_indicators: bool = True,
) -> str:
    """查询 AI 模拟盘的**决策日志**（buy/sell/hold 的理由 + 评分 + 指标 + 风控拦截）。

    ⚠️ 问「你最近做了什么决策 / 为什么买/卖/持有 XX / 上一轮怎么想的 / 为什么没动手」
    时走这个。模拟盘的决策推理**不会在群里主动播报**（只有真成交才由系统自动推一行
    冒泡），但每一条决策（含 hold）都落在 SQLModel 决策日志里，可随时查。与
    ``papertrade_trade_list`` 互补：trade_list 是"成交了什么"，decision_list 是"为什么
    这样决策（含没成交的 hold 理由）"。默认全服共用账户，任意群可查。

    Args:
        group_id / bot_id: 仅多群模式生效；共用模式忽略。留空即可。
        stock_code: 只看某只票的决策（留空看全部）
        limit: 返回最近多少条（默认 20，按时间倒序）
        include_indicators: indicators 是大段指标 JSON；只扫决策脉络（不复盘单条指标）
            时传 False 省 token。默认 True。

    返回 [{id, action, stock_code, stock_name, score, reason, blocked_by, trade_id,
    indicators, decided_at}, ...]。
    """
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    rows = await db.PaperDecisionRepo.list_recent(
        gid,
        bid,
        limit=limit,
        stock_code=stock_code or None,
    )
    if not rows:
        suffix: str = f"（{stock_code}）" if stock_code else ""
        return f"ℹ️ 当前模拟盘暂无决策记录{suffix}（账户 group_id={gid}）。"
    items: list[dict[str, Any]] = []
    for d in rows:
        created_at: _dt.datetime | None = d.created_at
        items.append(
            {
                "id": d.id,
                "action": d.action,
                "stock_code": d.stock_code,
                "stock_name": d.stock_name,
                "score": d.score,
                "reason": d.reason,
                "blocked_by": d.blocked_by,
                "trade_id": d.trade_id,
                # include_indicators=False 时置空省 token（只扫决策脉络场景）
                "indicators": d.indicators if include_indicators else "",
                "decided_at": created_at.isoformat() if created_at else None,
            }
        )
    return json.dumps(items, ensure_ascii=False)


@ai_tools(category="common", capability_domain="AI模拟盘")
async def papertrade_watchlist_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """查询某群群友关注列表（公开）。"""
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    rows = await db.PaperWatchlistRepo.list_by_account(gid, bid)
    items: list[_WatchlistItem] = []
    for w in rows:
        created_at: _dt.datetime | None = w.created_at
        items.append(
            {
                "stock_code": w.stock_code,
                "stock_name": w.stock_name,
                "user_id": w.user_id,
                "note": w.note,
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    return json.dumps(items, ensure_ascii=False)


@ai_tools(category="common", capability_domain="AI模拟盘")
async def papertrade_agent_pool_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """查询内部候选池（agent_pool）当前内容（公开只读）。

    返回 [{stock_code, stock_name, reason, priority, expires_at}, ...]。
    决策代理在每轮开头调此工具候选池充盈度，不足时调 papertrade_candidate_refresh
    增量补充；避免永远只看持仓股、不再找新标的的"锚定陷阱"。
    """
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    rows = await db.PaperAgentPoolRepo.list_by_account(gid, bid)
    items: list[dict[str, Any]] = []
    for r in rows:
        expires_at: _dt.datetime | None = r.expires_at
        items.append(
            {
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "reason": r.reason,
                "priority": r.priority,
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        )
    return json.dumps(items, ensure_ascii=False)


# ============================================================
# 2) 能力代理私有工具（4 个，visible_when 限定）
# ============================================================


@ai_tools(
    category="default",
    capability_domain="AI模拟盘",
    visible_when=_visible_to_papertrade_agent,
)
async def papertrade_decision_insert(
    ctx: RunContext[ToolContext],
    action: str,
    stock_code: str = "",
    stock_name: str = "",
    score: float = 0.0,
    reason: str = "",
    indicators: str = "",
    trade_id: int = 0,
    blocked_by: str = "",
) -> str:
    """向决策日志表插入一条记录（仅 papertrade_*_agent 调用）。

    字段使用规约（**违反规约会导致群播报糊成一坨**）：

      - ``reason``  —— **纯决策理由**，一段中文，30~120 字为宜。
                       **严禁**塞：账户现金 / 持仓列表 / K 线 / 技术指标 JSON /
                       任何 markdown 标题符号 / emoji / 行情数据。
                       这些由 ``indicators`` 字段承载，或播报时由框架自 DB 读出。

                       ✅ 示例：'MA5<MA20 多头排列，但 ROE 同比下滑 3.2 pct 且估值偏高，主动持币'
                       ❌ 反例：'账户现金 ¥1,000,000\\n📈 当前持仓：无\\n理由：技术面...'

      - ``indicators`` —— JSON 字典字符串。技术/财务快照 + **入场计划**（buy 必写）：
                       建议键：
                         ``plan_entry`` 入场价、``plan_stop_pct`` 止损幅度（负小数，
                         如 -0.08）、``plan_stop_price`` 止损价、``plan_take_pct`` /
                         ``plan_take_price`` 止盈（若有）、``plan_thesis`` 一句话逻辑。
                       其余可拼 stock_indicators / stock_financials 摘要。
                       非法 JSON 会降级为空；**不要**把大段中文塞进 reason 冒充计划。
                       **buy 硬门**：必须含可解析 ``plan_stop_pct``(<0) 或
                       ``plan_stop_price``(>0)，否则拒绝落库。

      - ``score``    —— -1.0 ~ +1.0；buy → +，sell → -，hold → 0。

      - ``blocked_by`` —— 风控拦截原因；hold 不带风控时传 ''。
    """
    from .strategy import indicators_have_entry_stop

    gid, bid = await _resolve_scope(ctx)
    denied: str = await _deny_write(ctx, gid, bid)
    if denied:
        return denied

    action_lc: str = (action or "").lower().strip()

    # reason 归一化 + 长度上限，避免下游播报挤糊
    norm_reason: str = "".join(ch if ch.isprintable() else " " for ch in (reason or "")).strip()
    if len(norm_reason) > 200:
        norm_reason = norm_reason[:197] + "..."

    # indicators：合法 JSON 对象；buy 时强制止损计划字段
    import json as _json

    ind_obj: dict[str, object] = {}
    if indicators and indicators.strip() and indicators.strip() != "{}":
        try:
            parsed: object = _json.loads(indicators)
        except (_json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            ind_obj = {str(k): v for k, v in parsed.items()}
            indicators = _json.dumps(ind_obj, ensure_ascii=False)
        else:
            indicators = "{}"
            ind_obj = {}
    else:
        indicators = ""
        ind_obj = {}

    if action_lc == "buy" and (stock_code or "").strip():
        if not indicators_have_entry_stop(ind_obj):
            return (
                "⚠️ buy 须在 indicators JSON 写入 plan_stop_pct(<0) 或 plan_stop_price(>0)"
                "（可解析数值止损）；已拒绝落库，请补全后重试"
            )

    d = await db.PaperDecisionRepo.append(
        gid,
        bid,
        action=action,
        stock_code=stock_code or None,
        stock_name=stock_name or None,
        score=score,
        reason=norm_reason,
        indicators=indicators,
        trade_id=trade_id if trade_id > 0 else None,
        blocked_by=blocked_by,
    )

    # 决策 → 候选池 反馈闭环：sell 从池移除、buy 促成保留（hold 不动）
    if stock_code and action_lc in ("buy", "sell"):
        try:
            from .candidate_pool import post_decision_pool_update

            await post_decision_pool_update(
                gid,
                bid,
                [
                    {
                        "action": action_lc,
                        "code": stock_code,
                        "name": stock_name,
                        "secid": "",
                        "score": score,
                    }
                ],
            )
        except Exception as e:
            _gslogger.debug(f"[SayuStock][PaperTrade] decision→pool 反馈失败: {e}")

    return f"ok decision_id={d.id}"


@ai_tools(
    category="default",
    capability_domain="AI模拟盘",
    visible_when=_visible_to_papertrade_agent,
)
async def papertrade_trade_insert(
    ctx: RunContext[ToolContext],
    stock_code: str,
    stock_name: str,
    secid: str,
    side: str,
    price: float,
    qty: int,
    amount: float,
    fee: float,
    realized_pnl: float = 0.0,
    reason: str = "",
    snapshot: str = "",
    decision_id: int = 0,
    mode: str = "balanced",
) -> str:
    """向交易流水表插入一条记录（仅 papertrade_*_agent 调用）。

    **本工具会自动维护账户现金**，**不要**再单独调
    ``PaperAccountRepo.update_cash``。后端走
    ``db.PaperTradeRepo.append_with_cash_update``，在同一 session 内原子地
    写流水 + 调整 account.cash + 累计 principal（仅 sell 路径），
    失败时 trade 行也不会落库。

    现金变化公式：
        - ``side='buy'``  → ``cash -= (amount + fee)``，principal 不动
        - ``side='sell'`` → ``cash += (amount - fee + realized_pnl)``，
                             ``principal += realized_pnl``

    提示：realized_pnl 已经在 sell 路径里作为 cash 增量的修正项闭环——
    上次 buy 时 cash -= amount + fee，现在 sell 只加 amount - fee 不够，
    必须补 realized_pnl 把"买入时其实只扣了 amount 现金，但持仓价值按
    avg_cost 记账"的差额在卖出现金里补回来。如果 LLM 在 realized_pnl
    里填 0 但实际上 prices 有差，cash 会累计偏差；调用方请按
    (sell_price - avg_cost) * qty - sell_fee 严格计算后传入。

    **A 股 T+1 拦截**（2026-07-01 加）：``side='sell'`` 时若该股 **今天**
    有任何买入记录，对应锁定股数即使有也不能卖。A 股 T+1 规则要求 "T 日买
    入，T+1 日开盘前不可卖"，错误信息直接返回给 LLM 让它调整决策（改 hold
    或换只老的卖）。其余 sell（昨天的买）合法。

    **实时价校验**（2026-07-06 加）：``price`` **必须**用同轮
    ``papertrade_match_order`` 返回的实时成交价。本工具会再拉一次实时行情
    对照：偏差 > 3% 时拒绝落库（说明传入的是入池旧价/隔夜价），返回错误
    让 LLM 重新走 match_order。行情不可达时放行（match_order 已把过关）。

    **buy 入场止损硬门**：``side='buy'`` 时 ``snapshot`` JSON 必须含可解析
    ``plan_stop_pct``(<0) 或 ``plan_stop_price``(>0)，否则拒绝写流水。
    """
    # 交易执行统一走 TradeExecutor 抽象层（实时价偏差校验 / A 股 T+1 / 写流水
    # + 现金维护都在 executor 里，模拟盘/实盘可切换）；本工具只透传 + 回传说明。
    from .trade_executor import get_executor

    gid, bid = await _resolve_scope(ctx)
    denied: str = await _deny_write(ctx, gid, bid)
    if denied:
        return denied

    # buy 硬门：snapshot 须含可解析数值止损，避免「先成交再 decision 失败」无计划仓
    side_lc: str = (side or "").lower().strip()
    if side_lc == "buy":
        import json as _json

        from .strategy import indicators_have_entry_stop

        snap_obj: dict[str, object] = {}
        raw_snap = (snapshot or "").strip()
        if raw_snap and raw_snap != "{}":
            try:
                parsed_snap: object = _json.loads(raw_snap)
            except (_json.JSONDecodeError, TypeError, ValueError):
                parsed_snap = None
            if isinstance(parsed_snap, dict):
                snap_obj = {str(k): v for k, v in parsed_snap.items()}
        if not indicators_have_entry_stop(snap_obj):
            return (
                "⚠️ buy 的 snapshot JSON 须含 plan_stop_pct(<0) 或 plan_stop_price(>0)；"
                "已拒绝落库，请补全入场止损后再 trade_insert"
            )

    result = await get_executor().record_trade(
        group_id=gid,
        bot_id=bid,
        stock_code=stock_code,
        stock_name=stock_name,
        secid=secid,
        side=side,
        price=price,
        qty=qty,
        amount=amount,
        fee=fee,
        realized_pnl=realized_pnl,
        reason=reason,
        snapshot=snapshot,
        decision_id=decision_id,
        mode=mode,
    )
    # 成交成功 → 系统确定性向群里推一行成交冒泡（不依赖 agent 最终输出）。
    if result.ok:
        await _broadcast_fill(
            ctx,
            side=side,
            stock_code=stock_code,
            stock_name=stock_name,
            qty=qty,
            price=price,
            realized_pnl=realized_pnl,
        )
    return result.message


@ai_tools(
    category="default",
    capability_domain="AI模拟盘",
    visible_when=_visible_to_papertrade_agent,
)
async def papertrade_position_upsert(
    ctx: RunContext[ToolContext],
    stock_code: str,
    stock_name: str,
    secid: str,
    qty: int,
    avg_cost: float,
    last_quote_price: float = 0.0,
) -> str:
    """更新持仓（qty=0 时删除记录；仅 papertrade_*_agent 调用）。

    **本工具不动账户现金**——cash 的增减已由 ``papertrade_trade_insert``
    在同一 session 内自动维护。position_upsert 只操作持仓表
    (SayuPaperPosition)：qty>0 时 upsert，qty=0 时 DELETE 行。

    ``last_quote_price``（2026-07-01 新增，可选）：决策代理在 buy 时把
    ``match_order.price`` 直接写进 ``SayuPaperPosition.last_quote_price``，
    让下一次心跳开播时该持仓显示 ``quote_source="live"`` 而不是 "cost"。
    不传或传 0 时不更新报价字段（保留历史值）。

    调用顺序示例（buy 路径）：
        1. papertrade_match_order(buy)  → 拿 fee_total / actual_qty / amount / price
        2. papertrade_trade_insert(buy)  → 写 trade + 自动扣 cash
        3. papertrade_position_upsert(qty, avg_cost=price, last_quote_price=price)  → 写持仓
        4. papertrade_decision_insert(action='buy', trade_id=...)
    """
    # 交易执行统一走 TradeExecutor 抽象层（模拟盘/实盘可切换）。
    from .trade_executor import get_executor

    gid, bid = await _resolve_scope(ctx)
    denied: str = await _deny_write(ctx, gid, bid)
    if denied:
        return denied
    pos_id: int = await get_executor().update_position(
        group_id=gid,
        bot_id=bid,
        stock_code=stock_code,
        stock_name=stock_name,
        secid=secid,
        qty=qty,
        avg_cost=avg_cost,
        last_quote_price=last_quote_price,
    )
    return f"ok pos_id={pos_id}  （cash 由 trade_insert 自动维护）"


@ai_tools(
    category="default",
    capability_domain="AI模拟盘",
    visible_when=_visible_to_papertrade_agent,
)
async def papertrade_candidate_refresh(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
    target_size: int = 0,
    rotate_out: int = 0,
    batch_size: int = 0,
) -> str:
    """**轮换**内部候选池（agent_pool）：淘汰旧标的 + 补充新鲜标的，防"锚定陷阱"。

    仅 papertrade_*_agent 可见。**每轮都应调一次**（不要再用"池 <3 才刷"的旧门槛——
    那会让池子一旦填满就永远冻结、每轮嚼同一批）。本工具一次做完 4 件事：

      1. 清理已过期候选（物理删除）。
      2. **淘汰**：删掉最旧的 ``rotate_out`` 只 auto 扫描候选（持仓 / 群友关注中的
         标的永不淘汰）——这是"剔除股票池"。
      3. **补蓝筹底仓**：把池中蓝筹底仓补到 ``BASE_KEEP`` 只（跨行业大盘蓝筹，
         保证池里始终有可交易的优质标的，而非全是超买微盘 → 决策代理只能一直
         hold → 账户永远空仓）。
      4. **补动量标的**（多源 + 轮询交织）：行业龙头 / 概念龙头 / 热股 /
         涨幅榜 / 跌幅榜（超跌） / 成交额龙头 / **高 ROE 质量池** / 雪球新闻，
         补到 ``target_size``；入池前批量报价**过滤涨停 / 过热**
         （当日涨幅 ≥ 本板涨停 × 0.8）。各源**轮询**写入，避免旧逻辑
         「sector 先塞满 → hotmap/news 永远 0」。

    去重：跳过已在 持仓 / 群友关注 / 现池 中的标的。auto 候选 ``AUTO_EXPIRE_HOURS``
    后过期、每轮再淘汰最旧几只 → 日内自然轮换；蓝筹底仓每轮随机补入不同名，
    既保质量又不长期锚定同一批。真正 buy/sell 仍由决策代理深度分析后产出。

    Args:
        target_size: 轮换后候选池目标只数（0=用默认 ``POOL_TARGET_SIZE``，约 16）。
        rotate_out: 本轮强制淘汰几只最旧 auto 候选（0=用默认 ``ROTATE_OUT_PER_REFRESH``）。
        batch_size: 兼容旧调用；>0 时额外限制本轮动量补入上限。

    Returns:
        JSON：{"expired": E, "evicted": [...], "base_added": [...], "added": [...],
        "sources": {"sector","concept","hotmap","gainer","laggard","amount","quality",
        "news","deduped","overheated"}, "pool_size_before": N, "pool_size_after": M}
    """
    from datetime import timedelta

    from .candidate_pool import (
        BASE_KEEP,
        BLUECHIP_BASE,
        POOL_TARGET_SIZE,
        AUTO_EXPIRE_HOURS,
        BASE_EXPIRE_HOURS,
        ROTATE_OUT_PER_REFRESH,
        derive_secid,
        _from_position,
        _from_watchlist,
        pick_base_slice,
        _from_quality_roe,
        filter_overheated,
        _from_hotmap_top_n,
        _from_amount_leaders,
        _from_market_gainers,
        _from_market_laggards,
        _from_sector_top_picks,
        _from_concept_top_picks,
        interleave_source_pairs,
        _from_news_extract_tickers,
    )

    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    denied: str = await _deny_write(ctx, gid, bid)
    if denied:
        return denied

    tgt: int = target_size if target_size > 0 else POOL_TARGET_SIZE
    tgt = max(5, min(tgt, 50))
    rot: int = rotate_out if rotate_out > 0 else ROTATE_OUT_PER_REFRESH
    rot = max(0, min(rot, tgt))
    now = _dt.datetime.now()

    # ── 0) 清过期（物理删除，腾出轮换空间） ──
    expired: int = 0
    try:
        expired = await db.PaperAgentPoolRepo.cleanup_expired_for(gid, bid)
    except Exception as e:
        _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh 清过期失败: {e}")

    # ── 保护集：持仓 + 群友关注（永不淘汰） ──
    protected: Set[str] = set()
    try:
        protected.update(await _from_position(gid, bid))
    except Exception as e:
        _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh position 保护集失败: {e}")
    try:
        protected.update(await _from_watchlist(gid, bid))
    except Exception as e:
        _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh watchlist 保护集失败: {e}")

    entries = await db.PaperAgentPoolRepo.list_by_account(gid, bid)
    pool_size_before: int = len(entries)

    # ── 1) 淘汰最旧的 rot 只 auto 候选（"剔除"） ──
    autos = sorted(
        [e for e in entries if e.added_by == "auto_refresh" and e.stock_code not in protected],
        key=lambda e: e.created_at,
    )
    evicted: list[str] = []
    for e in autos[:rot]:
        try:
            if await db.PaperAgentPoolRepo.remove(gid, bid, e.stock_code):
                evicted.append(e.stock_code)
        except Exception as ex:
            _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh 淘汰 {e.stock_code} 失败: {ex}")

    # 淘汰后重算 seen（现池 + 保护集）
    seen: Set[str] = set(await db.PaperAgentPoolRepo.list_codes(gid, bid)) | protected
    base_now: int = sum(1 for e in entries if e.added_by == "base" and e.stock_code not in evicted)

    # ── 2) 补蓝筹底仓到 BASE_KEEP ──
    base_added: list[str] = []
    base_slots: int = max(0, min(BASE_KEEP - base_now, tgt - len(seen)))
    if base_slots > 0:
        for code, name in pick_base_slice(len(BLUECHIP_BASE)):
            if len(base_added) >= base_slots:
                break
            if code in seen:
                continue
            secid = derive_secid(code)
            try:
                await db.PaperAgentPoolRepo.upsert(
                    gid,
                    bid,
                    stock_code=code,
                    stock_name=name,
                    secid=secid,
                    reason="蓝筹底仓（大盘蓝筹/指数成分，质量地基）",
                    added_by="base",
                    priority=2,
                    expires_at=now + timedelta(hours=BASE_EXPIRE_HOURS),
                )
            except Exception as ex:
                _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh 补底仓 {code} 失败: {ex}")
                continue
            seen.add(code)
            base_added.append(code)

    # ── 3) 补动量标的（多源 + 轮询交织）到 target，入池前过滤涨停/过热 ──
    sources: dict[str, int] = {
        "sector": 0,
        "concept": 0,
        "hotmap": 0,
        "gainer": 0,
        "laggard": 0,
        "amount": 0,
        "quality": 0,
        "news": 0,
        "deduped": 0,
        "overheated": 0,
    }
    added: list[dict[str, str]] = []
    momentum_cap: int = max(0, tgt - len(seen))
    if batch_size > 0:
        momentum_cap = min(momentum_cap, batch_size)

    if momentum_cap > 0:
        import asyncio
        from collections.abc import Awaitable

        async def _safe_source(label: str, coro: Awaitable[list[str]]) -> list[tuple[str, str]]:
            try:
                codes = await coro
                return [(c, label) for c in codes]
            except Exception as ex:
                _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh {label} 失败: {ex}")
                return []

        # 多源并行拉取，降低 8 路串行时延
        batches = await asyncio.gather(
            _safe_source("sector", _from_sector_top_picks(top_sectors=4, per_sector=3)),
            _safe_source("concept", _from_concept_top_picks(top_concepts=3, per_concept=3)),
            _safe_source("hotmap", _from_hotmap_top_n(n=12)),
            _safe_source("gainer", _from_market_gainers(n=10)),
            _safe_source("laggard", _from_market_laggards(n=8)),
            _safe_source("amount", _from_amount_leaders(n=10)),
            _safe_source("quality", _from_quality_roe(n=10)),
            _safe_source("news", _from_news_extract_tickers()),
        )
        raw_pairs: list[tuple[str, str]] = []
        for batch in batches:
            raw_pairs.extend(batch)

        # 去重（保留首次出现的 source），过滤非法 + 已 seen
        uniq: list[tuple[str, str]] = []
        useen: Set[str] = set()
        for c, s in raw_pairs:
            if not (c and len(c) == 6 and c.isdigit()):
                continue
            if c in seen:
                sources["deduped"] += 1
                continue
            if c in useen:
                continue
            useen.add(c)
            uniq.append((c, s))

        # 轮询交织：各源交替占位，避免 sector 一路写满
        uniq = interleave_source_pairs(uniq)

        # 一次批量报价过滤涨停/过热（跌幅榜几乎不会被误杀）
        kept: Set[str] = set(await filter_overheated([c for c, _ in uniq]))
        sources["overheated"] = len(uniq) - len(kept)

        _src_reason: dict[str, str] = {
            "sector": "行业板块龙头",
            "concept": "概念板块龙头",
            "hotmap": "大盘热股",
            "gainer": "沪深A涨幅榜",
            "laggard": "沪深A跌幅榜(超跌)",
            "amount": "成交额龙头",
            "quality": "高ROE财务质量",
            "news": "新闻提及",
        }
        for c, s in uniq:
            if len(added) >= momentum_cap:
                break
            if c not in kept:
                continue
            secid = derive_secid(c)
            src_label = _src_reason[s] if s in _src_reason else s
            try:
                await db.PaperAgentPoolRepo.upsert(
                    gid,
                    bid,
                    stock_code=c,
                    stock_name="",
                    secid=secid,
                    reason=f"{src_label}扫描（已过滤涨停/过热，priority=1）",
                    added_by="auto_refresh",
                    priority=1,
                    expires_at=now + timedelta(hours=AUTO_EXPIRE_HOURS),
                )
            except Exception as ex:
                _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh 写入 {c} 失败: {ex}")
                continue
            seen.add(c)
            added.append({"stock_code": c, "secid": secid, "source": s})
            if s in sources:
                sources[s] += 1
            else:
                sources[s] = 1

    pool_after: list[str] = []
    try:
        pool_after = await db.PaperAgentPoolRepo.list_codes(gid, bid)
    except Exception as e:
        _gslogger.debug(f"[SayuStock][PaperTrade] candidate_refresh 读池失败: {e}")
        pool_after = list(seen)

    return json.dumps(
        {
            "expired": expired,
            "evicted": evicted,
            "base_added": base_added,
            "added": added,
            "sources": sources,
            "pool_size_before": pool_size_before,
            "pool_size_after": len(pool_after),
        },
        ensure_ascii=False,
    )


@ai_tools(
    category="default",
    capability_domain="AI模拟盘",
    visible_when=_visible_to_papertrade_agent,
)
async def papertrade_match_order(
    ctx: RunContext[ToolContext],
    side: str,
    stock_code: str,
    qty: int,
    price: float = 0.0,
    cash_available: float = 0.0,
    position_qty: int = 0,
) -> str:
    """撮合一笔订单（A 股真实费率 + 涨跌停拦截 + 交易时段守卫）；返回 MatchResult JSON。

    **成交价规则（2026-07-06 修复）**：成交价 = **撮合此刻的实时行情价**
    （quote_service 60s 内报价），``price`` 参数只是调用方的参考价，可不传。
    之前实现按调用方传价成交，LLM 常把"股票入候选池当时的旧价"传进来，
    导致模拟盘成交价严重失真。现在：
      - 实时价可达 → 一律按实时价成交，返回的 ``price`` 即真实成交价，
        后续 ``papertrade_trade_insert`` / ``papertrade_position_upsert``
        **必须**用本工具返回的 price / amount / fee_total；
      - 实时价不可达 → ok=False 拒绝撮合（宁可不成交，不按旧价成交）。

    **交易时段守卫**：非 A 股交易日或不在 9:30-11:30 / 13:00-15:00 时段内时
    ok=False 拒绝撮合（真实市场此时也无法成交）。LLM 收到后应改 hold。

    内置涨跌停板拦截：按名义涨跌停 × 0.9 拦截——主板 ±9%、ST/*ST ±4.5%、
    科创/创业板 ±18%、北交所 ±27% 即视为触及涨跌停，买入触涨停 / 卖出触跌停
    直接 ok=False。调用方判断到 reason 含"涨停"或"跌停"后应改 hold。

    Args:
        side: buy / sell
        stock_code: 股票代码（用于识别主板 / 科创 / 创业板 / 北交所）
        qty: 请求股数
        price: 参考价（可不传；**实际成交永远按实时行情价**，两者偏差会写进 reason）
        cash_available: 账户可用现金（buy 时必填）
        position_qty: 当前持仓股数（sell 时必填）
    """
    # 交易执行统一走 TradeExecutor 抽象层（模拟盘/实盘可切换）；本工具只做
    # 参数透传 + 把 MatchResult 序列化成工具契约 JSON。
    from .trade_executor import get_executor

    res = await get_executor().match(
        side=side,
        stock_code=stock_code,
        qty=qty,
        price=price,
        cash_available=cash_available,
        position_qty=position_qty,
    )
    view: _MatchResultView = {
        "ok": res.ok,
        "side": res.side,
        "code": res.code,
        "requested_qty": res.requested_qty,
        "actual_qty": res.actual_qty,
        "price": res.price,
        "requested_price": price,  # 调用方参考价（executor 已按实时价成交）
        "price_source": "live",
        "amount": res.amount,
        "commission": res.commission,
        "stamp_tax": res.stamp_tax,
        "fee_total": res.fee_total,
        "reason": res.reason,
    }
    return json.dumps(view, ensure_ascii=False)


@ai_tools(
    category="default",
    capability_domain="AI模拟盘",
    visible_when=_visible_to_papertrade_agent,
)
async def papertrade_snapshot_write(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """写当日收盘净值快照（现金 + Σ持仓实时市值），按 trade_date 幂等 upsert。

    ⚠️ 仅 papertrade_*_agent 可见，**收盘快照代理专用**。一次做完：
      1. 读账户 + enriched 持仓（自动刷实时报价算 market_value）；
      2. total_equity = cash + Σmarket_value；
         total_pnl = total_equity - initial_cash；
         total_pnl_pct = total_pnl / initial_cash × 100；
         day_pnl = total_equity - 上一交易日快照的 total_equity（无历史则相对 initial_cash）；
      3. ``PaperSnapshotRepo.upsert_for_date`` 幂等写入（同一 trade_date 重跑只更新不新增）。

    这是**纯记账**，不做任何买卖 / 撮合 / 决策，收盘后（非交易时段）调用。
    trade_date 取东八区当天。
    """
    gid, bid = await _resolve_scope(ctx, group_id, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    denied: str = await _deny_write(ctx, gid, bid)
    if denied:
        return denied

    acc = await db.PaperAccountRepo.get(gid, bid)
    if not acc:
        return account_scope.not_opened_message(gid, bid) + " 无法写快照。"

    # 东八区当天作为 trade_date（系统时钟漂到 UTC 时避免快照记错日）
    try:
        from zoneinfo import ZoneInfo

        today_cn: _dt.date = _dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except Exception:
        today_cn = _dt.date.today()

    enriched: list[tuple[SayuPaperPosition, dict]] = await _get_enriched_positions(gid, bid)
    agg: dict[str, float] = _aggregate_enriched(enriched)
    position_value: float = agg["position_value"]
    total_equity: float = round(acc.cash + position_value, 2)
    total_pnl: float = round(total_equity - acc.initial_cash, 2)
    total_pnl_pct: float = round(total_pnl / acc.initial_cash * 100, 4) if acc.initial_cash else 0.0

    # day_pnl：相对上一交易日快照的 total_equity；无历史则相对初始本金
    prev = await db.PaperSnapshotRepo.prev_before(gid, bid, today_cn)
    baseline_equity: float = prev.total_equity if prev is not None else acc.initial_cash
    day_pnl: float = round(total_equity - baseline_equity, 2)
    day_pnl_pct: float = round(day_pnl / baseline_equity * 100, 4) if baseline_equity else 0.0

    snap = await db.PaperSnapshotRepo.upsert_for_date(
        gid,
        bid,
        today_cn,
        cash=round(acc.cash, 2),
        position_value=position_value,
        total_equity=total_equity,
        day_pnl=day_pnl,
        day_pnl_pct=day_pnl_pct,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
    )
    return json.dumps(
        {
            "ok": True,
            "snapshot_id": snap.id,
            "trade_date": today_cn.isoformat(),
            "cash": round(acc.cash, 2),
            "position_value": position_value,
            "position_count": len(enriched),
            "total_equity": total_equity,
            "day_pnl": day_pnl,
            "day_pnl_pct": day_pnl_pct,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
        },
        ensure_ascii=False,
    )


# ============================================================
# 3) 通用辅助（3 个）
# ============================================================


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    covers=[
        "股票财报与基本面：F10/利润表/资产负债表/现金流/ROE/毛利率/营收与净利同比",
    ],
)
async def stock_financials(
    ctx: RunContext[ToolContext],
    stock_code: str,
    report: str = "main",
) -> str:
    """获取股票财报数据（F10 / 利润表 / 资产负债表 / 现金流 / 主要指标）。

    ⚠️ **模拟盘心跳用法**：财报日内几乎不变，底层已有约 1440 分钟文件缓存。
    **不要**在每 30 分钟决策里对候选全集每只都调 main+income。优先复用
    ``papertrade_decision_list`` / 入场 indicators 里的 fund 摘要；仅新票、
    拟首次买入、或 report_date 跨季过期时再调。income 多期环比仅买前或疑拐点时用。

    Args:
        stock_code: 6 位股票代码
        report: ``main``（最新一期主要指标）/ ``income`` / ``balance`` / ``cashflow``

    返回 ``report='main'`` 时是一段 JSON 字典，字段（跨行业通用，本就是一张
    跨行业主指标表）：
        ``roe``（加权净资产收益率 %）/ ``revenue_yoy``（营收同比 %）/
        ``profit_yoy``（归母净利同比 %）/ ``gross_margin``（毛利率 %）/
        ``net_margin``（净利率 %）/ ``debt_ratio``（资产负债率 %）/
        ``eps``（基本每股收益 元）/ ``bps``（每股净资产 元）/
        ``report_date`` / ``_industry_type`` / ``_gap`` / ``_raw_keys_present``

        **银行股专属**（``_industry_type='bank'`` 时追加）：
            ``net_interest_margin``（净息差 %，本报表唯一给到的银行特色指标）

    ⚠️ **行业识别**：本报表里只有银行会给 ``NET_INTEREST_MARGIN``（净息差），
       据此判 bank，其余判 standard。NPL / 拨备 / 资本充足率 / 偿付能力等
       **不在本接口**（在专门的 F10 银行/保险指标表里），本工具不返回。

    ⚠️ **银行股典型返回**（如 000001 平安银行，2026Q1）：
        '{"_industry_type": "bank", "roe": 2.83, "revenue_yoy": 4.65,
          "profit_yoy": 3.03, "gross_margin": null, "net_margin": 41.17,
          "debt_ratio": 90.98, "eps": 0.67, "bps": 23.91,
          "net_interest_margin": 1.79, "report_date": "2026-03-31",
          "_gap": ["gross_margin"]}'

        → 银行股 ``gross_margin`` 为 null 是正常（银行没有毛利率口径），
          但 ``roe`` / ``net_margin`` / ``net_interest_margin`` 都有值，
          **不要**因 gross_margin 缺失就说"数据缺失"。

    在 ``papertrade_decision_insert(reason=...)`` 里的推荐写法：
        标准股：'ROE 10.6%（同比-3.2pct），毛利率 89.8% 稳定，资产负债率 12.1% 极低'
        银行股：'ROE 2.83%（单季）、净息差 1.79%、净利率 41.2%、资产负债率 90.98%'
    """
    if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
        return f"⚠️ stock_code 需为 6 位数字代码: {stock_code!r}"
    if report == "main":
        # 上游 ``get_financial_snapshot`` 返回 ``dict[str, Any]`` + ``_gap``
        snap: dict[str, object] = await get_financial_snapshot(stock_code)
        return json.dumps(_stringify_values(snap), ensure_ascii=False)
    if report == "income":
        rows: list[dict[str, object]] = await get_income_statement(stock_code)
    elif report == "balance":
        rows = await get_balance_sheet(stock_code)
    elif report == "cashflow":
        rows = await get_cash_flow(stock_code)
    else:
        return f"⚠️ report 非法: {report}"
    return json.dumps([_stringify_values(r) for r in rows[:8]], ensure_ascii=False)


def _stringify_values(d: dict[str, object]) -> dict[str, str]:
    """把任意 dict 的 value 转 str，方便 JSON 序列化上游 ``Any`` 字段。

    主要用于 ``Dict[str, Any]`` —— 没有更好的元信息强行 narrow 时，
    把每个 value 用 ``str(...)`` 收敛成稳定字符串，避免运行时 ``TypeError``。
    """
    out: dict[str, str] = {}
    for k, v in d.items():
        out[k] = "" if v is None else str(v)
    return out


@ai_tools(
    category="common",
    capability_domain="AI模拟盘",
    covers=[
        "任意标的K线技术指标：A股/港股/美股/指数/ETF/期货/现货贵金属（黄金XAU、白银）/外汇",
        "日K/周K/月K/分钟K 的 MA、MACD、RSI、KDJ、BOLL、支撑压力、波动率",
        "实时行情数据支撑的技术面分析（现价/K线形态/超买超卖）",
    ],
)
async def stock_indicators(
    ctx: RunContext[ToolContext],
    stock_code: str,
    periods: int = 60,
    kline_period: int = 101,
) -> str:
    """计算标的K线技术指标（MA / MACD / RSI / KDJ / CMF / BOLL / CCI / BBI / 支撑压力 / 波动率等）。

    标的经 get_code_id 解析，覆盖 A股/港股/美股/指数/ETF/期货/现货贵金属（如
    XAU=现货黄金）/外汇——**不止 A 股**。问「黄金/XAU 技术面、K 线」也应走本工具。

    KDJ（9,3,3，通达信/东财口径）返回字段：``kdj_k`` / ``kdj_d`` / ``kdj_j``
    （K/D 常态 0~100，J 可越界）、``kdj_golden_cross_in_3d`` / ``kdj_death_cross_in_3d``
    （近 3 日 K 上穿/下穿 D）、``kdj_overbought``（J>100 或 K>80）/ ``kdj_oversold``
    （J<0 或 K<20）。低位金叉 + 超卖偏多，高位死叉 + 超买偏空。

    Args:
        stock_code: 标的代码或名称（A股 6 位代码/名称，或 XAU/现货黄金/纳指等，
                    经 get_code_id 解析，支持股票/指数/期货/现货/外汇）
        periods: 取最近多少根 K 线（默认 60）
        kline_period: K 线周期（默认 101 = 日 K；可选 5/15/30/60 = 分钟 K；
                       102 = 周 K，103 = 月 K）

    典型用法（决策时）：

      - 日 K 决策（默认）：
          stock_indicators('000001', periods=120)            # 120 日日 K
      - 多周期共振：
          stock_indicators('000001', periods=60, kline_period=5)   # 60 根 5 分钟 K
          stock_indicators('000001', periods=60, kline_period=60)  # 60 根 60 分钟 K
      - BOLL 跨周期敞口比（同标的两次调用后 diff）：
          short = stock_indicators(code, periods=20, kline_period=101)['boll20_bandwidth']
          mid   = stock_indicators(code, periods=60, kline_period=101)['boll60_bandwidth']
          → short / mid > 1.3 表示短期波动显著放大（突破/破位高发期）
    """
    # 解析代码（get_code_id 已显式返回 Optional[Tuple[str, str, str]]）
    code_id: tuple[str, str, str] | None = await get_code_id(stock_code)
    if code_id is None:
        return f"⚠️ 未找到股票: {stock_code}"
    code, name, _ = code_id

    # 校验 kline_period —— 允许 5/15/30/60/101/102/103
    allowed_periods: set[int] = {5, 15, 30, 60, 101, 102, 103}
    if kline_period not in allowed_periods:
        return (
            f"⚠️ kline_period 非法: {kline_period}，"
            f"仅支持 {sorted(allowed_periods)}（5/15/30/60=分钟 K，101=日，102=周，103=月）"
        )
    is_min_k: bool = kline_period in (5, 15, 30, 60)

    end: _dt.datetime = _dt.datetime.now()
    if is_min_k:
        start: _dt.datetime = end - _dt.timedelta(days=10)
    else:
        start = end - _dt.timedelta(days=int(periods * 1.5) + 30)
    try:
        period = KlinePeriod(str(kline_period))
    except ValueError:
        period = KlinePeriod.D1
    series = await get_market().kline(code, period, start=start.date(), end=end.date())
    if is_market_error(series):
        return f"⚠️ 拉 K 线失败: {series.message}"
    df = kline_to_df(series)
    if df.empty or len(df) < 20:
        return f"⚠️ K 线数据不足（{len(df)} 行, kline_period={kline_period}）"
    df = df.tail(periods).reset_index(drop=True)
    ind_dict: dict[str, float | bool | None] = compute_indicators(df)
    # 元数据（股票代码 / 名称 / K 线周期）拼进去；用 dict 字面量直接构造，避免 TypedDict
    # 与匿名 dict[str, ...] 的结构赋值兼容问题
    result: dict[str, float | bool | None | str] = {
        **ind_dict,
        # "as_of" 时效契约字段（框架方案七）：freshness 账本据此识别新鲜读数
        "as_of": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stock_code": code,
        "stock_name": name,
        "kline_period": kline_period,
        "kline_label": (
            f"{kline_period}m"
            if is_min_k
            else "D"
            if kline_period == 101
            else "W"
            if kline_period == 102
            else "M"
            if kline_period == 103
            else "?"
        ),
        "kline_count": len(df),
    }
    return json.dumps(result, ensure_ascii=False, default=str)


@ai_tools(category="common", capability_domain="AI模拟盘")
async def stock_is_trading_day(ctx: RunContext[ToolContext]) -> str:
    """判断当前是否 A 股交易日 + 是否在交易时段。"""
    td: bool = is_a_share_trading_day()
    tt: bool = is_trading_time()
    _, _, desc = trading_day_summary()
    view: _TradingDayView = {
        "is_trading_day": td,
        "is_trading_time": tt,
        "should_decide": td and tt,
        "desc": desc,
    }
    return json.dumps(view, ensure_ascii=False)
