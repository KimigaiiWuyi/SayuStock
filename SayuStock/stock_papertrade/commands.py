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

    # ─── 2) Kanban period 树（4 子任务：decision/snapshot/monthly_report/pool_refresh） ───
    period_root_id: str | None = None
    try:
        subtasks: list[dict] = [
            {
                "description": (
                    "每 30 分钟决策心跳：\n"
                    "Phase 0: **每轮必调** papertrade_candidate_refresh() 轮换候选池"
                    "（淘汰最旧 auto 候选 + 补蓝筹底仓/板块/热股/新闻 + 过滤涨停过热），"
                    "再 papertrade_agent_pool_list 看轮换后的池；\n"
                    "Phase 1: papertrade_account_query + papertrade_position_list"
                    " → 看账户+持仓；\n"
                    "Phase 2: papertrade_watchlist_list + papertrade_agent_pool_list"
                    " → 合入候选全集（持仓+群友关注+AI池）；\n"
                    "Phase 3: 拉宏观 news/cloudmap；\n"
                    "Phase 4: 对候选集每只跑 stock_indicators + stock_financials；\n"
                    "Phase 5: 评分 → 决策 buy/sell/hold → 如有交易走撮合→流水→持仓→决策；\n"
                    "关键纪律：候选池每轮必须轮换（不用'<3 才刷'的旧门槛，否则池子填满后"
                    "永远冻结、每轮嚼同一批）；即使有持仓也要评估轮换进来的新标的。"
                    "hold 仅写 decision_insert，不调撮合。\n"
                    "播报纪律（模拟真人）：只有真成交(buy/sell)才发群、且只发极简一行冒泡；"
                    "本轮全 hold / 无成交时最终消息只输出 <<NO_BROADCAST>> 一个标记，框架据此不推群。"
                ),
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
            {
                "description": (
                    "候选池轮换（独立于 decision，每 2 小时跑一次）：\n"
                    "直接调 papertrade_candidate_refresh() 做一次轮换"
                    "（淘汰最旧 auto 候选 + 补蓝筹底仓/板块/热股/新闻 + 过滤涨停过热），"
                    "再 papertrade_agent_pool_list 看轮换后的池;\n"
                    "**本轮仅做轮换，不调任何撮合/流水/持仓/决策工具，不做 buy/sell 判断**。"
                ),
                "agent_profile": "papertrade_pool_refresh_agent",
                "recurring_trigger": "cron:30 10,12,14 * * 1-5",
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
            recurring_trigger=None,
            recurring_until=None,
            root_agent_profile="",
        )
        period_root_id = period_root.id
        await _db.PaperAccountRepo.bind_kanban_period(group_id, bot_id, period_root_id)

        # ─── 关键修复（2026-07-01）：ROOT 本身不设 recurring_trigger。
        #
        # 框架的"根级周期模板"和"子任务级周期模板"是两条独立机制（各自的
        # arm/clone/schedule 代码路径完全不同，见 kanban.py / recurring.py）：
        # 若给 create_kanban_tree 传 recurring_trigger，ROOT 在创建那一刻
        # 起 recurring_status 就已经被写成 'armed'（kanban.py:160），随后
        # execute_ready_tasks 一进来就命中 early-return（kanban_executor.py:
        # 501：``if root.recurring_trigger and root.recurring_status ==
        # "armed": return``）——_maybe_arm_recurring_subtasks 根本不会被调
        # 用，3 个子任务永远不会被 arm 到 APScheduler。旧版本试图用"先
        # kick_root 再 schedule_template"的调用顺序绕过这个早返，但
        # ROOT.recurring_status 在 create_kanban_tree 内部就已经同步写死为
        # 'armed'，kick_root 放在它之前还是之后都不影响这个既成事实——纯
        # no-op（这正是"开盘后 AI 30 分钟心跳从未触发"的根因）。
        #
        # 正确做法：ROOT 保持非周期（recurring_trigger=None，
        # recurring_status 入库为 ''），只让 3 个子任务自带各自的
        # recurring_trigger；kick_root 一次即可触发 execute_ready_tasks →
        # _maybe_arm_recurring_subtasks，把 3 个子任务独立 arm 到
        # APScheduler（``arm_recurring_subtask`` → ``schedule_subtask_
        # template``，与 ROOT 自身状态无关）。此后每次子任务 cron fire 都走
        # ``recurring._fire_subtask_template`` → ``clone_subtask_for_fire``
        # + ``kick_root(root_task_id)``；进程重启由启动期
        # ``restore_armed_subtask_templates`` 统一恢复。
        from gsuid_core.ai_core.planning.kanban_executor import kick_root as _kick_root

        await _kick_root(period_root_id)
    except Exception as e:
        raise RuntimeError(f"Kanban period 树创建失败: {type(e).__name__}: {e}") from e

    return init_root_id, period_root_id


async def _kick_immediate_decision(ev: Event, group_id: str, bot_id: str) -> None:
    """fire-and-forget 立即触发一次 ``papertrade_decision_agent``。

    2026-07-01 修复：之前实现用 ``asyncio.create_task(run_capability_agent(...))``
    fire-and-forget，把结果丢给 GC——LLM 写完交易 + 决策日志后**没人消费**返回
    值，群消息没有播报。修法：内部 await 完 capagent → 算副作用 Δ → 通过
    ``emit_proactive_message`` 把「📈 AI 模拟盘·操盘播报」推群。
    外层仍然 ``asyncio.create_task(_kick_immediate_decision(...))`` 不阻塞 init
    主消息（init 成功文案 + 这条决策播报**两条独立推送**，场景和时机都不同）。

    三条推群策略（与 ``PAPERTRADE_GUIDE.md §一`` 一致）：
        buy  → 推 ✅（"📈 自主决策🟢 买入" + 成交明细 + 决策理由 + 行情快照）
        sell → 推 ✅（"📈 自主决策🔴 卖出" + 成交明细 + realized_pnl + 决策理由）
        hold → **不推**（设计哲学：持仓不动不必打扰群里，只写决策日志供事后查询）

    ``session_id_suffix`` 用 ``init_decision_{group_id}`` 与后续 cron 实例的会话
    串号隔开，避免 session_logger 串话。
    """
    from gsuid_core.logger import logger

    try:
        from gsuid_core.ai_core.proactive.emitter import emit_proactive_message
        from gsuid_core.ai_core.capability_agents.runner import run_capability_agent

        from .proactive import (
            decision_state_delta,
            snapshot_decision_state,
            build_papertrade_proactive_text,
        )
    except Exception as e:
        # 这里失败说明 ai_core / proactive 模块未就绪——但不应阻塞 init 主流程
        logger.exception(f"[SayuStock][PaperTrade] init 立即决策：依赖 import 失败: {e}")
        return

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
        f"完成后简短回报：action / 持仓数量 / 决策 ID。"
    )

    # 1) 拍快照：跑 capagent 前 trades / positions / decisions 各几张表
    baseline: tuple[int, int, int] = (0, 0, 0)
    try:
        baseline = await snapshot_decision_state(group_id, bot_id)
    except Exception as e:
        logger.warning(f"[SayuStock][PaperTrade] snapshot_decision_state 失败（用 0 baseline）: {e}")

    # 2) 跑 capagent（**这里 await**——拿结果用于下面 emit）
    result: str = ""
    try:
        result = await run_capability_agent(
            profile_id="papertrade_decision_agent",
            task=task_prompt,
            ev=ev,
            bot=None,  # bot 由 emit_proactive_message 内部 _resolve_active_bot(ev) 解析
            session_id_suffix=f"init_decision_{group_id}",
        )
    except Exception as e:
        # 跑挂也走 fallback 文本推送，告诉群里"AI 决策失败"，避免静默失败
        logger.exception(f"[SayuStock][PaperTrade] papertrade_decision_agent 执行异常: {e}")
        try:
            await emit_proactive_message(
                event=ev,
                message=f"⚠️ AI 模拟盘心跳异常：{type(e).__name__}: {str(e)[:200]}",
                source="kanban",
                trigger_reason=f"papertrade_init_kick_failed:{group_id}",
                suppress_when_heartbeat_recent=False,
            )
        except Exception as ee:
            logger.exception(f"[SayuStock][PaperTrade] 失败播报本身又失败: {ee}")
        return

    # 3) 算副作用 Δ
    trades_d: int = 0
    positions_d: int = 0
    decisions_d: int = 0
    try:
        trades_d, positions_d, decisions_d = await decision_state_delta(
            baseline, group_id, bot_id
        )
    except Exception as e:
        logger.warning(f"[SayuStock][PaperTrade] decision_state_delta 失败: {e}")

    # 4) 决定是否推送 + 推送什么
    # 推群语义（与 ``PAPERTRADE_GUIDE.md §一·主动消息播报策略`` 一致）：
    #   - trades_d > 0  → 必有真成交，**必推**（用户明确要求"无论买还是卖都
    #     应该在群里发主动消息播报"）；哪怕 LLM 没写决策日志也推，因为群里
    #     已经有真金白银的成交记录
    #   - decisions_d > 0 且 action in ('buy','sell')  → 必推
    #   - decisions_d > 0 且 action == 'hold'          → **不推**（设计哲学：
    #     持仓不动不必打扰群里）
    #   - decisions_d == 0 且 trades_d == 0           → LLM 啥都没干（或工具
    #     全不可达 → 业务失败），不推
    try:
        latest_action: str = ""
        if decisions_d > 0:
            ds = await _db.PaperDecisionRepo.list_recent(group_id, bot_id, limit=1)
            if ds:
                latest_action = ds[0].action or ""

        # 先归一 action 字符串大小写（之前发现过 LLM 写 'BUY' 全大写）
        latest_action_lc: str = latest_action.lower()
        if latest_action_lc == "hold":
            logger.info(
                "[SayuStock][PaperTrade] init 决策 action=hold，按设计不推送（持仓不动）。"
            )
            return

        if trades_d <= 0 and latest_action_lc not in ("buy", "sell"):
            logger.info(
                f"[SayuStock][PaperTrade] init 决策无成交且 action={latest_action or '(空)'!r}，不推送。"
            )
            return

        # ── 真正推送 ──
        text: str = await build_papertrade_proactive_text(
            group_id,
            bot_id,
            variant="auto",
            trades_d=trades_d,
            positions_d=positions_d,
            decisions_d=decisions_d,
            fallback_text=(result or "").strip()[:1000]
            or "（决策 agent 无文本）",
        )
        sent: bool = await emit_proactive_message(
            event=ev,
            message=text,
            source="kanban",
            trigger_reason=(
                f"papertrade_init_kick:{group_id} action={latest_action_lc or '(trade_only)'}"
            ),
            suppress_when_heartbeat_recent=False,  # 关键播报，不被心跳抑制
        )
        if sent:
            logger.info(
                f"[SayuStock][PaperTrade] init 决策播报已推群 action={latest_action_lc or '(trade_only)'} "
                f"trades_Δ={trades_d:+d} positions_Δ={positions_d:+d}"
            )
        else:
            logger.warning(
                f"[SayuStock][PaperTrade] init 决策播报被 C8 网关抑制或 bot 不可用 action={latest_action_lc}"
            )
    except Exception as e:
        # 整段推送链路异常——记日志，绝不抛出（fire-and-forget 任务失败时
        # 用户不应再看到任何报错）
        logger.exception(f"[SayuStock][PaperTrade] init 决策推送链路异常: {e}")


@sv_papertrade.on_fullmatch(
    ("模拟盘初始化",),
    to_ai="""初始化 AI 模拟盘账户（默认 100w 现金，平衡模式）。一次完整流程，包含：

1) SQLModel 建账户（100w 现金，balanced 模式）
2) Kanban init 树（leaf-root / papertrade_setup_agent，单次执行把账户建好）
3) Kanban period 树（ROOT 非周期，仅容器；4 个子任务各自独立挂 APScheduler）：
   - 决策代理（cron:0,30 9-14 * * 1-5，工作日每 30 分钟看盘）
   - 决策代理（cron:35 15 * * 1-5，工作日收盘写当日快照）
   - 复盘代理（cron:0 9 1 * *，每月 1 日 09:00 出月报）
   - 候选池轮换代理（cron:30 10,12,14 * * 1-5，每 2 小时轮换候选池防锚定）
4) kick_root 一次，触发 4 个子任务各自 arm 到 APScheduler

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
        4) register_kanban_task 建 period 树（ROOT 非周期，4 子任务各自带 recurring_trigger：
           decision / snapshot / monthly_report / pool_refresh）
        5) kick_root(period_root_id) 触发 4 个子任务各自 arm 到 APScheduler
        6) bind_kanban_init/period 回填 root_id

    重要：之前实现只跑了步骤 2，没有 Kanban 心跳树，开盘时不会自动决策——
    这是 2026-07-01 群里发现「开盘后 AI 啥都没干」的根因。本版补齐 1+3+4+5+6。
    另注：早期修复版本给 period ROOT 也设了 recurring_trigger 并配合
    schedule_template 挂 APScheduler，这其实是 no-op（ROOT 一旦带
    recurring_trigger，create_kanban_tree 内部就会把它的 recurring_status
    同步写成 'armed'，execute_ready_tasks 随即早返，_maybe_arm_recurring_
    subtasks 永远不会被调用）。现在改为 ROOT 不设 recurring_trigger，
    只让 4 个子任务各自独立 arm，参见 ``_setup_papertrade_kanban_trees``
    内的详细注释。
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

    msg_suffix_open = (
        " + 一次决策心跳（买/卖完成后推群播报；hold 按设计不推）"
        if is_market
        else "（非开盘时段，跳过决策；等下次 cron）"
    )
    msg = (
        f"✅ AI 模拟盘已开户\n"
        f"群号: {ev.group_id}\n"
        f"初始资金: {acc.initial_cash:,.0f}\n"
        f"模式: {acc.mode}\n"
        f"心跳: {acc.frequency_minutes} 分钟\n\n"
        f"Kanban 心跳树：\n"
        f"  init_root   = {init_id[:16] if init_id else '(空)'}…\n"
        f"  period_root = {period_id[:16] if period_id else '(空)'}…\n\n"
        f"已立即触发 init 验证{msg_suffix_open}。"
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

    # (b) 仅在开盘时段踢一次 decision（2026-07-01 修复：用 create_task 不再 await）
    # 原实现 ``await _kick_immediate_decision(...)`` 实际是 fire-and-forget
    # （函数体内部用 create_task 异步跑），但 7-1 修复后 _kick_immediate_decision
    # 改成真正 await capagent + emit，30~60s。把外层 await 改成 create_task 让
    # ``send_init_command`` 立即返回推 init 成功消息；决策播报完成后由 capagent
    # 链路独立推群，与 init 成功消息两条独立、互不阻塞。
    if _is_market_open_now():
        from gsuid_core.logger import logger

        try:
            _decision_task: asyncio.Task = asyncio.create_task(
                _kick_immediate_decision(ev, group_id, bot_id)
            )
            _ = _decision_task  # 显式持有防 GC
            logger.info(
                "[SayuStock][PaperTrade] init-time 决策 kick 已派发（fire-and-forget）。"
            )
        except Exception as e:
            logger.exception(f"[SayuStock][PaperTrade] kick decision 派发失败: {e}")


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
