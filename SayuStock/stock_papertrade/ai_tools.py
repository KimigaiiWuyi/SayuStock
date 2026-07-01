"""AI 模拟盘 ai_tools 集合。

11 个 ai_tools，分三类（**没有重叠**，每个工具只做一件事）：
- 业务/账本（**4 个只读**，capability_domain="AI模拟盘"，category="common"）
  —— 主 persona + 能力代理都能用
- 能力代理私有（**4 个写**，capability_domain="AI模拟盘"，category="default" + visible_when）
  —— 仅 papertrade_*_agent 可见；防止主 persona 误调写操作
- 通用辅助（3 个，capability_domain="AI模拟盘"，category="common"）
  —— 财报 / 指标 / 交易日判断

**删除的工具**（已收敛到 trigger 或被废弃）：
- ~~papertrade_account_create~~ —— 与 trigger ``send_init_command`` 重叠；统一走 trigger
- ~~papertrade_account_update~~ —— 死代码（没有命令 / 流程使用）

**所有 group_id 参数**：留空时从 ctx.deps.ev.group_id 推断。
"""

# pyright/basedpyright 文件级指令 —— 仅作用于本文件。
# 本文件依赖 ``gsuid_core.ai_core.{models,register,planning.runtime}`` 等
# 框架模块（基于 pyright 静态分析插件路径看不见根包），还依赖 ``@with_session``
# 装饰器动态签名以及 ``@ai_tools`` 等未注解装饰器 —— 这些都是上游已知限制，
# 不是本文件代码错误。开启以下噪声规则没有任何价值：
# - reportMissingImports: 根包导入被插件路径屏蔽
# - reportUnknownVariableType / reportUnknownMemberType:
#   上游 ``ToolContext`` / ``Event`` / ``ai_tools`` 等未注解，牵连到所有下
#   游属性 (acc.cash, ev.group_id, ...)。
# - reportUnknownArgumentType: ``get_gg`` 等上游返回 Any，传参时无法 narrow。
# - reportCallIssue: @with_session 装饰器从签名里隐藏 session 参数，
#   基于 pyright 看不到这个变换
# - reportUntypedFunctionDecorator: @ai_tools 等装饰器无类型注解，basedpyright
#   看不到 wrap 后函数的签名，导致重复报告所有下层未知类型。
# - reportUnusedParameter: 框架要求的 ctx 参数在本工具实现里未必使用
#   （如某些通用工具根本不看 ev）。
# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportCallIssue=false, reportUntypedFunctionDecorator=false, reportUnusedParameter=false

import json
import datetime as _dt
from typing import TypedDict

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools

from . import db
from .indicators import klines_to_df, klines_to_df_mins, compute_indicators
from .trading_calendar import is_trading_time, trading_day_summary, is_a_share_trading_day
from ..utils.stock.request import get_gg
from ..utils.eastmoney_finance import (
    get_cash_flow,
    get_balance_sheet,
    get_income_statement,
    get_financial_snapshot,
)
from ..utils.stock.request_utils import get_code_id


# ============================================================
# TypedDict：每个 ai_tool 的 JSON 输出契约，供下游 JSON 序列化
# ============================================================
class _AccountView(TypedDict):
    group_id: str
    bot_id: str
    cash: float
    initial_cash: float
    principal: float
    total_equity: float
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
    price: float
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
    try:
        from gsuid_core.ai_core.planning.runtime import get_plan_context

        plan_ctx: object | None = get_plan_context()
    except Exception:
        return False
    if plan_ctx is None:
        return False
    profile: str = getattr(plan_ctx, "agent_profile", "") or ""
    return profile.startswith("papertrade_") or profile == "stock_agent"


# ============================================================
# 1) 业务/账本工具（6 个，主人格 + 能力代理共用）
# ============================================================


@ai_tools(category="common", capability_domain="AI模拟盘")
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
    gid: str = _resolve_group_id(ctx, group_id)
    bid: str = _resolve_bot_id(ctx, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    acc = await db.PaperAccountRepo.get(gid, bid)
    if not acc:
        return f"ℹ️ 群 {gid} 在 {bid} 上尚未开通 AI 模拟盘。发送「AI操盘初始化」开户。"
    last_decided: _dt.datetime | None = acc.last_decided_at
    view: _AccountView = {
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
        "last_decided_at": last_decided.isoformat() if last_decided else None,
    }
    return json.dumps(view, ensure_ascii=False)


@ai_tools(category="common", capability_domain="AI模拟盘")
async def papertrade_position_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
) -> str:
    """列出某群 AI 模拟盘当前持仓。"""
    gid: str = _resolve_group_id(ctx, group_id)
    bid: str = _resolve_bot_id(ctx, bot_id)
    if not gid or not bid:
        return "⚠️ 无法确定 group_id/bot_id"
    positions = await db.PaperPositionRepo.list_by_account(gid, bid)
    if not positions:
        return f"ℹ️ 群 {gid} 当前无持仓。"
    items: list[_PositionItem] = []
    for p in positions:
        opened: _dt.datetime | None = p.opened_at
        items.append(
            {
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "secid": p.secid,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "opened_at": opened.isoformat() if opened else None,
            }
        )
    return json.dumps(items, ensure_ascii=False)


@ai_tools(category="common", capability_domain="AI模拟盘")
async def papertrade_trade_list(
    ctx: RunContext[ToolContext],
    group_id: str = "",
    bot_id: str = "",
    stock_code: str = "",
    limit: int = 20,
) -> str:
    """查询某群 AI 模拟盘交易流水。"""
    gid: str = _resolve_group_id(ctx, group_id)
    bid: str = _resolve_bot_id(ctx, bot_id)
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
                "decided_at": decided_at.isoformat() if decided_at else None,
                "executed_at": executed_at.isoformat() if executed_at else None,
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
    gid: str = _resolve_group_id(ctx, group_id)
    bid: str = _resolve_bot_id(ctx, bot_id)
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

      - ``indicators`` —— JSON 字典字符串。由 ``stock_indicators`` /
                       ``stock_financials`` / ``stock_is_trading_day`` 三工具的原返回
                       拼装；若全部不可达，**传 '{}'**，不要塞中文。

      - ``score``    —— -1.0 ~ +1.0；buy → +，sell → -，hold → 0。

      - ``blocked_by`` —— 风控拦截原因；hold 不带风控时传 ''。
    """
    gid: str = _resolve_group_id(ctx)
    bid: str = _resolve_bot_id(ctx)

    # 防御：reason 含异常控制字符（\r / \t / 全角空格连用）时归一化，
    #     且长度上限 200 字（超长截断+省略号），避免下游播报挤糊
    norm_reason: str = "".join(ch if ch.isprintable() else " " for ch in (reason or "")).strip()
    if len(norm_reason) > 200:
        norm_reason = norm_reason[:197] + "..."

    # 防御：indicators 必须是合法 JSON；非法时降级 '{}'
    import json as _json

    if indicators and indicators.strip() and indicators.strip() != "{}":
        try:
            _json.loads(indicators)
        except Exception:
            indicators = "{}"
    else:
        indicators = ""

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
    """
    gid: str = _resolve_group_id(ctx)
    bid: str = _resolve_bot_id(ctx)
    try:
        t = await db.PaperTradeRepo.append_with_cash_update(
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
    except (ValueError, RuntimeError) as e:
        # side 非法 / 账户不存在——不写库，明示错误给 LLM
        return f"⚠️ trade_insert 失败: {e}"
    cash_delta: float = -(amount + fee) if side == "buy" else (amount - fee + realized_pnl)
    if side == "buy":
        formula: str = "buy: cash -= amount+fee"
    else:
        formula = "sell: cash += amount-fee+realized_pnl, principal += realized_pnl"
    return f"ok trade_id={t.id}  cash_delta={cash_delta:+,.2f}  ({formula})"


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
    """更新持仓（qty=0 时删除记录；仅 papertrade_*_agent 调用）。

    **本工具不动账户现金**——cash 的增减已由 ``papertrade_trade_insert``
    在同一 session 内自动维护。position_upsert 只操作持仓表
    (SayuPaperPosition)：qty>0 时 upsert，qty=0 时 DELETE 行。

    调用顺序示例（buy 路径）：
        1. papertrade_match_order(buy)  → 拿 fee_total / actual_qty / amount
        2. papertrade_trade_insert(buy)  → 写 trade + 自动扣 cash
        3. papertrade_position_upsert(qty, avg_cost=price)  → 写持仓
        4. papertrade_decision_insert(action='buy', trade_id=...)
    """
    gid: str = _resolve_group_id(ctx)
    bid: str = _resolve_bot_id(ctx)
    p = await db.PaperPositionRepo.upsert(
        gid,
        bid,
        stock_code=stock_code,
        stock_name=stock_name,
        secid=secid,
        qty=qty,
        avg_cost=avg_cost,
    )
    return f"ok pos_id={p.id if p else 0}  （cash 由 trade_insert 自动维护）"


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
    view: _MatchResultView = {
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
    }
    return json.dumps(view, ensure_ascii=False)


# ============================================================
# 3) 通用辅助（3 个）
# ============================================================


@ai_tools(category="common", capability_domain="AI模拟盘")
async def stock_financials(
    ctx: RunContext[ToolContext],
    stock_code: str,
    report: str = "main",
) -> str:
    """获取股票财报数据（F10 / 利润表 / 资产负债表 / 现金流 / 主要指标）。

    Args:
        stock_code: 6 位股票代码
        report: ``main``（最新一期主要指标）/ ``income`` / ``balance`` / ``cashflow``

    返回 ``report='main'`` 时是一段 JSON 字典，重点字段：

        **标准行业**（制造业 / 消费 / 科技等）：
            ``roe`` / ``revenue_yoy`` / ``profit_yoy`` / ``gross_margin`` /
            ``net_margin`` / ``debt_ratio`` / ``eps`` / ``bps`` /
            ``report_date`` / ``_industry_type`` / ``_gap`` / ``_raw_keys_present``

        **银行股专属**（``_industry_type='bank'`` 时追加）：
            ``jroa``（扣非 ROE，银行偏好口径）/
            ``net_interest_margin``（净息差）/
            ``npl_ratio``（不良率）/
            ``provision_coverage``（拨备覆盖率）/
            ``core_capital_adequacy_ratio``（核心一级资本充足率）

        **保险股专属**（``_industry_type='insurance'`` 时追加）：
            ``solvency_ar``（偿付能力充足率）/
            ``premium_income``（保费收入）

        **券商专属**（``_industry_type='broker'`` 时追加）：
            ``main_business_income``（主营营收）

    ⚠️ **行业识别自动判断**（基于东财 main_financial 表特征字段）：
       - 看到 JROA / NPL_RATIO → 银行
       - 看到 SOLVENCY_AR / PREMIUM_INCOME → 保险
       - 看到 MAIN_BUSINESS_INCOME 但缺 XSMLL → 券商
       - 其余 → standard

    ⚠️ **银行股典型返回**（如 000001 平安银行）：
        '{"_industry_type": "bank", "roe": null, "revenue_yoy": null, "gross_margin": null,
          "net_margin": null, "debt_ratio": 90.98, "bps": 23.91, "report_date": "2026-03-31",
          "jroa": 8.5, "net_interest_margin": 1.83, "npl_ratio": 1.06,
          "provision_coverage": 246.0, "core_capital_adequacy_ratio": 9.42,
          "_raw_keys_present": ["JROA", "ZCFZL", "BPS", "NPL_RATIO", ...],
          "_gap": ["roe", "revenue_yoy", "gross_margin", "net_margin"]}'

        → 这是银行股正常形态，**不要**因 ``roe`` 为 null 就说"数据缺失"。
          银行股评估口径改为：jroa ≥ 行业均值 / npl_ratio < 1.5% /
          provision_coverage > 150% / core_capital_adequacy_ratio > 8.5% /
          net_interest_margin 趋势向上。

    在 ``papertrade_decision_insert(reason=...)`` 里的推荐写法：
        标准股：'ROE 同比+0.5pct 至 12.3%，毛利率 38.7% 创近 4 季新高'
        银行股：'银行股口径——jroa=8.5% 持平行业均值，npl=1.06% 较年初降 4bp，
                拨备覆盖率 246% 充足，资本充足率 9.42% 偏紧需关注分红能力'
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


@ai_tools(category="common", capability_domain="AI模拟盘")
async def stock_indicators(
    ctx: RunContext[ToolContext],
    stock_code: str,
    periods: int = 60,
    kline_period: int = 101,
) -> str:
    """计算股票技术指标（MA / MACD / RSI / CMF / BOLL / CCI / BBI / 支撑压力 / 波动率等）。

    Args:
        stock_code: 6 位代码或名称（先用 get_code_id 解析）
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

    # 拉 K 线（upstream get_gg 无返回类型注解 → 视为 dict | str）
    end: _dt.datetime = _dt.datetime.now()
    # 分钟 K 拉近几天即可（按 kline_period 自适应）
    if is_min_k:
        start: _dt.datetime = end - _dt.timedelta(days=10)
    else:
        start = end - _dt.timedelta(days=int(periods * 1.5) + 30)
    # ``get_gg`` 无返回类型注解；运行时返回 ``str`` (错误) 或 ``dict`` 负载。
    # 显式联合类型让下游 isinstance 有意义，并避免基于 pyright 把它推成
    # ``Dict[str, Any]`` 后报不必要的 isinstance。
    raw: str | dict[str, object] = await get_gg(code, f"single-stock-kline-{kline_period}", start, end)
    if isinstance(raw, str):
        return f"⚠️ 拉 K 线失败: {raw}"
    payload: dict[str, object] = raw
    data: object = payload.get("data")
    data_dict: dict[str, object] = data if isinstance(data, dict) else {}
    klines_obj: object = data_dict.get("klines")
    klines: list[str] = [k for k in klines_obj if isinstance(k, str)] if isinstance(klines_obj, list) else []
    if not klines:
        return f"⚠️ {name}({code}) 无 K 线数据 (kline_period={kline_period})"
    if is_min_k:
        df = klines_to_df_mins(klines)
    else:
        df = klines_to_df(klines)
    if df.empty or len(df) < 20:
        return f"⚠️ K 线数据不足（{len(df)} 行, kline_period={kline_period}）"
    df = df.tail(periods).reset_index(drop=True)
    ind_dict: dict[str, float | bool | None] = compute_indicators(df)
    # 元数据（股票代码 / 名称 / K 线周期）拼进去；用 dict 字面量直接构造，避免 TypedDict
    # 与匿名 dict[str, ...] 的结构赋值兼容问题
    result: dict[str, float | bool | None | str] = {
        **ind_dict,
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
