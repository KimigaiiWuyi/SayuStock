"""用户命令（``sv_papertrade`` 注册，pm=3）。

公开命令：
1. ``send_init_command``  — ``AI操盘初始化`` (群主 / 管理员权限)
2. ``send_view``          — ``模拟盘查看``
3. ``send_pnl``           — ``模拟盘收益`` (周期: 日/周/月/季/年/ytd/总)
4. ``send_records``       — ``模拟盘记录``
5. ``send_leaderboard``   — ``模拟盘排行`` (群主 / 管理员权限)
6. ``send_query_group``   — ``模拟盘查询 <group_id>`` (群主 / 管理员权限)

所有 DB 操作走 ``from . import db as _db`` 函数内 lazy import；渲染走
``from .render import draw_xxx``；跨群查询走 ``from . import cross_group as _cross``。
"""

import re
import asyncio
import datetime as _dt

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from . import db as _db, cross_group as _cross
from .sv import sv_papertrade
from .render import draw_leaderboard, draw_account_view
from .permissions import check_admin
from .trading_calendar import is_trading_time, is_a_share_trading_day


# ============================================================
# 1) AI操盘初始化（完整 6 步：账户 + Kanban init 树 + Kanban period 树 + APScheduler 周期 + 回填）
# ============================================================
async def _setup_papertrade_kanban_trees(
    bot: Bot, ev: Event, group_id: str, bot_id: str
) -> tuple[str | None, str | None]:
    """注册 Kanban init / period 两棵树并挂 APScheduler cron，返回 (init_root_id, period_root_id)。

    任何一步失败抛 RuntimeError，由调用方把当前进度打到用户消息里。
    与 admin.send_dry_run 的 _build_init_tree / _build_period_tree 逻辑一致，但
    这是真实生产路径（不是 DRY_RUN），失败时直接 raise 让用户看到错误。
    """
    from gsuid_core.ai_core.planning import recurring as kanban_recurring
    from gsuid_core.ai_core.planning.kanban import create_kanban_tree

    # ─── 1) Kanban init 树（leaf-root 模式，setup_agent 跑一次完成初始化） ───
    init_root_id: str | None = None
    try:
        init_root, _ = await create_kanban_tree(
            goal=f"群{group_id} AI模拟盘 init",
            owner_user_id=str(ev.user_id),
            scope_key=f"papertrade_init_{group_id}_{bot_id}",
            bot_id=bot_id,
            persona_name=None,
            bot_self_id="",
            group_id=group_id,
            user_type="group",
            WS_BOT_ID=None,
            session_id=f"papertrade_init_{group_id}",
            user_pm=0,
            broadcast_targets=[group_id],
            subtasks=None,
            recurring_trigger=None,
            recurring_until=None,
            root_agent_profile="papertrade_setup_agent",
        )
        init_root_id = init_root.id
        await _db.PaperAccountRepo.bind_kanban_init(group_id, bot_id, init_root_id)
    except Exception as e:
        raise RuntimeError(f"Kanban init 树创建失败: {type(e).__name__}: {e}") from e

    # ─── 2) Kanban period 树（3 子任务：decision/snapshot/monthly_report） ───
    period_root_id: str | None = None
    try:
        subtasks: list[dict] = [
            {
                "description": "查询账户/候选池/持仓 → 决策 → papertrade_*_insert/upsert 写入",
                "agent_profile": "papertrade_decision_agent",
                "recurring_trigger": "cron:0,30 9-14 * * 1-5",
            },
            {
                "description": "收盘后写当日快照（position_value / total_equity / pnl_pct）",
                "agent_profile": "papertrade_decision_agent",
                "recurring_trigger": "cron:35 15 * * 1-5",
            },
            {
                "description": "月初出复盘报告（月收益 / 胜率 / 最大回撤）",
                "agent_profile": "papertrade_reporter_agent",
                "recurring_trigger": "cron:0 9 1 * *",
            },
        ]
        period_root, _ = await create_kanban_tree(
            goal=f"群{group_id} AI模拟盘 周期托管",
            owner_user_id=str(ev.user_id),
            scope_key=f"papertrade_period_{group_id}_{bot_id}",
            bot_id=bot_id,
            persona_name=None,
            bot_self_id="",
            group_id=group_id,
            user_type="group",
            WS_BOT_ID=None,
            session_id=f"papertrade_period_{group_id}",
            user_pm=0,
            broadcast_targets=[group_id],
            subtasks=subtasks,
            recurring_trigger="cron:0 9 * * 1-5",
            recurring_until=None,
            root_agent_profile="",
        )
        period_root_id = period_root.id
        await _db.PaperAccountRepo.bind_kanban_period(group_id, bot_id, period_root_id)
        # 关键：把周期模板挂到 APScheduler，cron 到了才会克隆实例运行
        ok: bool = kanban_recurring.schedule_template(period_root_id, "cron:0 9 * * 1-5")
        if not ok:
            raise RuntimeError(
                f"schedule_template 返回 False（root_id={period_root_id[:8]}…），"
                f"详见 gsuid_core.ai_core.planning.recurring logger"
            )
    except Exception as e:
        raise RuntimeError(f"Kanban period 树创建失败: {type(e).__name__}: {e}") from e

    return init_root_id, period_root_id


async def _kick_immediate_decision(ev: Event, group_id: str, bot_id: str) -> None:
    """fire-and-forget 立即触发一次 papertrade_decision_agent。

    决策代理跑完后会通过 ``emit_proactive_message`` 把「📈 AI 模拟盘·操盘播报」
    推群。这里 ``asyncio.create_task`` 不 await——保持 task 引用防 GC，
    由框架 event loop 在后台跑完。

    ``session_id_suffix`` 用 ``init_decision_{group_id}`` 和后续 cron 实例的
    会话串号隔开。
    """
    try:
        from gsuid_core.ai_core.capability_agents.runner import run_capability_agent

        task_prompt = (
            f"为群{group_id} 在 bot {bot_id} 上立即执行一次 AI 模拟盘心跳决策（init-time 立即触发）。\n"
            f"Step 1: papertrade_account_query → 拿 cash / mode / enabled\n"
            f"Step 2: papertrade_position_list → 拿当前持仓\n"
            f"Step 3: stock_is_trading_day → 确认开盘\n"
            f"Step 4: 选 1~3 只候选股（自选 / 持仓 / 大盘热股）跑 stock_indicators + stock_financials\n"
            f"Step 5: 综合决策 action=buy/sell/hold：\n"
            f"  - buy  → papertrade_match_order → papertrade_trade_insert\n"
            f"         → papertrade_position_upsert → papertrade_decision_insert\n"
            f"  - sell → papertrade_match_order → papertrade_trade_insert(realized_pnl)\n"
            f"         → papertrade_position_upsert(qty=0) → papertrade_decision_insert\n"
            f"  - hold → 仅 papertrade_decision_insert（reason 详细写为什么不动）\n"
            f"完成后简短回报：action / 持仓数量 / 决策 ID。决策结果会通过 emit_proactive_message 自动推群。"
        )
        # 显式持有 task 引用防 GC（asyncio 不会回收还有强引用的 task）
        _decision_task: asyncio.Task = asyncio.create_task(
            run_capability_agent(
                profile_id="papertrade_decision_agent",
                task=task_prompt,
                ev=ev,
                bot=None,  # type: ignore[arg-type]  -- decision_agent 自己从 ctx.deps.bot 拿
                session_id_suffix=f"init_decision_{group_id}",
            )
        )
        # 不 await，让它在后台跑；fire-and-forget 失败由 run_capability_agent 内部 logger 兜底
        _ = _decision_task
    except Exception as e:
        # fire-and-forget 失败不阻塞 init 主流程；只记日志，由 Kanban cron 兜底
        from gsuid_core.logger import logger

        logger.exception(f"[SayuStock][PaperTrade] init 立即决策失败: {e}")


@sv_papertrade.on_fullmatch(
    ("模拟盘初始化",),
    to_ai="""初始化 AI 模拟盘账户（默认 100w 现金，平衡模式）。一次完整流程，包含：

1) SQLModel 建账户（100w 现金，balanced 模式）
2) Kanban init 树（leaf-root / papertrade_setup_agent，单次执行把账户建好）
3) Kanban period 树（3 子任务）：
   - 决策代理（cron:0,30 9-14 * * 1-5，工作日每 30 分钟看盘）
   - 决策代理（cron:35 15 * * 1-5，工作日收盘写当日快照）
   - 复盘代理（cron:0 9 1 * *，每月 1 日 09:00 出月报）
4) APScheduler 周期挂载（cron:0 9 * * 1-5）

仅群主/管理员可触发；已开户直接返回原账户。

Args:
    text: 自定义初始资金（如 2000000），留空用默认 100w
""",
)
async def send_init_command(bot: Bot, ev: Event):
    """用户输入「模拟盘初始化」时执行。

    完整 6 步：
        1) check_admin（pm <= 1）
        2) PaperAccountRepo.get_or_create 建 SQLModel 账户
        3) register_kanban_task 建 init 树（papertrade_setup_agent）
        4) register_kanban_task 建 period 树（3 子任务）
        5) schedule_template 挂 APScheduler
        6) bind_kanban_init/period 回填 root_id

    重要：之前实现只跑了步骤 2，没有 Kanban 心跳树，开盘时不会自动决策——
    这是 2026-07-01 群里发现「开盘后 AI 啥都没干」的根因。本版补齐 1+3+4+5+6。
    """
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可初始化 AI 模拟盘")

    group_id: str = str(ev.group_id)
    bot_id: str = ev.bot_id

    # ── 可选：自定义初始资金（"AI操盘初始化 2000000"） ──
    initial_cash: float = 1_000_000.0
    raw_text: str = ev.text.strip() if ev.text else ""
    if raw_text:
        try:
            parsed: float = float(raw_text.replace(",", "").replace(" ", ""))
            if 10_000 <= parsed <= 1_000_000_000:
                initial_cash = parsed
        except (ValueError, TypeError):
            return await bot.send(f"⚠️ 初始资金格式错误：{raw_text!r}（须为 1w~1亿的数字）")

    # ── 幂等：已有账户则直接返回现状，不重建 Kanban 树 ──
    existing = await _db.PaperAccountRepo.get(group_id, bot_id)
    if existing is not None:
        # 但如果心跳树丢了（手动清盘 / 升级迁移），顺手补挂
        if not existing.kanban_init_root_id or not existing.kanban_period_root_id:
            try:
                init_id, period_id = await _setup_papertrade_kanban_trees(bot, ev, group_id, bot_id)
            except RuntimeError as e:
                return await bot.send(
                    f"⚠️ 账户已存在（id={existing.id}），但补挂 Kanban 心跳失败：{e}\n"
                    f"AI 开盘不会自动决策。请联系 SUPERUSER 通过「AI操盘清盘」重置。"
                )
            # 补挂同样立即踢 init + （开盘时）踢一次 decision
            await _kick_after_kanban_ready(ev, group_id, bot_id, init_id)
            is_market = _is_market_open_now()
            return await bot.send(
                f"ℹ️ 账户已存在（id={existing.id}），已补挂 Kanban 心跳：\n"
                f"  init_root_id   = {init_id or '(空)'}\n"
                f"  period_root_id = {period_id or '(空)'}\n"
                f"已立即触发 init 验证"
                f"{' + 一次决策心跳' if is_market else '（非开盘时段，跳过决策）'}。"
            )
        return await bot.send(
            f"ℹ️ 本群已开户 AI 模拟盘（id={existing.id}），无需重复初始化。\n如需重置请用「AI操盘清盘」（master-only）。"
        )

    # ── 步骤 2：建 SQLModel 账户 ──
    try:
        acc = await _db.PaperAccountRepo.get_or_create(
            group_id=group_id,
            bot_id=bot_id,
            initial_cash=initial_cash,
            mode="balanced",
            initialized_by=str(ev.user_id),
        )
    except Exception as e:
        return await bot.send(f"⚠️ 建账户失败: {type(e).__name__}: {e}")

    # ── 步骤 3+4+5+6：建 Kanban 两棵树 + APScheduler + 回填 root_id ──
    try:
        init_id, period_id = await _setup_papertrade_kanban_trees(bot, ev, group_id, bot_id)
    except RuntimeError as e:
        # Kanban 失败时账户已建，回滚账户（避免"半初始化"脏状态）
        try:
            await _db.PaperAccountRepo.reset_account(group_id, bot_id)
        except Exception:
            pass
        return await bot.send(f"⚠️ 初始化失败：{e}\n账户已自动回滚。请检查 gsuid_core.ai_core 是否就绪后重试。")

    # ── 步骤 7：fire-and-forget 立即踢 init + （开盘时）踢一次 decision ──
    #    原因：用户发"AI操盘初始化"在开盘时段，希望 AI 立刻开始看盘，不等到
    #    下个 30 分钟 tick。踢的过程不阻塞主消息：init 通常 < 5s，decision
    #    ~30-60s，二者都在后台跑完。
    await _kick_after_kanban_ready(ev, group_id, bot_id, init_id)
    is_market: bool = _is_market_open_now()

    msg = (
        f"✅ AI 模拟盘已开户\n"
        f"群号: {ev.group_id}\n"
        f"初始资金: {acc.initial_cash:,.0f}\n"
        f"模式: {acc.mode}\n"
        f"心跳: {acc.frequency_minutes} 分钟\n\n"
        f"Kanban 心跳树：\n"
        f"  init_root   = {init_id[:16] if init_id else '(空)'}…\n"
        f"  period_root = {period_id[:16] if period_id else '(空)'}…\n\n"
        f"已立即触发 init 验证"
        f"{' + 一次决策心跳（开盘中，决策结果稍后推群）' if is_market else '（非开盘时段，跳过决策；等下次 cron）'}。"
    )
    await bot.send(msg)


# ============================================================
# Helpers：init 完成后立即 kick
# ============================================================
def _is_market_open_now() -> bool:
    """是否处于 A 股开盘时段（交易日 + 交易时段）。"""
    return is_a_share_trading_day() and is_trading_time()


async def _kick_after_kanban_ready(ev: Event, group_id: str, bot_id: str, init_id: str | None) -> None:
    """Kanban 树就绪后立即 fire-and-forget 触发 init 验证 + （开盘时）一次决策。

    - init 树永远踢一次——验证账户 / 回填 root_id / papertrade_setup_agent 自检。
    - decision 仅在 ``_is_market_open_now()`` 为真时踢——非开盘时段让 cron 兜底，
      避免浪费 token。

    所有 kick 都是 ``asyncio.create_task``，不阻塞 send 成功消息；失败由
    Kanban cron + 日志兜底，不影响主流程。
    """
    if not init_id:
        return

    # (a) 永远踢 init 树（fire-and-forget）
    try:
        from gsuid_core.ai_core.planning.kanban_executor import kick_root

        _init_task: asyncio.Task = asyncio.create_task(kick_root(init_id))
        # 显式持有 task 引用防 GC
        _ = _init_task
    except Exception as e:
        from gsuid_core.logger import logger

        logger.exception(f"[SayuStock][PaperTrade] kick init 失败: {e}")

    # (b) 仅在开盘时段踢一次 decision
    if _is_market_open_now():
        await _kick_immediate_decision(ev, group_id, bot_id)


# ============================================================
# 2) 模拟盘查看
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘查看",),
)
async def send_view(bot: Bot, ev: Event):
    img = await draw_account_view(str(ev.group_id), ev.bot_id)
    await bot.send(img)


# ============================================================
# 3) 模拟盘收益
# ============================================================
@sv_papertrade.on_prefix(
    ("模拟盘收益",),
)
async def send_pnl(bot: Bot, ev: Event):
    text: str = ev.text.strip()
    # 把"近 N 天"和"自某日期"两类周期统一到 since: datetime | None。
    # 关键修复：ytd = 今年 1/1 至今，不是 now-365d，否则 6 月份触发会少算 6 个月。
    mapping: dict[str, int | None] = {
        "日": 1,
        "今日": 1,
        "今天": 1,
        "today": 1,
        "周": 7,
        "本周": 7,
        "this_week": 7,
        "月": 30,
        "本月": 30,
        "this_month": 30,
        "季": 90,
        "本季": 90,
        "年": 365,
        "本年": 365,
        "今年": 365,
        # 注意："ytd" 单独走 since_calc，不进 mapping
        "总": None,
        "全部": None,
        "all": None,
    }

    if text == "ytd":
        now = _dt.datetime.now()
        since: _dt.datetime | None = _dt.datetime(now.year, 1, 1)
    elif text not in mapping:
        return await bot.send("⚠️ 周期须为 日/周/月/季/年/ytd/总")
    else:
        days = mapping[text]
        since = None if days is None else _dt.datetime.now() - _dt.timedelta(days=days)

    agg = await _db.PaperTradeRepo.aggregate_pnl(
        str(ev.group_id),
        ev.bot_id,
        since=since,
    )
    period: str = text if text in ("总", "全部", "all") else f"近{text}"
    msg = (
        f"📊 AI 操盘 · {period}盈亏\n"
        f"已实现盈亏: {agg['total_pnl']:+,.2f}\n"
        f"总成交额: {agg['total_amount']:,.0f}\n"
        f"总手续费: {agg['total_fee']:,.2f}\n"
        f"交易笔数: {agg['trade_count']}"
    )
    await bot.send(msg)


# ============================================================
# 4) 模拟盘记录
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘记录",),
)
async def send_records(bot: Bot, ev: Event):
    rows = await _db.PaperTradeRepo.list_by_account(
        str(ev.group_id),
        ev.bot_id,
        limit=20,
    )
    if not rows:
        return await bot.send("ℹ️ 暂无交易记录")
    lines: list[str] = ["【AI 操盘 · 最近 20 笔交易】"]
    for t in rows:
        side = "买" if t.side == "buy" else "卖"
        executed_at: _dt.datetime | None = t.executed_at
        at = executed_at.strftime("%m-%d %H:%M") if executed_at else "?"
        pnl = f" 盈{t.realized_pnl:+.0f}" if t.side == "sell" and t.realized_pnl else ""
        lines.append(f"[{at}] {side} {t.stock_name or t.stock_code} {t.qty}@{t.price:.2f} 费{t.fee:.1f}{pnl}")
    await bot.send("\n".join(lines))


# ============================================================
# 5) 模拟盘排行
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘排行",),
)
async def send_leaderboard(bot: Bot, ev: Event):
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可看跨群排行")
    img = await draw_leaderboard()
    await bot.send(img)


# ============================================================
# 6) 模拟盘查询
# ============================================================
@sv_papertrade.on_prefix(
    ("模拟盘查询",),
)
async def send_query_group(bot: Bot, ev: Event):
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可跨群查询")
    target: str = ev.text.strip()
    if not re.fullmatch(r"\d{5,15}", target):
        return await bot.send("⚠️ 群号格式错误（5~15 位数字）")
    acc = await _cross.query_account(target)
    if not acc:
        return await bot.send(f"ℹ️ 群 {target} 未开通 AI 模拟盘")
    positions = await _cross.query_positions(target)
    snap = await _cross.query_latest_snapshot(target)
    lines: list[str] = [
        f"【跨群查询 · 群 {target}】",
        f"模式: {acc['mode']}  状态: {'🟢' if acc['enabled'] else '🔴'}",
        f"现金: {acc['cash']:,.0f}  初始: {acc['initial_cash']:,.0f}",
    ]
    if snap:
        lines.append(
            f"总资产: {snap['total_equity']:,.0f}  盈亏: {snap['total_pnl']:+,.0f} ({snap['total_pnl_pct']:+.2f}%)"
        )
    if positions:
        lines.append(f"持仓: {len(positions)} 只")
        for p in positions[:5]:
            lines.append(f"  - {p['stock_name'] or p['stock_code']} ×{p['qty']}@{p['avg_cost']:.2f}")
    await bot.send("\n".join(lines))
