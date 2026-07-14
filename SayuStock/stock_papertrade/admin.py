"""Master-only 命令（``sv_papertrade_admin`` 注册）。

PM gate + private chat gate 完全交给框架层（``gsuid_core/handler.py`` 的
``_sv_authorized``），本文件内不再做运行时 master 检查。

公开命令：
- ``send_dry_run``: 真 agent 端到端压测（preflight → 造 Kanban init/period
  树 → 真跑 papertrade_setup_agent / papertrade_decision_agent → 真推主动
  消息 → DB 状态总览）。
"""

# pyright/basedpyright 文件级指令 —— 仅作用于本文件。
# - gsuid_core.* 根包在本文件解析路径下不可达
# - @with_session 等装饰器动态隐藏 session 形参，基于 pyright 看不到该变换
# - framework.Event / Event.user_pm 联级未注解
# - ai_core 的 LLM / Kanban / agent / proactive emitter 上游未注解
# - 上述模块返回对象 (AIAgentTask / scheduler / profile) 大量字段为 Any
# 上游这些是已知限制，不是本文件代码错误。
# pyright: reportMissingImports=false, reportCallIssue=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUntypedFunctionDecorator=false, reportUnusedParameter=false, reportUnusedImport=false, reportImplicitStringConcatenation=false, reportAny=false, reportExplicitAny=false

import time as _time
import asyncio
import datetime as _dt
from typing import Any

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from . import db as _db, account_scope as _scope
from .sv import sv_papertrade_admin
from ..utils.database.papertrade_models import (
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperDecision,
    SayuPaperPosition,
)


# ============================================================
# 模拟盘清盘（master-only）
# ============================================================
@sv_papertrade_admin.on_fullmatch(
    ("模拟盘清盘"),
)
async def send_clear_all(bot: Bot, ev: Event):
    """一键清掉 模拟盘在 DB / Kanban / APScheduler 上的所有残留。

    清理范围（按顺序）：
        1. **DB**：``PaperAccountRepo.reset_account(group_id, bot_id)`` 一次性清
           7 张表（account / position / trade / decision / snapshot / watchlist /
           agent_pool）对应分区。
        2. **Kanban init 树**：``hard_delete_task_tree(init_root_id)`` 删任务
           节点 + 日志 + artifact + workspace 文件 + 摘 recurring job。
        3. **Kanban period 树**：同上 + ``include_instances=True`` 把 APScheduler
           周期模板已克隆的所有历史实例树一并清掉。
        4. **APScheduler**：保险——``unschedule_template`` 各调一遍；hard_delete
           内部已经做了，这里是双保险用于旧部署。
        5. **KV 草稿**（可选）：``record:papertrade:*`` / ``stock:decisions`` 等
           ai_core 通用 record KV 不在 DB 表里，业务不查它们，留着也无影响；
           这里刻意不清，省得误删别的群数据。

    PM gate + 群聊 gate 走框架层 ``sv_papertrade_admin`` pm=0 / area="GROUP"。
    """
    if not ev.group_id:
        return await bot.send("⚠️ 清理需要在群聊触发，私聊不支持。")

    # 全局模式下清的是那个钉死的账户，与命令在哪个群发的无关——否则在别的群发清盘
    # 只会清一个空分区，用户还以为清干净了。
    group_id, bot_id = await _scope.resolve_account_key(ev)

    # 顶层 lazy import；失败时给清楚报错
    try:
        from gsuid_core.ai_core.planning.kanban import hard_delete_task_tree  # noqa: E402
        from gsuid_core.ai_core.planning.recurring import (  # noqa: E402
            unschedule_template,
        )
    except Exception as e:
        return await bot.send(
            f"⚠️ ai_core 子系统 import 失败: {type(e).__name__}: {e}\n"
            f"无法执行 Kanban 树清理；请手动到 webconsole Kanban 页面 disable。"
        )

    sections: list[str] = [
        "🧹 **模拟盘 · 一键清盘**\n"
        f"群 {group_id} / bot {bot_id}\n"
        "⚠️ 本操作会清掉该群所有 papertrade 数据 + Kanban 树，不可恢复。"
    ]

    # ─── 1) 取一下账户绑定（用来定位 init / period 树） ───
    t0: float = _time.perf_counter()
    pre_lines: list[str] = []
    acc: SayuPaperAccount | None = None
    init_root_id: str | None = None
    period_root_id: str | None = None
    try:
        acc = await _db.PaperAccountRepo.get(group_id, bot_id)
        if acc is not None:
            init_root_id = acc.kanban_init_root_id
            period_root_id = acc.kanban_period_root_id
            pre_lines.append(f"账户: id={acc.id}, cash={acc.cash:.0f}, mode={acc.mode}")
            pre_lines.append(f"  init_root_id   = {init_root_id or '(未绑定)'}")
            pre_lines.append(f"  period_root_id = {period_root_id or '(未绑定)'}")
        else:
            pre_lines.append("账户: (不存在，无需清表)")
    except Exception as e:
        pre_lines.append(f"❌ 读取账户失败: {type(e).__name__}: {e}")

    pre_dt: float = _time.perf_counter() - t0
    sections.append("─── 1) 账户定位 ───\n" + "\n".join(pre_lines) + f"\n⏱ {pre_dt:.2f}s")

    # ─── 2) 清 DB（7 张表） ───
    t0 = _time.perf_counter()
    db_lines: list[str] = []
    deleted: dict[str, int] = {}
    try:
        deleted = await _db.PaperAccountRepo.reset_account(group_id, bot_id)
        _scope.invalidate_home_cache()  # 账户没了，钉死的键必须重算
        db_lines.append("✅ PaperAccountRepo.reset_account OK")
        if not deleted:
            db_lines.append("   (各表均无记录可清)")
        else:
            for k, v in deleted.items():
                db_lines.append(f"   {k}: -{v} 行")
        total: int = sum(deleted.values())
        db_lines.append(f"   合计删除: {total} 行")
    except Exception as e:
        db_lines.append(f"❌ DB 清理失败: {type(e).__name__}: {e}")
    db_dt: float = _time.perf_counter() - t0
    sections.append("─── 2) DB 清理（7 张表） ───\n" + "\n".join(db_lines) + f"\n⏱ {db_dt:.2f}s")

    # ─── 3) 清 Kanban init 树 + period 树 ───
    t0 = _time.perf_counter()
    kanban_lines: list[str] = []

    async def _drop_one_tree(root_id: str | None, label: str, *, include_instances: bool) -> None:
        if not root_id:
            kanban_lines.append(f"   {label}: (无 root_id，跳过)")
            return
        # 双保险先摘 APScheduler
        unscheduled: bool = unschedule_template(root_id)
        kanban_lines.append(f"   {label}: unschedule_template({root_id[:8]}…) = {unscheduled}")
        try:
            ok, msg, stats = await hard_delete_task_tree(
                root_id,
                delete_files=True,
                include_instances=include_instances,
            )
            if ok:
                tasks_d: int = int(stats.get("tasks_deleted", 0))
                logs_d: int = int(stats.get("logs_deleted", 0))
                arts_d: int = int(stats.get("artifacts_deleted", 0))
                files_d: int = int(stats.get("files_deleted", 0))
                dirs_d: int = int(stats.get("dirs_deleted", 0))
                kanban_lines.append(f"   {label}: ✅ hard_delete OK")
                kanban_lines.append(
                    f"     tasks={tasks_d} logs={logs_d} artifacts={arts_d} files={files_d} dirs={dirs_d}"
                )
            else:
                kanban_lines.append(f"   {label}: ⚠️ hard_delete 返回失败: {msg}")
        except Exception as e:
            kanban_lines.append(f"   {label}: ❌ hard_delete 异常: {type(e).__name__}: {e}")

    try:
        await _drop_one_tree(init_root_id, "init 树  ", include_instances=False)
        await _drop_one_tree(period_root_id, "period 树", include_instances=True)
    except Exception as e:
        kanban_lines.append(f"❌ Kanban 清理异常: {type(e).__name__}: {e}")

    kanban_dt: float = _time.perf_counter() - t0
    sections.append("─── 3) Kanban 树清理 ───\n" + "\n".join(kanban_lines) + f"\n⏱ {kanban_dt:.2f}s")

    # ─── 4) APScheduler job 保险再清一次 ───
    t0 = _time.perf_counter()
    sched_lines: list[str] = []
    try:
        from gsuid_core.aps import scheduler as aps_scheduler  # noqa: E402

        if init_root_id:
            jid_i: str = f"kanban_recurring_{init_root_id}"
            try:
                aps_scheduler.remove_job(jid_i)
                sched_lines.append(f"✅ remove_job({jid_i[:40]}…)")
            except Exception:
                sched_lines.append(f"(no-op) remove_job({jid_i[:40]}…)")
        if period_root_id:
            jid_p: str = f"kanban_recurring_{period_root_id}"
            try:
                aps_scheduler.remove_job(jid_p)
                sched_lines.append(f"✅ remove_job({jid_p[:40]}…)")
            except Exception:
                sched_lines.append(f"(no-op) remove_job({jid_p[:40]}…)")
        sched_lines.append("✅ APScheduler 清理完成（双保险）")
    except Exception as e:
        sched_lines.append(f"❌ APScheduler 清理异常: {type(e).__name__}: {e}")
    sched_dt: float = _time.perf_counter() - t0
    sections.append("─── 4) APScheduler job 清理（双保险）───\n" + "\n".join(sched_lines) + f"\n⏱ {sched_dt:.2f}s")

    # ─── 5) 总结 ───
    summary_lines: list[str] = []
    if acc is not None:
        summary_lines.append(f"已清账户: id={acc.id}, group={acc.group_id}, bot={acc.bot_id}")
    else:
        summary_lines.append("账户: 本就不存在（无需清理）")
    summary_lines.append(f"DB 合计删除: {sum(deleted.values())} 行")
    summary_lines.append("Kanban 树: 见上节")
    summary_lines.append("APScheduler: 见上节")
    summary_lines.append("")
    summary_lines.append("✅ 清理完成。下次发「模拟盘初始化」会重新建一个干净账户。")
    sections.append("─── 5) 总结 ───\n" + "\n".join(summary_lines))

    return await bot.send("\n\n".join(sections))


# ============================================================
# 真 agent 端到端压测（扩写：全流程覆盖）
# ============================================================
@sv_papertrade_admin.on_fullmatch(
    ("模拟盘模拟测试"),
)
async def send_dry_run(bot: Bot, ev: Event):
    """真 agent 端到端压测（master-only，5 段决策流 + auto-cleanup）。

    ⚠️ **会真调 LLM API 并烧 token** + **会真调东财/雪球接口** +
    **会真买真卖**过撮合费率，**会持久化测试数据到 DB 与 Kanban**，
    **末尾自动清理**（同 模拟盘清盘 逻辑）。

    流程（事件流叙事）：每个 decision 段都内嵌一次 emit_proactive_message
    推一条 bot 主动消息，让 master 实时看到每段副作用。

        preflight                  — 校验 agent profile 注册、APScheduler 运行
        Kanban init 树             — leaf-root papertrade_setup_agent
        Kanban period 树           — ROOT 非周期容器，3 子任务各自带 recurring_trigger
        ① setup_agent (LLM)       — 调 papertrade_account_create 真建账户
                                     + 重试 bind_kanban_init/period
        ② 自主决策 (LLM，**不强制**) — LLM 自己判断 buy/sell/hold；
                                     验证三类行为 + 主动消息
        ③ 强制 BUY (LLM，真撮合)   — papertrade_match_order + trade_insert +
                                     position_upsert + decision_insert；
                                     失败则 ❌；最后推主动消息
        ④ 强制 SELL (LLM，真平仓)   — 平仓走完整撮合；如持仓空先 buy 兜底；
                                     推主动消息
        ⑤ 强制 HOLD (LLM，仅写决策) — **禁止调** match_order/trade_insert/
                                     position_upsert；只 papertrade_decision_insert
                                     一条 hold 决策；推主动消息
        ⑥ KB + Web search (LLM)    — search_knowledge + web_search_tool
                                     + get_latest_news + papertrade_account_query；
                                     推主动消息
        ⑦ DB 状态 + delta 校验     — cash 变动 / fee 总和 / realized_pnl 必须非零；
                                     不达标即标 ❌
        ⑧ 写快照（手工模拟 15:05） — db.PaperSnapshotRepo.upsert_for_date（幂等）
        ⑨ auto-cleanup             — reset_account + unschedule_template +
                                     hard_delete_task_tree × 2 + APScheduler
                                     job 移除（双保险）

    每段都校验副作用（row id / DB delta），任何一步 LLM 不走全流程
    就在报告里标红，不允许假阳性。
    """
    if not ev.group_id:
        return await bot.send("⚠️ 压测需要在群聊触发，私聊不支持。")

    group_id: str = str(ev.group_id)
    bot_id: str = ev.bot_id

    # 顶层 lazy import 失败时给清楚报错；不进 preflight
    try:
        from gsuid_core.ai_core.planning.kanban import (  # noqa: E402  -- 懒加载
            create_kanban_tree,
        )
        from gsuid_core.ai_core.proactive.emitter import (  # noqa: E402  -- 懒加载
            emit_proactive_message,
        )
        from gsuid_core.ai_core.capability_agents.runner import (  # noqa: E402  -- 懒加载
            run_capability_agent,
        )
        from gsuid_core.ai_core.planning.kanban_executor import (  # noqa: E402  -- 懒加载
            kick_root,
        )
    except Exception as e:
        return await bot.send(
            f"⚠️ ai_core 子系统 import 失败，框架未就绪: {type(e).__name__}: {e}\n请确认 gsuid_core.ai_core 可正常加载。"
        )

    # 报告缓冲区
    sections: list[str] = []
    sections.append(
        "🧪 **模拟盘 · 真 Agent 端到端压测**\n"
        f"群 {group_id} / bot {bot_id} / master uid={ev.user_id}\n"
        f"⚠️ 本压测会调真实 LLM API 并持久化测试数据到 DB + Kanban"
    )

    # ─── preflight ───
    t0: float = _time.perf_counter()
    preflight_ok: bool = True
    preflight_lines: list[str] = []

    # 1) agent 节点注册检查（AgentNode 统一后走 agent_node.get_node）
    get_profile: Any = None
    try:
        from gsuid_core.ai_core.agent_node import get_node as get_profile  # noqa: E402  -- 懒加载
    except Exception as e:
        preflight_ok = False
        preflight_lines.append(f"❌ 加载 get_node 失败: {e}")

    expected_profiles: list[str] = [
        "papertrade_setup_agent",
        "papertrade_decision_agent",
        "papertrade_reporter_agent",
    ]
    if get_profile is not None:
        missing: list[str] = [p for p in expected_profiles if get_profile(p) is None]
        if missing:
            preflight_ok = False
            for p in missing:
                preflight_lines.append(f"❌ 缺失 agent profile: {p}")
        else:
            for p in expected_profiles:
                preflight_lines.append(f"✅ {p} 已注册")

    # 2) APScheduler 运行检查
    sched: Any = None
    try:
        from gsuid_core.aps import scheduler as aps_scheduler  # noqa: E402  -- 懒加载

        sched = aps_scheduler
    except Exception as e:
        preflight_ok = False
        preflight_lines.append(f"❌ 加载 APScheduler 失败: {e}")
    if sched is not None:
        running: bool = bool(getattr(sched, "running", False))
        preflight_lines.append(f"{'✅' if running else '❌'} APScheduler running={running}")
        if not running:
            preflight_ok = False

    preflight_dt: float = _time.perf_counter() - t0
    preflight_block: str = "─── preflight ───\n" + "\n".join(preflight_lines) + f"\n⏱ {preflight_dt:.2f}s"
    sections.append(preflight_block)

    if not preflight_ok:
        sections.append(
            "⚠️ preflight 失败 — 后续步骤全部跳过。\n请先确保 gsuid_core.ai_core 与 APScheduler 已正常初始化。"
        )
        return await bot.send("\n\n".join(sections))

    # ─── Kanban init 树 ───
    t0 = _time.perf_counter()
    init_lines: list[str] = []
    root_init: Any = None

    async def _build_init_tree() -> None:
        nonlocal root_init
        # leaf-root 模式：subtasks=None + root_agent_profile="papertrade_setup_agent"
        root, _ = await create_kanban_tree(
            goal=f"群{group_id} 模拟盘 init (DRY_RUN {ev.user_id})",
            owner_user_id=str(ev.user_id),
            scope_key=f"papertrade_init_{group_id}_{bot_id}",
            bot_id=bot_id,
            persona_name=None,
            bot_self_id="",
            group_id=group_id,
            user_type="group",
            WS_BOT_ID=None,
            session_id=f"dry_run_init_{group_id}",
            user_pm=0,
            broadcast_targets=[group_id],
            subtasks=None,
            recurring_trigger=None,
            recurring_until=None,
            root_agent_profile="papertrade_setup_agent",
        )
        root_init = root
        await _db.PaperAccountRepo.bind_kanban_init(group_id, bot_id, root.id)
        # fire-and-forget：kick_root 真跑一次（用具名变量避免 _ 与内层 _ 冲突）
        _kick_task: asyncio.Task[None] = asyncio.create_task(kick_root(root.id))

    try:
        await _build_init_tree()
        init_lines.append("✅ create_kanban_tree OK")
        init_lines.append(f"   root_id = {root_init.id}")
        init_lines.append(f"   goal    = 群{group_id} 模拟盘 init (DRY_RUN {ev.user_id})")
        init_lines.append("   profile = papertrade_setup_agent (leaf-root)")
        init_lines.append("   bind    → PaperAccount.kanban_init_root_id")
        init_lines.append("   kick_root 任务已派发（异步执行）")
    except Exception as e:
        init_lines.append(f"❌ Kanban init 树创建失败: {type(e).__name__}: {e}")

    init_dt: float = _time.perf_counter() - t0
    init_block: str = (
        "─── Kanban init 树（papertrade_setup_agent）───\n" + "\n".join(init_lines) + f"\n⏱ {init_dt:.2f}s"
    )
    sections.append(init_block)

    # ─── Kanban period 树 + APScheduler 周期挂载 ───
    t0 = _time.perf_counter()
    period_lines: list[str] = []
    root_period: Any = None

    async def _build_period_tree() -> None:
        nonlocal root_period
        # 多子任务周期模板：3 子任务（period / snapshot / monthly_report）
        subtasks: list[dict[str, Any]] = [
            {
                "description": "查询账户/候选池/持仓 → 决策 → papertrade_*_insert/upsert 写入",
                "agent_profile": "papertrade_decision_agent",
                "recurring_trigger": "cron:0,30 9-11,13-15 * * 1-5",
            },
            {
                "description": "收盘后写当日快照：papertrade_snapshot_write() 幂等 upsert",
                "agent_profile": "papertrade_snapshot_agent",
                "recurring_trigger": "cron:5 15 * * 1-5",
            },
            {
                "description": "月初出复盘报告（月收益 / 胜率 / 最大回撤）",
                "agent_profile": "papertrade_reporter_agent",
                "recurring_trigger": "cron:0 9 1 * *",
            },
        ]
        root, _ = await create_kanban_tree(
            goal=f"群{group_id} 模拟盘 周期托管 (DRY_RUN {ev.user_id})",
            owner_user_id=str(ev.user_id),
            scope_key=f"papertrade_period_{group_id}_{bot_id}",
            bot_id=bot_id,
            persona_name=None,
            bot_self_id="",
            group_id=group_id,
            user_type="group",
            WS_BOT_ID=None,
            session_id=f"dry_run_period_{group_id}",
            user_pm=0,
            broadcast_targets=[group_id],
            subtasks=subtasks,
            recurring_trigger=None,
            recurring_until=None,
            root_agent_profile="",
        )
        root_period = root
        await _db.PaperAccountRepo.bind_kanban_period(group_id, bot_id, root.id)
        # ROOT 不设 recurring_trigger（同 commands._setup_papertrade_kanban_trees
        # 的 2026-07-01 修复：ROOT 带 recurring_trigger 会让 create_kanban_tree
        # 直接把 recurring_status 写成 'armed'，随后 execute_ready_tasks 早返，
        # _maybe_arm_recurring_subtasks 永远不会被调用，3 个子任务永远不会
        # 被 arm）。kick_root 一次即可让 3 个子任务各自独立挂上 APScheduler。
        await kick_root(root.id)

    try:
        await _build_period_tree()
        period_lines.append("✅ create_kanban_tree OK")
        period_lines.append(f"   root_id = {root_period.id}")
        period_lines.append("   subtasks = 3 (period / snapshot / monthly_report)，各自 recurring_trigger")
        period_lines.append("   ROOT 非周期（容器）；kick_root → 3 个子任务各自 arm")
        period_lines.append("   bind    → PaperAccount.kanban_period_root_id")
    except Exception as e:
        period_lines.append(f"❌ Kanban period 树失败: {type(e).__name__}: {e}")

    period_dt: float = _time.perf_counter() - t0
    period_block: str = (
        "─── Kanban period 树（3 子任务 + APScheduler cron）───\n" + "\n".join(period_lines) + f"\n⏱ {period_dt:.2f}s"
    )
    sections.append(period_block)

    # ─── 真跑 papertrade_setup_agent ───
    t0 = _time.perf_counter()
    setup_lines: list[str] = []
    setup_result: str = ""

    async def _run_setup_agent() -> str:
        task: str = (
            f"为群{group_id} 在 bot {bot_id} 上初始化 模拟盘账户（DRY_RUN smoke test by master uid={ev.user_id}）。"
            f"调用 papertrade_account_create 工具建账户："
            f"initial_cash=1000000, mode='balanced', initialized_by='{ev.user_id} (DRY_RUN)'。"
            f"完成后简短报告：账户 id / cash / mode。"
        )
        # grant_write：压测走 ad-hoc capagent，root_task_id 对不上账户的 Kanban 树，
        # 不显式发票会被 _deny_write 拒掉（压测本来就要真写库）。
        with _scope.grant_write():
            return await run_capability_agent(
                profile_id="papertrade_setup_agent",
                task=task,
                ev=ev,
                bot=bot,
                session_id_suffix=f"dry_run_init_{group_id}",
            )

    try:
        setup_result = await _run_setup_agent()
        setup_lines.append("✅ run_capability_agent 返回")
        setup_lines.append("   profile = papertrade_setup_agent")
        preview: str = setup_result[:200]
        if len(setup_result) > 200:
            preview += "…"
        setup_lines.append("   返回（前 200 字符）:")
        setup_lines.append("   ┌─")
        for line in preview.split("\n")[:6]:
            setup_lines.append(f"   │ {line[:60]}")
        setup_lines.append("   └─")
        try:
            acc_now = await _db.PaperAccountRepo.get(group_id, bot_id)
            if acc_now is not None:
                setup_lines.append(f"   副作用: account.id={acc_now.id}, cash={acc_now.cash:.0f}, mode={acc_now.mode}")
                # 关键：build_init_tree 触发时 account 还不存在 → bind_kanban_init 早退；
                # 现在 setup_agent 创建了 account，重试一次 bind 把 root_id 回填
                if root_init is not None and not acc_now.kanban_init_root_id:
                    await _db.PaperAccountRepo.bind_kanban_init(group_id, bot_id, root_init.id)
                    setup_lines.append(f"   bind_kanban_init 重试 OK → {root_init.id[:8]}…")
                if root_period is not None and not acc_now.kanban_period_root_id:
                    await _db.PaperAccountRepo.bind_kanban_period(group_id, bot_id, root_period.id)
                    setup_lines.append(f"   bind_kanban_period 重试 OK → {root_period.id[:8]}…")
            else:
                setup_lines.append("   副作用: account 未被创建（agent 没调工具？）")
        except Exception as ex:
            setup_lines.append(f"   ⚠️ 副作用/补 bind 异常: {type(ex).__name__}: {ex}")
    except Exception as e:
        setup_lines.append(f"❌ papertrade_setup_agent 失败: {type(e).__name__}: {e}")

    setup_dt: float = _time.perf_counter() - t0
    setup_block: str = (
        "─── papertrade_setup_agent（真 LLM 调用）───\n" + "\n".join(setup_lines) + f"\n⏱ {setup_dt:.2f}s"
    )
    sections.append(setup_block)

    # ────────────────────────────────────────────────────────────────────
    # 真实交易播报 builder + 副作用 Δ 计算：复用 ``stock_papertrade.proactive``
    # 共享模块——把"📈 模拟盘操盘播报"模板拼装外置，原 200 多行被替换为
    # 一个 variant 调度 + 一次调用。压测方只走 "force_*" / "kb_web" variant
    # 自爆身份，生产路径永远走 "auto"。
    # ────────────────────────────────────────────────────────────────────
    from .proactive import (  # noqa: E402  -- 懒加载；外部模块，重构期暂留
        Variant as _Variant,
        decision_state_delta,
        snapshot_decision_state,
        build_papertrade_proactive_text,
    )

    # variant 调度：段编号 → 共享模块 variant 字面量
    _STEP_VARIANT: dict[str, _Variant] = {
        "②": "auto",
        "③": "force_buy",
        "④": "force_sell",
        "⑤": "force_hold",
        "⑥": "kb_web",
    }

    # ────────────────────────────────────────────────────────────────────
    # 通用 runner：跑一次 decision_agent + 副作用 Δ + emit 主动消息
    # ────────────────────────────────────────────────────────────────────
    async def _run_round(
        *,
        step_no: str,
        head_label: str,
        task: str,
        session_suffix: str,
        proactive_text: str,
        proactive_reason: str,
    ) -> tuple[str, int, int, int, bool, float]:
        """执行 → 算 Δ → 推主动消息 → 返回 (block_text, trades_Δ, positions_Δ, decisions_Δ, proactive_ok, dt)"""
        rt0: float = _time.perf_counter()
        lines: list[str] = []
        result: str = ""
        trades_d: int = 0
        positions_d: int = 0
        decisions_d: int = 0
        proactive_ok: bool = False
        baseline = await snapshot_decision_state(group_id, bot_id)

        try:
            with _scope.grant_write():
                result = await run_capability_agent(
                    profile_id="papertrade_decision_agent",
                    task=task,
                    ev=ev,
                    bot=bot,
                    session_id_suffix=session_suffix,
                )
            lines.append("✅ run_capability_agent 返回")
            lines.append(f"   profile = papertrade_decision_agent（{head_label}）")
            lines.append("   返回（前 200 字符）:")
            lines.append("   ┌─")
            for ln in result[:200].split("\n")[:6]:
                lines.append(f"   │ {ln[:60]}")
            lines.append("   └─")
        except Exception as e:
            lines.append(f"❌ LLM 调用失败: {type(e).__name__}: {e}")

        try:
            trades_d, positions_d, decisions_d = await decision_state_delta(baseline, group_id, bot_id)
        except Exception as e:
            lines.append(f"❌ 副作用查询失败: {type(e).__name__}: {e}")

        # 每段后 emit_proactive_message（即使 LLM 失败也推，验证链路）
        # 真实播报：从 DB 动态拼"📈 模拟盘操盘播报"，让 master 看到的就是产品形态。
        #   ②/③/④/⑤ 都会拼 — ⑥ (KB/Web 通路) 保留元文本因为没有真实交易可播
        #   fallback_text 传给 build_papertrade_proactive_text：DB 异常 / 账户不存在时退化用
        text: str = proactive_text
        if step_no in _STEP_VARIANT:
            try:
                text = await build_papertrade_proactive_text(
                    group_id,
                    bot_id,
                    variant=_STEP_VARIANT[step_no],
                    trades_d=trades_d,
                    positions_d=positions_d,
                    decisions_d=decisions_d,
                    fallback_text=proactive_text,
                )
            except Exception as e:
                lines.append(f"❌ build_proactive_text 失败: {type(e).__name__}: {e}，回退到 proactive_text")
        try:
            proactive_ok = await emit_proactive_message(
                ev,
                text,
                source="kanban",
                trigger_reason=proactive_reason,
                bot=bot,
                suppress_when_heartbeat_recent=False,
            )
            ok_mark: str = "✅" if proactive_ok else "⚠️"
            # 报告里打印"前 12 行 / 320 字符"避免刷屏
            text_summary: str = text[:320].replace("\n", " ⏎ ")
            lines.append(
                f"   {ok_mark} emit_proactive_message({head_label}) → {proactive_ok}\n"
                f"      text = {text_summary}{'…' if len(text) > 320 else ''}"
            )
        except Exception as e:
            lines.append(f"   ❌ emit_proactive_message 失败: {type(e).__name__}: {e}")
            proactive_ok = False

        rd: float = _time.perf_counter() - rt0
        block: str = (
            f"─── {step_no} {head_label} ───\n"
            + "\n".join(lines)
            + f"\n   副作用 Δ: trades={trades_d:+d}  positions={positions_d:+d}  decisions={decisions_d:+d}"
            + f"\n⏱ {rd:.2f}s"
        )
        return (block, trades_d, positions_d, decisions_d, proactive_ok, rd)

    # ─── ② 自主决策（让 LLM 自己选股 + 自己选 buy/sell/hold） ───
    t0 = _time.perf_counter()
    block2, t2_d, p2_d, d2_d, push2_ok, _ = await _run_round(
        step_no="②",
        head_label="自主决策（LLM 自由选股 + buy/sell/hold）",
        task=(
            f"为群{group_id} 做 1 次【完全自主】模拟盘心跳决策（DRY_RUN by master uid={ev.user_id}）。\n"
            f"\n"
            f"⚠️ 本轮**不预选股票**——决策股由你（LLM）自主挑选：先扫描大盘 → 选行业 → 选个股 → 评估。\n"
            f"\n"
            f"严格按以下顺序调用【真实】papertrade_*_tools + stock_* + ai_core 工具，"
            f"缺哪个工具就在报告里明说，不要 fallback 到 record_put / record_append 之类的 KV。\n"
            f"\n"
            f"━━━ 第一阶段：环境扫描 ━━━\n"
            f"Step 1: papertrade_account_query → 拿 cash / mode / enabled\n"
            f"Step 2: papertrade_position_list → 拿当前持仓（决定 sell 还是 buy）\n"
            f"Step 3: stock_is_trading_day → 看是否交易日（注意 desc 内容）\n"
            f"Step 4: get_market_overview() → 大盘指数 / 涨跌家数 / 北向资金\n"
            f"        关键判断：大盘涨跌幅 > 1%? 涨跌家数比 > 3:1? 北向净流入?\n"
            f"Step 5: get_sector_heatmap(top_n=5) → 找出当日最强 3 个板块 + 最弱 3 个板块\n"
            f"        从最强板块的 top_stocks 里挑 1~3 只作为候选\n"
            f"Step 6: get_latest_news(limit=5) → 财经新闻，特别关注最强板块的催化消息\n"
            f"\n"
            f"━━━ 第二阶段：个股深度分析（候选 1~3 只）━━━\n"
            f"Step 7: 对每只候选股：\n"
            f"        7.1 search_stock(query=<股票名>) → 拿 secid\n"
            f"        7.2 stock_indicators(stock_code, periods=120) → 日 K 全套技术指标\n"
            f"            （MA / MACD / RSI / BOLL20 / BOLL60 / CCI14 / BBI / 支撑压力）\n"
            f"        7.3 stock_indicators(stock_code, periods=60, kline_period=60) → 60 分钟 K\n"
            f"        7.4 stock_indicators(stock_code, periods=80, kline_period=15) → 15 分钟 K\n"
            f"        7.5 stock_financials(stock_code, report='main') → 财报 + 行业类型\n"
            f"            重点：roe/revenue_yoy/profit_yoy/net_margin/debt_ratio；"
            f"银行股另看 net_interest_margin（无毛利率）\n"
            f"        7.6 跨周期共振判断：\n"
            f"            - 日 K MACD 金叉 + 60m K MACD 金叉 + 15m K MACD 金叉 = 强买信号\n"
            f"            - 日 K BOLL20 带宽 / BOLL60 带宽 > 1.3 = 短期波动放大\n"
            f"            - CCI14 > 100 超买 / < -100 超卖\n"
            f"\n"
            f"━━━ 第三阶段：行业横向对比（如果你选了某板块）━━━\n"
            f"Step 8: 选 1~2 只同板块龙头 / 竞品跑相同 Step 7 流程\n"
            f"        8.1 横向比：roe/net_margin、毛利率、MACD 趋势、BOLL 位置\n"
            f"        8.2 找出板块内最强个股 vs 你候选的个股\n"
            f"\n"
            f"━━━ 第四阶段：综合决策 ━━━\n"
            f"Step 9: 综合 Step 1~8 自主判断 action（buy / sell / hold）；\n"
            f"        **reason 必须引用**：\n"
            f"        - 大盘环境（来自 Step 4）\n"
            f"        - 板块选择理由（来自 Step 5）\n"
            f"        - 跨周期共振 / BOLL 敞口 / CCI / BBI 结论（来自 Step 7.6）\n"
            f"        - 行业横向对比结论（来自 Step 8，若做了）\n"
            f"Step 10: papertrade_decision_insert(action=<你的判断>, reason='...', indicators='<JSON 摘要>')\n"
            f"        若 action='buy'：先 papertrade_match_order(buy) → papertrade_trade_insert(buy) → papertrade_position_upsert(qty>0)，decision_insert 的 trade_id 关联\n"  # noqa: E501  压测 prompt：完整 step 描述，不拆行避免破坏 LLM 指令语义
            "        若 action='sell'：先 papertrade_match_order(sell) → papertrade_trade_insert(sell, realized_pnl) → papertrade_position_upsert(qty=0)，decision_insert 的 trade_id 关联\n"  # noqa: E501  同上
            "        若 action='hold'：仅 decision_insert，不调 match_order/trade_insert/position_upsert\n"
            "\n"
            "⚠️ 不要为了过测试伪造 row id；若 papertrade_* 工具不可达，"
            "就在报告里列工具缺口，仅 papertrade_decision_insert 写一条对应 action 的决策日志。"
        ),
        session_suffix=f"dry_run_a_free_{group_id}",
        proactive_text="🧪 [DRY_RUN ②] 模拟盘 自主决策完成（详见主报告）",
        proactive_reason=f"模拟盘压测 · 自主决策段 · master uid={ev.user_id}",
    )
    sections.append(block2)
    t0 = _time.perf_counter()

    # ─── ③ 强制 BUY（必须真买 + 主动消息） ───
    block3, t3_d, p3_d, d3_d, push3_ok, _ = await _run_round(
        step_no="③",
        head_label="强制 BUY（真撮合 + 主动消息推送）",
        task=(
            f"为群{group_id} 做【强制 BUY】模拟盘心跳（DRY_RUN by master uid={ev.user_id}）。\n"
            f"\n"
            f"⚠️ 【DRY_RUN 强制 BUY】 — 不允许 action='hold'。\n"
            f"决策股已固定为 000001 平安银行；价格取 stock_indicators 返回的当日 close；"
            f"qty 取整手（≥100 股，占现金 ≤10%）。\n"
            f"\n"
            f"Step 1: papertrade_account_query → 拿 cash\n"
            f"Step 2: search_stock(query='平安银行') → 拿 secid + name\n"
            f"Step 3: stock_indicators(stock_code='000001') → 拿当日 close\n"
            f"Step 4: papertrade_match_order(side='buy', stock_code='000001', qty, price, cash_available=acc.cash)\n"
            f"        → 拿 fee_total / actual_qty / amount\n"
            f"Step 5: papertrade_trade_insert(\n"
            f"          stock_code='000001', stock_name='平安银行', secid=<Step2 拿到的>,\n"
            f"          side='buy', price=<price>, qty=<actual_qty from match>,\n"
            f"          amount=<amount from match>, fee=<fee_total from match>,\n"
            f"          realized_pnl=0.0, reason='DRY_RUN ③ 强制 buy', snapshot='...',\n"
            f"          decision_id=0, mode='balanced'\n"
            f"        ) → 拿 trade_id\n"
            f"Step 6: papertrade_position_upsert(\n"
            f"          stock_code='000001', stock_name='平安银行', secid=<Step2 拿到的>,\n"
            f"          qty=<actual_qty>, avg_cost=<price>\n"
            f"        ) → 拿 pos_id\n"
            f"Step 7: papertrade_decision_insert(\n"
            f"          action='buy', stock_code='000001', stock_name='平安银行',\n"
            f"          score=0.5, reason='DRY_RUN ③ 强制 buy', indicators='...',\n"
            f"          trade_id=<trade_id from Step5>\n"
            f"        ) → 拿 decision_id\n"
            f"\n"
            f"最终报告格式：\n"
            f"  ## ③ 强制 buy 摘要\n"
            f"  - LLM 决策: BUY 000001 qty=X price=Y\n"
            f"  - trade_id / pos_id / decision_id\n"
            f"\n"
            f"若 papertrade_* 工具不可达（schema 里查不到），如实在报告里列工具缺口，不要 fabricate row id。"
        ),
        session_suffix=f"dry_run_b_forcebuy_{group_id}",
        proactive_text="🧪 [DRY_RUN ③] 强制 BUY 已成交（详见主报告）",
        proactive_reason=f"模拟盘压测 · 强制 BUY 段 · master uid={ev.user_id}",
    )
    sections.append(block3)
    t0 = _time.perf_counter()

    # ─── ④ 强制 SELL（必须真卖 + 主动消息） ───
    block4, t4_d, p4_d, d4_d, push4_ok, _ = await _run_round(
        step_no="④",
        head_label="强制 SELL（真撮合平仓 + 主动消息推送）",
        task=(
            f"为群{group_id} 做【强制 SELL】模拟盘心跳（DRY_RUN by master uid={ev.user_id}）。\n"
            f"\n"
            f"⚠️ 【DRY_RUN 强制 SELL】 — 不允许 action='hold'。\n"
            f'如持仓列表为空（③ 段没真建上），先做"建仓兜底"再 sell：\n'
            f"  papertrade_match_order(buy, 100股, price=close) → trade_insert(buy) → position_upsert(100股)\n"
            f"然后立刻 sell 整仓。\n"
            f"\n"
            f"Step 1: papertrade_account_query → 拿 cash\n"
            f'Step 2: papertrade_position_list → 拿持仓；若空走上面"建仓兜底"\n'
            f"Step 3: search_stock(query='平安银行') → 拿 secid\n"
            f"Step 4: stock_indicators(stock_code='000001') → 拿 close 作为 sell_price\n"
            f"Step 5: papertrade_match_order(side='sell', stock_code='000001', qty=持仓qty, price, position_qty=持仓qty)\n"  # noqa: E501  压测 prompt：完整 sell 撮合参数列表
            "        → 拿 fee_total / actual_qty / amount\n"
            "Step 6: papertrade_trade_insert(\n"
            "          stock_code='000001', stock_name='平安银行', secid=<Step3 拿到的>,\n"
            "          side='sell', price=<price>, qty=<actual_qty>,\n"
            "          amount=<amount from match>, fee=<fee_total from match>,\n"
            "          realized_pnl=(price - Step2 持仓 avg_cost) * actual_qty - fee_total,\n"
            "          reason='DRY_RUN ④ 强制 sell 平仓', snapshot='...',\n"
            "          decision_id=0, mode='balanced'\n"
            "        ) → 拿 trade_id\n"
            "Step 7: papertrade_position_upsert(stock_code='000001', stock_name='平安银行', secid, qty=0, avg_cost=0)\n"  # noqa: E501  压测 prompt：完整平仓参数
            "        → 持仓清 0（qty=0 自动 DELETE）→ pos_id 可能 0\n"
            "Step 8: papertrade_decision_insert(action='sell', stock_code='000001', stock_name='平安银行',\n"
            "          score=0.5, reason='DRY_RUN ④ 强制 sell', indicators='...',\n"
            "          trade_id=<trade_id from Step6>)\n"
            "\n"
            "最终报告格式：\n"
            "  ## ④ 强制 sell 摘要\n"
            "  - 持仓现状: (qty>0, stock_code, qty, avg_cost)\n"
            "  - LLM 决策: SELL 000001 qty=X price=Y avg_cost=Z\n"
            "  - match fee_total / trade_id / pos_id / decision_id\n"
            "  - realized_pnl = (price - avg_cost) * qty - fee_total\n"
            "\n"
            "若 papertrade_* 工具不可达，如实在报告里列工具缺口，不要 fabricate row id。"
        ),
        session_suffix=f"dry_run_c_forcesell_{group_id}",
        proactive_text="🧪 [DRY_RUN ④] 强制 SELL 已平仓（详见主报告）",
        proactive_reason=f"模拟盘压测 · 强制 SELL 段 · master uid={ev.user_id}",
    )
    sections.append(block4)
    t0 = _time.perf_counter()

    # ─── ⑤ 强制 HOLD（验证 hold 行为：仅 decision_insert，无交易） ───
    block5, t5_d, p5_d, d5_d, push5_ok, _ = await _run_round(
        step_no="⑤",
        head_label="强制 HOLD（仅写决策日志，不交易）",
        task=(
            f"为群{group_id} 做【强制 HOLD】模拟盘心跳（DRY_RUN by master uid={ev.user_id}）。\n"
            f"\n"
            f"⚠️ 【DRY_RUN 强制 HOLD】 — 必须 action='hold'。\n"
            f"**禁止调用 papertrade_match_order / papertrade_trade_insert / papertrade_position_upsert**。\n"
            f"本段的全部副作用：**仅 1 条 papertrade_decision_insert(action='hold')**。\n"
            f"\n"
            f"Step 1: papertrade_account_query → 拿 cash\n"
            f"Step 2: papertrade_position_list → 拿持仓（应为空，因为 ④ 段已清仓）\n"
            f"Step 3: stock_is_trading_day → 看是否交易日（注意 desc 内容）\n"
            f"Step 4: 决策强制 HOLD：\n"
            f"        stock_code=None（hold 不指定股票）\n"
            f"        reason='DRY_RUN ⑤ 强制 hold：信号弱 / 持仓已清 / 非交易日'\n"
            f"        score=0.0（hold 是中性信号）\n"
            f"Step 5: papertrade_decision_insert(\n"
            f"          action='hold', stock_code=None, stock_name=None,\n"
            f"          score=0.0, reason='...', indicators='...', trade_id=None\n"
            f"        ) → 拿 decision_id\n"
            f"\n"
            f"最终报告格式：\n"
            f"  ## ⑤ 强制 hold 摘要\n"
            f"  - LLM 决策: HOLD\n"
            f"  - decision_id\n"
            f"  - 已确认未调 papertrade_match_order / trade_insert / position_upsert\n"
            f"\n"
            f"若 papertrade_decision_insert 不可达，在报告里列工具缺口，不要 fabricate row id。"
        ),
        session_suffix=f"dry_run_d_forcehold_{group_id}",
        proactive_text="🧪 [DRY_RUN ⑤] 强制 HOLD 完成（仅决策无交易，详见主报告）",
        proactive_reason=f"模拟盘压测 · 强制 HOLD 段 · master uid={ev.user_id}",
    )
    sections.append(block5)
    t0 = _time.perf_counter()

    # ─── ⑥ KB + Web search 通路验证（不依赖决策） ───
    block6, _, _, _, push6_ok, _ = await _run_round(
        step_no="⑥",
        head_label="KB + Web search 通路验证（不依赖决策）",
        task=(
            f"为群{group_id} 验证 ai_core 知识库 + 网络搜索 + 新闻三条通路（DRY_RUN by master uid={ev.user_id}）。\n"
            f"\n"
            f"⚠️ 严格按以下顺序调用【真实】ai_core 工具，缺工具就明说，不要 KV 替代。\n"
            f"\n"
            f"Step 1: search_knowledge(query='平安银行 ROE 财务') → 应命中 sayustock_papertrade_guide 或 sayustock_overview KB\n"  # noqa: E501  压测 prompt：KB 命中预期
            "Step 2: web_search_tool(query='平安银行 2025 业绩快报') → 拿 web 搜索摘要\n"
            "Step 3: get_latest_news(limit=2) → 顺手再拉一次财经新闻验证 ai_tools 通路\n"
            "Step 4: papertrade_account_query → 顺手确认账户（应已建账户 + 持平或微变）\n"
            "\n"
            "最终报告：每个工具返回的关键摘要（前 100~200 字符）+ 是否成功拿到数据（不是空 / 不是错误）"
        ),
        session_suffix=f"dry_run_e_kbweb_{group_id}",
        proactive_text="🧪 [DRY_RUN ⑥] KB + Web search 通路验证完成（详见主报告）",
        proactive_reason=f"模拟盘压测 · KB/Web 通路段 · master uid={ev.user_id}",
    )
    sections.append(block6)
    # 注：此前的 push_dt 在 ⑤ 后赋过值但未使用，单独保留是为对齐各段耗时统计节奏；
    #     这里不再赋冗余变量，直接进入 DB 状态总览。
    _push_dt_unused: float = _time.perf_counter() - t0  # noqa: F841  保留 perf_counter 节奏

    # ─── DB 状态总览 ───
    t0 = _time.perf_counter()
    db_lines: list[str] = []

    try:
        acc = await _db.PaperAccountRepo.get(group_id, bot_id)
        if acc is not None:
            db_lines.append(f"account: id={acc.id}, cash={acc.cash:.0f}, mode={acc.mode}, enabled={acc.enabled}")
            db_lines.append(f"  kanban_init_root_id   = {acc.kanban_init_root_id or '(未绑定)'}")
            db_lines.append(f"  kanban_period_root_id = {acc.kanban_period_root_id or '(未绑定)'}")
            db_lines.append(
                f"  last_decided_at = {acc.last_decided_at.isoformat() if acc.last_decided_at else '(未决策)'}"
            )
        else:
            db_lines.append("account: (不存在)")

        positions = await _db.PaperPositionRepo.list_by_account(group_id, bot_id)
        db_lines.append(f"\npositions (持仓, qty>0, {len(positions)} 只):")
        for p in positions[:5]:
            db_lines.append(f"  - {p.stock_code} ({p.stock_name}) ×{p.qty}@{p.avg_cost:.2f} pos_id={p.id}")

        trades = await _db.PaperTradeRepo.list_by_account(group_id, bot_id, limit=5)
        db_lines.append("\ntrades (最近 5 笔):")
        for t in trades[:5]:
            side = "买" if t.side == "buy" else "卖"
            db_lines.append(
                f"  - {t.executed_at.isoformat() if t.executed_at else '?'} "
                f"{side} {t.stock_code} {t.qty}@{t.price:.2f} fee={t.fee:.2f} trade_id={t.id}"
            )

        decisions = await _db.PaperDecisionRepo.list_recent(group_id, bot_id, limit=3)
        db_lines.append("\ndecisions (最近 3 条):")
        for d in decisions[:3]:
            db_lines.append(
                f"  - {d.created_at.isoformat() if d.created_at else '?'} "
                f"action={d.action} stock={d.stock_code or '(none)'} decision_id={d.id}"
            )

        snapshots = await _db.PaperSnapshotRepo.list_range(group_id, bot_id)
        db_lines.append(f"\nsnapshots ({len(snapshots)} 条):")
        for s in snapshots[-3:]:
            db_lines.append(
                f"  - {s.trade_date.isoformat()} equity={s.total_equity:.0f} "
                f"pnl={s.total_pnl:+.0f} ({s.total_pnl_pct:+.2f}%) snap_id={s.id}"
            )

        agg: dict[str, float] = await _db.PaperTradeRepo.aggregate_pnl(group_id, bot_id)
        db_lines.append(
            f"\naggregate_pnl: amount={agg['total_amount']:.0f}, "
            f"fee={agg['total_fee']:.2f}, trades={int(agg['trade_count'])}"
        )
    except Exception as e:
        db_lines.append(f"❌ DB 状态查询失败: {type(e).__name__}: {e}")

    db_dt: float = _time.perf_counter() - t0
    sections.append("─── ⑥ DB 状态总览 ───\n" + "\n".join(db_lines) + f"\n⏱ {db_dt:.2f}s")

    # ─── ⑥.5 delta 校验（cash 变动 / fee 总和 / realized_pnl 必须非 0） ───
    t0 = _time.perf_counter()
    delta_lines: list[str] = []
    try:
        acc_now: SayuPaperAccount | None = await _db.PaperAccountRepo.get(group_id, bot_id)
        initial_cash: float = acc_now.initial_cash if acc_now else 1_000_000.0
        cash_now: float = acc_now.cash if acc_now else 0.0
        positions_now: list[SayuPaperPosition] = await _db.PaperPositionRepo.list_by_account(group_id, bot_id)
        trades_now: list[SayuPaperTrade] = await _db.PaperTradeRepo.list_by_account(group_id, bot_id, limit=50)
        decisions_now: list[SayuPaperDecision] = await _db.PaperDecisionRepo.list_recent(group_id, bot_id, limit=50)

        total_fee: float = sum(t.fee for t in trades_now)
        buy_count: int = sum(1 for t in trades_now if t.side == "buy")
        sell_count: int = sum(1 for t in trades_now if t.side == "sell")
        realized_pnl: float = sum(t.realized_pnl for t in trades_now)
        buy_amount: float = sum(t.amount for t in trades_now if t.side == "buy")
        sell_amount: float = sum(t.amount for t in trades_now if t.side == "sell")
        buy_fee: float = sum(t.fee for t in trades_now if t.side == "buy")
        sell_fee: float = sum(t.fee for t in trades_now if t.side == "sell")

        # papertrade_trade_insert 现在自动维护 cash + principal：
        #   buy  : cash -= (amount + fee)
        #   sell : cash += (amount - fee + realized_pnl); principal += realized_pnl
        # 因此 expected_cash = initial + Σbuy(-amount-fee) + Σsell(amount-fee+realized_pnl)
        #               = initial - buy_amount - buy_fee + sell_amount - sell_fee + realized_pnl
        #               = initial + (sell_amount - buy_amount) - total_fee + realized_pnl
        expected_cash: float = initial_cash - buy_amount - buy_fee + sell_amount - sell_fee + realized_pnl
        diff: float = round(cash_now - expected_cash, 4)

        # principal 也校验：sell 应累计 realized_pnl，本轮预期
        #   principal = initial_cash + realized_pnl
        principal_now: float = acc_now.principal if acc_now else 0.0
        expected_principal: float = initial_cash + realized_pnl
        principal_diff: float = round(principal_now - expected_principal, 4)

        delta_lines.append(f"   initial_cash    = {initial_cash:,.0f}")
        delta_lines.append(f"   cash_now        = {cash_now:,.0f}")
        delta_lines.append(f"   buy_amount      = {buy_amount:,.0f}  ({buy_count} 笔, fee={buy_fee:.2f})")
        delta_lines.append(f"   sell_amount     = {sell_amount:,.0f}  ({sell_count} 笔, fee={sell_fee:.2f})")
        delta_lines.append(f"   total_fee       = {total_fee:.2f}")
        delta_lines.append(f"   realized_pnl    = {realized_pnl:+.2f}")
        delta_lines.append(f"   expected_cash   = {expected_cash:,.2f}")
        delta_lines.append(f"   cash diff       = {diff:+.4f}")
        delta_lines.append("")
        delta_lines.append(f"   principal_now   = {principal_now:,.2f}")
        delta_lines.append(f"   expected_principal = {expected_principal:,.2f} (= initial + realized_pnl)")
        delta_lines.append(f"   principal diff  = {principal_diff:+.4f}")
        delta_lines.append("")
        delta_lines.append(f"   positions (qty>0): {len(positions_now)} 只 (round 2 应收 0)")
        delta_lines.append(f"   trades 总数: {len(trades_now)} 笔")
        delta_lines.append(f"   decisions 总数: {len(decisions_now)} 条")
        delta_lines.append("")

        ok_list: list[str] = []
        if buy_count >= 1:
            ok_list.append(f"✅ 真买入 {buy_count} 笔")
        else:
            ok_list.append("❌ 没买入")
        if sell_count >= 1:
            ok_list.append(f"✅ 真卖出 {sell_count} 笔")
        else:
            ok_list.append("❌ 没卖出")
        if abs(diff) < 1.0:
            ok_list.append(f"✅ cash 自洽 (diff {diff:+.4f})")
        else:
            ok_list.append(f"⚠️ cash 差额 {diff:+.4f}（应≈0；不为 0 则 cash 没被 trade_insert 维护）")
        if abs(principal_diff) < 1.0:
            ok_list.append(f"✅ principal 自洽 (diff {principal_diff:+.4f})")
        else:
            ok_list.append(f"⚠️ principal 差额 {principal_diff:+.4f}（sell 路径应 += realized_pnl）")
        if realized_pnl != 0.0:
            ok_list.append(f"✅ realized_pnl 已记账 ({realized_pnl:+.2f})")
        else:
            ok_list.append("❌ realized_pnl 全 0（sell 没回写 pnl？）")
        if len(positions_now) == 0:
            ok_list.append("✅ 持仓已清零（round 2 平仓成功）")
        else:
            ok_list.append(f"⚠️ 持仓未清零：还有 {len(positions_now)} 只")
        delta_lines.append("   校验结论：")
        for ln in ok_list:
            delta_lines.append(f"      {ln}")
    except Exception as e:
        delta_lines.append(f"❌ delta 校验失败: {type(e).__name__}: {e}")

    delta_dt: float = _time.perf_counter() - t0
    sections.append("─── ⑥.5 delta 校验 ───\n" + "\n".join(delta_lines) + f"\n⏱ {delta_dt:.2f}s")

    # ─── ⑦ 写快照（手工模拟 15:05 收盘后写，幂等 upsert） ───
    t0 = _time.perf_counter()
    snap_lines: list[str] = []
    try:
        from .db import PaperSnapshotRepo  # noqa: E402  -- 本仓 import

        acc_snap: SayuPaperAccount | None = await _db.PaperAccountRepo.get(group_id, bot_id)
        if acc_snap is None:
            snap_lines.append("⚠️ account 不存在，跳过写快照")
        else:
            positions_snap: list[SayuPaperPosition] = await _db.PaperPositionRepo.list_by_account(group_id, bot_id)
            today: _dt.date = _dt.date.today()
            # 持仓市值用 avg_cost 近似（压测不实时拉行情，避免又烧一次东财接口）
            position_value: float = float(sum(p.qty * p.avg_cost for p in positions_snap))
            total_equity: float = acc_snap.cash + position_value
            day_pnl: float = float(realized_pnl) if "realized_pnl" in dir() else 0.0
            total_pnl: float = total_equity - acc_snap.initial_cash
            total_pnl_pct: float = total_pnl / acc_snap.initial_cash * 100 if acc_snap.initial_cash else 0.0
            snap = await PaperSnapshotRepo.upsert_for_date(
                group_id,
                bot_id,
                today,
                cash=acc_snap.cash,
                position_value=position_value,
                total_equity=total_equity,
                day_pnl=day_pnl,
                day_pnl_pct=0.0,
                total_pnl=total_pnl,
                total_pnl_pct=total_pnl_pct,
            )
            snap_lines.append("✅ PaperSnapshotRepo.upsert_for_date OK")
            snap_lines.append(f"   trade_date = {today.isoformat()}")
            snap_lines.append(f"   cash       = {acc_snap.cash:,.2f}")
            snap_lines.append(f"   position   = {position_value:,.2f}")
            snap_lines.append(f"   equity     = {total_equity:,.2f}")
            snap_lines.append(f"   total_pnl  = {total_pnl:+,.2f} ({total_pnl_pct:+.4f}%)")
            snap_lines.append(f"   snap_id    = {snap.id}")
    except Exception as e:
        snap_lines.append(f"❌ 写快照失败: {type(e).__name__}: {e}")

    snap_dt: float = _time.perf_counter() - t0
    sections.append("─── ⑦ 写快照（手工模拟 15:35 收盘后）───\n" + "\n".join(snap_lines) + f"\n⏱ {snap_dt:.2f}s")

    # ─── ⑧ auto-cleanup（压测结束自动清掉所有副作用） ───
    t0 = _time.perf_counter()
    cleanup_lines: list[str] = []
    cleanup_lines.append("🧹 开始 auto-cleanup（与 模拟盘清盘 同逻辑）")
    cleanup_lines.append("")

    # 8.1 DB（7 张表）
    try:
        deleted: dict[str, int] = await _db.PaperAccountRepo.reset_account(group_id, bot_id)
        cleanup_lines.append("─── 8.1 DB（7 张表）───")
        for k, v in deleted.items():
            cleanup_lines.append(f"   {k}: -{v} 行")
        cleanup_lines.append(f"   合计删除: {sum(deleted.values())} 行")
    except Exception as e:
        cleanup_lines.append(f"❌ 8.1 DB 清理失败: {type(e).__name__}: {e}")

    # 8.2 Kanban 树 + 周期 job（hard_delete 内部已经摘 job；这里双保险 unschedule）
    try:
        from gsuid_core.ai_core.planning.kanban import hard_delete_task_tree  # noqa: E402
        from gsuid_core.ai_core.planning.recurring import unschedule_template  # noqa: E402

        cleanup_lines.append("")
        cleanup_lines.append("─── 8.2 Kanban 树 + APScheduler job ───")

        async def _drop_one(root_id: str | None, label: str, *, include_inst: bool) -> None:
            if not root_id:
                cleanup_lines.append(f"   {label}: (无 root_id，跳过)")
                return
            try:
                unschedule_template(root_id)
                cleanup_lines.append(f"   {label}: unschedule_template({root_id[:8]}…)")
            except Exception as ex:
                cleanup_lines.append(f"   {label}: unschedule 异常: {ex}")
            try:
                ok, msg, stats = await hard_delete_task_tree(
                    root_id,
                    delete_files=True,
                    include_instances=include_inst,
                )
                if ok:
                    cleanup_lines.append(
                        f"   {label}: ✅ hard_delete tasks={stats.get('tasks_deleted', 0)} "
                        f"logs={stats.get('logs_deleted', 0)} arts={stats.get('artifacts_deleted', 0)} "
                        f"files={stats.get('files_deleted', 0)} dirs={stats.get('dirs_deleted', 0)}"
                    )
                else:
                    cleanup_lines.append(f"   {label}: ⚠️ hard_delete 失败: {msg}")
            except Exception as ex:
                cleanup_lines.append(f"   {label}: ❌ hard_delete 异常: {type(ex).__name__}: {ex}")

        await _drop_one(root_init.id if root_init else None, "init 树  ", include_inst=False)
        await _drop_one(root_period.id if root_period else None, "period 树", include_inst=True)
    except Exception as e:
        cleanup_lines.append(f"❌ 8.2 Kanban import 失败: {type(e).__name__}: {e}")

    # 8.3 APScheduler job 双保险
    try:
        from gsuid_core.aps import scheduler as aps_scheduler  # noqa: E402

        cleanup_lines.append("")
        cleanup_lines.append("─── 8.3 APScheduler job 双保险 ───")
        for rid in [root_init.id if root_init else None, root_period.id if root_period else None]:
            if not rid:
                continue
            jid: str = f"kanban_recurring_{rid}"
            try:
                aps_scheduler.remove_job(jid)
                cleanup_lines.append(f"   ✅ remove_job({jid[:40]}…)")
            except Exception:
                cleanup_lines.append(f"   (no-op) remove_job({jid[:40]}…)")
    except Exception as e:
        cleanup_lines.append(f"❌ 8.3 APScheduler 清理异常: {type(e).__name__}: {e}")

    cleanup_lines.append("")
    cleanup_lines.append("✅ auto-cleanup 完成。下次发「模拟盘初始化」重新建账。")
    cleanup_dt: float = _time.perf_counter() - t0
    sections.append("─── ⑧ auto-cleanup ───\n" + "\n".join(cleanup_lines) + f"\n⏱ {cleanup_dt:.2f}s")

    return await bot.send("\n\n".join(sections))
