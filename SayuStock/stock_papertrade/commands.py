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
import datetime as _dt

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from . import db as _db, cross_group as _cross
from .sv import sv_papertrade
from .render import draw_leaderboard, draw_account_view
from .permissions import check_admin


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


@sv_papertrade.on_fullmatch(
    ("模拟盘初始化"),
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
            return await bot.send(
                f"ℹ️ 账户已存在（id={existing.id}），已补挂 Kanban 心跳：\n"
                f"  init_root_id   = {init_id or '(空)'}\n"
                f"  period_root_id = {period_id or '(空)'}\n"
                f"下一个交易日开盘即开始自主决策。"
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

    msg = (
        f"✅ AI 模拟盘已开户\n"
        f"群号: {ev.group_id}\n"
        f"初始资金: {acc.initial_cash:,.0f}\n"
        f"模式: {acc.mode}\n"
        f"心跳: {acc.frequency_minutes} 分钟\n\n"
        f"Kanban 心跳树：\n"
        f"  init_root   = {init_id[:16] if init_id else '(空)'}…\n"
        f"  period_root = {period_id[:16] if period_id else '(空)'}…\n\n"
        f"下一个交易日 9:30 起开始自主决策。"
    )
    await bot.send(msg)


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
