"""AI 模拟盘 ai_tools 集合。

13 个 ai_tools，分三类：
- 业务/账本（6 个，capability_domain="AI模拟盘"）—— 主人格 + 能力代理共用
- 能力代理私有（4 个，visible_when 限定）—— 仅 papertrade_*_agent 可见
- 通用辅助（3 个）—— 财报 / 指标 / 交易日判断

所有 group_id 参数：留空时从 ctx.deps.ev.group_id 推断。
"""

import json
from typing import Any, Dict

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools

from . import db
from .indicators import klines_to_df, compute_indicators
from .trading_calendar import is_trading_time, is_a_share_trading_day
from ..utils.stock.request import get_gg
from ..utils.eastmoney_finance import (
    get_cash_flow,
    get_balance_sheet,
    get_income_statement,
    get_financial_snapshot,
)
from ..utils.stock.request_utils import get_code_id


# ============================================================
# 上下文推断辅助
# ============================================================
def _resolve_group_id(ctx: RunContext[ToolContext], group_id: str = "") -> str:
    if group_id:
        return str(group_id)
    ev = ctx.deps.ev
    return str(ev.group_id) if ev and ev.group_id else ""


def _resolve_bot_id(ctx: RunContext[ToolContext], bot_id: str = "") -> str:
    if bot_id:
        return bot_id
    ev = ctx.deps.ev
    return ev.bot_id if ev and ev.bot_id else ""


def _visible_to_papertrade_agent(ctx: RunContext[ToolContext]) -> bool:
    """仅 papertrade_*_agent 可见（visible_when）"""
    plan_ctx = None
    try:
        from gsuid_core.ai_core.planning.runtime import get_plan_context

        plan_ctx = get_plan_context()
    except Exception:
        return False
    if plan_ctx is None or not plan_ctx.agent_profile:
        return False
    return plan_ctx.agent_profile.startswith("papertrade_") or plan_ctx.agent_profile == "stock_agent"


# ============================================================
# 1) 业务/账本工具（6 个，主人格 + 能力代理共用）
# ============================================================


@ai_tools(category="default", capability_domain="AI模拟盘")
async def papertrade_account_query(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """查询 AI 模拟盘账户状态（当前群或指定群）。

    Args:
        group_id: 群号；留空用当前会话群号
        bot_id: 平台；留空用当前 bot
    """
    gid = _resolve_group_id(ctx, group_id)
    bid = _resolve_bot_id(ctx, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    acc = await db.PaperAccountRepo.get(gid, bid)
    if not acc:
        return f"ℹ️ 群 {gid} 在 {bid} 上尚未开通 AI 模拟盘。发送「AI操盘初始化」开户。"
    return json.dumps(
        {
            "group_id": gid,
            "bot_id": bid,
            "cash": acc.cash,
            "initial_cash": acc.initial_cash,
            "principal": acc.principal,
            "total_equity": acc.cash + 0,  # 简版；详细含持仓市值由调用方补
            "mode": acc.mode,
            "frequency_minutes": acc.frequency_minutes,
            "enabled": acc.enabled,
            "kanban_init_root_id": acc.kanban_init_root_id,
            "kanban_period_root_id": acc.kanban_period_root_id,
            "last_decided_at": acc.last_decided_at.isoformat() if acc.last_decided_at else None,
        },
        ensure_ascii=False,
    )


@ai_tools(category="default", capability_domain="AI模拟盘")
async def papertrade_account_create(
    ctx: RunContext[ToolContext],
    initial_cash: float = 1_000_000.0,
    mode: str = "balanced",
) -> str:
    """创建 AI 模拟盘账户（已存在则返回原账户）。

    Args:
        initial_cash: 初始资金（1w~1亿）
        mode: balanced / aggressive / conservative
    """
    gid = _resolve_group_id(ctx)
    bid = _resolve_bot_id(ctx)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    if initial_cash < 10_000 or initial_cash > 1_000_000_000:
        return "⚠️ initial_cash 须在 1w~1亿之间"
    if mode not in ("balanced", "aggressive", "conservative"):
        return f"⚠️ mode 非法: {mode}"
    ev = ctx.deps.ev
    init_by = str(ev.user_id) if ev else None
    acc = await db.PaperAccountRepo.get_or_create(
        gid,
        bid,
        initial_cash=initial_cash,
        mode=mode,
        initialized_by=init_by,
    )
    return f"✅ 账户已就绪 (group={gid}, cash={acc.cash}, mode={acc.mode}, id={acc.id})"


@ai_tools(category="default", capability_domain="AI模拟盘")
async def papertrade_account_update(
    ctx: RunContext[ToolContext],
    enabled: int = -1,
    mode: str = "",
) -> str:
    """更新 AI 模拟盘账户的开关/模式。

    Args:
        enabled: 0=关闭 / 1=开启 / -1=不修改（默认）
        mode: balanced/aggressive/conservative/""（不修改）
    """
    gid = _resolve_group_id(ctx)
    bid = _resolve_bot_id(ctx)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    fields: Dict[str, Any] = {}
    if enabled in (0, 1):
        fields["enabled"] = enabled
    if mode in ("balanced", "aggressive", "conservative"):
        fields["mode"] = mode
    if not fields:
        return "⚠️ 没有要修改的字段"
    acc = await db.PaperAccountRepo.update(gid, bid, **fields)
    if not acc:
        return f"⚠️ 群 {gid} 尚未开户"
    return f"✅ 已更新账户 (enabled={acc.enabled}, mode={acc.mode})"


@ai_tools(category="default", capability_domain="AI模拟盘")
async def papertrade_position_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """列出某群 AI 模拟盘当前持仓。"""
    gid = _resolve_group_id(ctx, group_id)
    bid = _resolve_bot_id(ctx, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    positions = await db.PaperPositionRepo.list_by_account(gid, bid)
    if not positions:
        return f"ℹ️ 群 {gid} 当前无持仓。"
    return json.dumps(
        [
            {
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "secid": p.secid,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            }
            for p in positions
        ],
        ensure_ascii=False,
    )


@ai_tools(category="default", capability_domain="AI模拟盘")
async def papertrade_trade_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
    stock_code: str = "",
    limit: int = 20,
) -> str:
    """查询某群 AI 模拟盘交易流水。"""
    gid = _resolve_group_id(ctx, group_id)
    bid = _resolve_bot_id(ctx, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    rows = await db.PaperTradeRepo.list_by_account(
        gid,
        bid,
        limit=limit,
        stock_code=stock_code or None,
    )
    return json.dumps(
        [
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
                "decided_at": t.decided_at.isoformat() if t.decided_at else None,
                "executed_at": t.executed_at.isoformat() if t.executed_at else None,
            }
            for t in rows
        ],
        ensure_ascii=False,
    )


@ai_tools(category="default", capability_domain="AI模拟盘")
async def papertrade_watchlist_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """查询某群群友关注列表（公开）。"""
    gid = _resolve_group_id(ctx, group_id)
    bid = _resolve_bot_id(ctx, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    items = await db.PaperWatchlistRepo.list_by_account(gid, bid)
    return json.dumps(
        [
            {
                "stock_code": i.stock_code,
                "stock_name": i.stock_name,
                "user_id": i.user_id,
                "note": i.note,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ],
        ensure_ascii=False,
    )


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
    """向决策日志表插入一条记录（仅 papertrade_*_agent 调用）"""
    gid = _resolve_group_id(ctx)
    bid = _resolve_bot_id(ctx)
    d = await db.PaperDecisionRepo.append(
        gid,
        bid,
        action=action,
        stock_code=stock_code or None,
        stock_name=stock_name or None,
        score=score,
        reason=reason,
        indicators=indicators,
        trade_id=trade_id if trade_id > 0 else None,
        blocked_by=blocked_by,
    )
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
    """向交易流水表插入一条记录（仅 papertrade_*_agent 调用）"""
    gid = _resolve_group_id(ctx)
    bid = _resolve_bot_id(ctx)
    t = await db.PaperTradeRepo.append(
        gid,
        bid,
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
        decision_id=decision_id if decision_id > 0 else None,
        mode=mode,
    )
    return f"ok trade_id={t.id}"


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
) -> str:
    """更新持仓（qty=0 时删除记录；仅 papertrade_*_agent 调用）"""
    gid = _resolve_group_id(ctx)
    bid = _resolve_bot_id(ctx)
    p = await db.PaperPositionRepo.upsert(
        gid,
        bid,
        stock_code=stock_code,
        stock_name=stock_name,
        secid=secid,
        qty=qty,
        avg_cost=avg_cost,
    )
    return f"ok pos_id={p.id if p else 0}"


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
    price: float,
    cash_available: float = 0.0,
    position_qty: int = 0,
) -> str:
    """撮合一笔订单（A 股真实费率）；返回 MatchResult JSON 字符串。

    Args:
        side: buy / sell
        stock_code: 股票代码
        qty: 请求股数
        price: 成交价
        cash_available: 账户可用现金（buy 时必填）
        position_qty: 当前持仓股数（sell 时必填）
    """
    from .matcher import match_order

    res = match_order(
        side=side,
        code=stock_code,
        qty=qty,
        price=price,
        cash_available=cash_available,
        position_qty=position_qty,
    )
    return json.dumps(
        {
            "ok": res.ok,
            "side": res.side,
            "code": res.code,
            "requested_qty": res.requested_qty,
            "actual_qty": res.actual_qty,
            "price": res.price,
            "amount": res.amount,
            "commission": res.commission,
            "stamp_tax": res.stamp_tax,
            "fee_total": res.fee_total,
            "reason": res.reason,
        },
        ensure_ascii=False,
    )


# ============================================================
# 3) 通用辅助（3 个）
# ============================================================


@ai_tools(category="default", capability_domain="AI模拟盘")
async def stock_financials(
    ctx: RunContext[ToolContext],
    stock_code: str,
    report: str = "main",
) -> str:
    """获取股票财报数据（F10 / 利润表 / 资产负债表 / 现金流 / 主要指标）。

    Args:
        stock_code: 6 位股票代码
        report: main / income / balance / cashflow
    """
    if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
        return f"⚠️ stock_code 需为 6 位数字代码: {stock_code!r}"
    if report == "main":
        snap = await get_financial_snapshot(stock_code)
        return json.dumps(snap, ensure_ascii=False)
    if report == "income":
        rows = await get_income_statement(stock_code)
    elif report == "balance":
        rows = await get_balance_sheet(stock_code)
    elif report == "cashflow":
        rows = await get_cash_flow(stock_code)
    else:
        return f"⚠️ report 非法: {report}"
    return json.dumps(rows[:8], ensure_ascii=False)  # 最近 8 期


@ai_tools(category="default", capability_domain="AI模拟盘")
async def stock_indicators(
    ctx: RunContext[ToolContext],
    stock_code: str,
    periods: int = 60,
) -> str:
    """计算股票技术指标（MA / MACD / RSI / CMF / 支撑压力 / 波动率等）。

    Args:
        stock_code: 6 位代码或名称（先用 get_code_id 解析）
        periods: 取最近多少根日 K（默认 60）
    """
    # 解析代码
    code_id = await get_code_id(stock_code)
    if code_id is None:
        return f"⚠️ 未找到股票: {stock_code}"
    code, name, _ = code_id
    # 拉日 K
    import datetime as _dt

    end = _dt.datetime.now()
    start = end - _dt.timedelta(days=int(periods * 1.5) + 30)
    raw = await get_gg(code, "single-stock-kline-101", start, end)
    if isinstance(raw, str):
        return f"⚠️ 拉 K 线失败: {raw}"
    klines = raw.get("data", {}).get("klines", [])
    if not klines:
        return f"⚠️ {name}({code}) 无 K 线数据"
    df = klines_to_df(klines)
    if df.empty or len(df) < 30:
        return f"⚠️ K 线数据不足（{len(df)} 行）"
    df = df.tail(periods).reset_index(drop=True)
    ind = compute_indicators(df)
    ind["stock_code"] = code
    ind["stock_name"] = name
    return json.dumps(ind, ensure_ascii=False, default=str)


@ai_tools(category="default", capability_domain="AI模拟盘")
async def stock_is_trading_day(ctx: RunContext[ToolContext]) -> str:
    """判断当前是否 A 股交易日 + 是否在交易时段。"""
    td = is_a_share_trading_day()
    tt = is_trading_time()
    from .trading_calendar import trading_day_summary

    _, _, desc = trading_day_summary()
    return json.dumps(
        {
            "is_trading_day": td,
            "is_trading_time": tt,
            "should_decide": td and tt,
            "desc": desc,
        },
        ensure_ascii=False,
    )
