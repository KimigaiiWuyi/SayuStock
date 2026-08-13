"""用户命令（``sv_papertrade`` 注册，pm=3）。

**模拟盘是命名的盘，不是群的财产**。凡是针对某个盘的命令都必须带盘名，
无参只回用法，不会落到默认盘。播报订阅由「模拟盘推送」系列命令独立管理。

命令一览：

| 命令 | 权限 | 说明 |
|---|---|---|
| ``模拟盘初始化 [初始资金]`` | 管理员 | 建默认盘（等价于 ``模拟盘创建 默认模拟盘``） |
| ``模拟盘创建 <盘名> [策略] [初始资金]`` | 管理员 | 新建一个命名盘 |
| ``模拟盘列表`` | 所有人 | 列出全部盘 |
| ``模拟盘改名 <旧名> <新名>`` | 管理员 | 改盘名 |
| ``模拟盘删除 <盘名>`` | 管理员 | 删盘（连带清账本与播报订阅） |
| ``模拟盘策略列表`` | 所有人 | 可用策略与参数 |
| ``模拟盘策略 <盘名> <策略id>`` | 管理员 | 切换策略 |
| ``模拟盘推送添加/删除 <盘名>`` | 管理员 | 本群订阅/退订该盘的成交播报 |
| ``模拟盘推送列表`` | 所有人 | 本群订阅了哪些盘 |
| ``模拟盘查看/持仓/自选/收益/记录 <盘名>`` | 所有人 | 查询 |
| ``模拟盘排行`` | 管理员 | 跨盘收益排行 |
| ``模拟盘查询 <盘名>`` | 管理员 | 单盘明细 |
"""

import asyncio
import datetime as _dt

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from . import db as _db, strategies as _strat, cross_group as _cross, account_scope as _scope
from .sv import sv_papertrade
from .render import draw_leaderboard, draw_account_view, build_holdings_snapshot_image
from .permissions import check_admin
from .trading_calendar import is_trading_time, is_a_share_trading_day
from ..utils.database.papertrade_models import DEFAULT_ACCOUNT_NAME, SayuPaperAccount

_DECISION_KICK_SEM = asyncio.Semaphore(2)


# ============================================================
# 0) 公共小工具
# ============================================================
def _is_market_open_now() -> bool:
    """是否处于 A 股开盘时段（交易日 + 交易时段）。"""
    return is_a_share_trading_day() and is_trading_time()


async def _resolve_or_complain(bot: Bot, raw_name: str) -> SayuPaperAccount | None:
    """按盘名解析账户；失败时直接把提示发出去并返回 None。"""
    acc = await _scope.resolve_account(name=raw_name, fallback_default=False)
    if acc is None:
        await bot.send(_scope.not_opened_message(name=raw_name))
        return None
    return acc


async def _require_named_account(bot: Bot, raw_name: str, usage: str) -> SayuPaperAccount | None:
    """命令层：没写盘名只回用法，不落到默认盘。"""
    name = _scope.normalize_account_name(raw_name)
    if not name:
        await bot.send(usage)
        return None
    return await _resolve_or_complain(bot, name)


async def _subscribe_current_group(ev: Event, account_id: int) -> None:
    """把当前群登记为该盘的播报目标。

    ``ws_bot_id`` / ``bot_self_id`` 必须**在这一刻**从真实 Event 上抄下来存库：
    播报发生在 cron 里，那时没有任何 Event 可以参照，而 ``emit_proactive_message``
    要靠 ``ws_bot_id`` 在 ``gss.active_bot`` 里挑对连接。少了它多适配器部署下会把
    QQ 的消息投进 Discord 的连接，静默失败。
    """
    if not ev.group_id:
        return
    await _db.PaperBroadcastRepo.add(
        account_id,
        bot_id=ev.bot_id or "",
        group_id=str(ev.group_id),
        bot_self_id=ev.bot_self_id or "",
        ws_bot_id=ev.WS_BOT_ID or ev.real_bot_id or "",
        created_by=str(ev.user_id),
    )


# ============================================================
# 1) Kanban 心跳树
# ============================================================
async def _setup_papertrade_kanban_trees(ev: Event, account: SayuPaperAccount) -> tuple[str | None, str | None]:
    """注册 Kanban init / period 两棵树并挂 APScheduler cron，返回 (init_root_id, period_root_id)。

    ``scope_key`` 用 ``account_id`` 而不是 ``(group_id, bot_id)``：多盘之后同一个群
    可以有多个盘，用群号做 scope_key 会让第二个盘复用第一个盘的心跳树，两个决策代理
    并发写同一个账本（``position_upsert`` 是读-改-写，交错就把持仓算坏）。

    子任务描述里注入了**策略的 prompt_block**（生效点 1）——这是策略影响 LLM 行为的
    主通道。注意它是**建树时快照**，之后改策略必须重建心跳树才会生效，
    ``send_switch_strategy`` 会负责这件事。

    任何一步失败抛 RuntimeError，由调用方把当前进度打到用户消息里。
    """
    from gsuid_core.ai_core.planning.kanban import create_kanban_tree

    account_id: int = account.id or 0
    group_id: str = str(ev.group_id) if ev.group_id else ""
    bot_id: str = ev.bot_id or ""
    strategy, params = _strat.resolve_with_params(
        account.strategy_id, _db.parse_strategy_params(account.strategy_params)
    )
    # ─── 1) Kanban init 树（leaf-root 模式，setup_agent 跑一次完成初始化） ───
    init_root_id: str | None = None
    try:
        init_root, _ = await create_kanban_tree(
            goal=f"模拟盘「{account.name}」init",
            owner_user_id=str(ev.user_id),
            scope_key=f"papertrade_init_acc_{account_id}",
            bot_id=bot_id,
            persona_name=None,
            bot_self_id=ev.bot_self_id or "",
            group_id=group_id,
            user_type="group",
            WS_BOT_ID=ev.WS_BOT_ID,
            session_id=f"papertrade_init_acc_{account_id}",
            user_pm=0,
            broadcast_targets=[group_id] if group_id else [],
            subtasks=None,
            recurring_trigger=None,
            recurring_until=None,
            root_agent_profile="papertrade_setup_agent",
        )
        init_root_id = init_root.id
        await _db.PaperAccountRepo.bind_kanban_init(account_id, init_root_id)
    except Exception as e:
        raise RuntimeError(f"Kanban init 树创建失败: {type(e).__name__}: {e}") from e

    # ─── 2) Kanban period 树（4 子任务：decision/snapshot/monthly_report/pool_refresh） ───
    period_root_id: str | None = None
    try:
        subtasks: list[dict] = [
            {
                "description": strategy.kanban_decision_task(account.name, params),
                "agent_profile": strategy.agent_profile,
                # 只在实际交易时段的整/半点触发：9:30/10:00/10:30/11:00/11:30 +
                # 13:00/13:30/14:00/14:30/15:00。cron 的分/时是笛卡尔积，9-11,13-15
                # 会多出 9:00（开盘前）和 15:30（收盘后）两个点——由决策代理开头的
                # stock_is_trading_day 交易时段守卫直接 NO_BROADCAST 退出，不误动。
                # 午休 12:00/12:30 已被 hour 列表（跳过 12）排除。
                "recurring_trigger": "cron:0,30 9-11,13-15 * * 1-5",
            },
            {
                "description": (
                    f"收盘后为模拟盘「{account.name}」写当日净值快照："
                    "直接调 papertrade_snapshot_write() 一次"
                    "（现金 + Σ持仓实时市值 → total_equity / total_pnl / pnl_pct，"
                    "按 trade_date 幂等 upsert），然后按代理规约输出 NO_BROADCAST。"
                ),
                "agent_profile": "papertrade_snapshot_agent",
                # 15:05：收盘（15:00）后 5 分钟，等东财 EOD 价落定即写，不再拖到 15:35。
                "recurring_trigger": "cron:5 15 * * 1-5",
            },
            {
                "description": f"月初为模拟盘「{account.name}」出复盘报告（月收益 / 胜率 / 最大回撤）",
                "agent_profile": "papertrade_reporter_agent",
                "recurring_trigger": "cron:0 9 1 * *",
            },
            {
                "description": (
                    f"候选池轮换（模拟盘「{account.name}」，独立于 decision，每 2 小时跑一次）：\n"
                    "直接调 papertrade_candidate_refresh() 做一次轮换"
                    "（淘汰最旧 auto + 补蓝筹底仓 + 按本盘策略配置的来源补充），"
                    "再 papertrade_agent_pool_list 看轮换后的池;\n"
                    "**本轮仅做轮换，不调任何撮合/流水/持仓/决策工具，不做 buy/sell 判断**。"
                ),
                "agent_profile": "papertrade_pool_refresh_agent",
                # 10:15 / 14:15：均落在交易时段内，避开午休（原 12:30 在午休且不可靠）。
                "recurring_trigger": "cron:15 10,14 * * 1-5",
            },
        ]
        period_root, _ = await create_kanban_tree(
            goal=f"模拟盘「{account.name}」周期托管",
            owner_user_id=str(ev.user_id),
            scope_key=f"papertrade_period_acc_{account_id}",
            bot_id=bot_id,
            persona_name=None,
            bot_self_id=ev.bot_self_id or "",
            group_id=group_id,
            user_type="group",
            WS_BOT_ID=ev.WS_BOT_ID,
            session_id=f"papertrade_period_acc_{account_id}",
            user_pm=0,
            broadcast_targets=[group_id] if group_id else [],
            subtasks=subtasks,
            recurring_trigger=None,
            recurring_until=None,
            root_agent_profile="",
        )
        period_root_id = period_root.id
        await _db.PaperAccountRepo.bind_kanban_period(account_id, period_root_id)

        # ─── 关键修复（2026-07-01）：ROOT 本身不设 recurring_trigger。
        #
        # 框架的"根级周期模板"和"子任务级周期模板"是两条独立机制（各自的
        # arm/clone/schedule 代码路径完全不同，见 kanban.py / recurring.py）：
        # 若给 create_kanban_tree 传 recurring_trigger，ROOT 在创建那一刻
        # 起 recurring_status 就已经被写成 'armed'（kanban.py:160），随后
        # execute_ready_tasks 一进来就命中 early-return（kanban_executor.py:
        # 501：``if root.recurring_trigger and root.recurring_status ==
        # "armed": return``）——_maybe_arm_recurring_subtasks 根本不会被调
        # 用，4 个子任务永远不会被 arm 到 APScheduler。
        #
        # 正确做法：ROOT 保持非周期，只让子任务自带各自的 recurring_trigger；
        # kick_root 一次即可触发 execute_ready_tasks →
        # _maybe_arm_recurring_subtasks，把子任务独立 arm 到 APScheduler。
        # 进程重启由启动期 ``restore_armed_subtask_templates`` 统一恢复。
        from gsuid_core.ai_core.planning.kanban_executor import kick_root as _kick_root

        await _kick_root(period_root_id)
    except Exception as e:
        if init_root_id:
            try:
                from gsuid_core.ai_core.planning.kanban import fail_task_tree

                await fail_task_tree(init_root_id, reason="period 树创建失败，回滚 init 树")
            except Exception:
                pass
        raise RuntimeError(f"Kanban period 树创建失败: {type(e).__name__}: {e}") from e

    return init_root_id, period_root_id


async def _teardown_kanban_trees(account: SayuPaperAccount) -> None:
    """删盘 / 换策略前把旧心跳树终结掉。

    不终结的后果很具体：APScheduler 里 armed 的子任务模板还在，cron 到点照样
    fire → ``kick_root`` 一棵指向已删账户的树 → 决策代理拿不到账户，每 30 分钟
    烧一次 token 报一次错。
    """
    from gsuid_core.logger import logger

    for root_id in (account.kanban_init_root_id, account.kanban_period_root_id):
        if not root_id:
            continue
        try:
            from gsuid_core.ai_core.planning.kanban import fail_task_tree

            await fail_task_tree(root_id, reason="模拟盘已删除或切换策略，心跳树终止")
        except Exception as e:
            logger.warning(f"[SayuStock][PaperTrade] 终止心跳树 {root_id} 失败（不阻塞）: {e}")


async def _rebuild_kanban_trees(ev: Event, account: SayuPaperAccount) -> tuple[str | None, str | None]:
    """补挂 / 启用 / 换策略：先拆旧树再新建。

    ``create_kanban_tree`` 按 scope_key 每次新建一棵，不是幂等复用；不先
    teardown 会给同一盘挂上第二棵 period 树，两棵树并发 ``position_upsert``。
    """
    await _teardown_kanban_trees(account)
    return await _setup_papertrade_kanban_trees(ev, account)


async def _kick_immediate_decision(ev: Event, account: SayuPaperAccount) -> None:
    """fire-and-forget 立即触发一次 ``papertrade_decision_agent``。

    播报口径：**成交播报完全交给 ``papertrade_trade_insert`` 工具**在成交那一刻
    确定性推一行简洁冒泡（见 ``broadcast.broadcast_fill``）；决策代理最终只输出
    ``<<NO_BROADCAST>>``、推理只落库。所以这里 await 完 capagent 即结束——不再拍
    快照 / 算 Δ / 拼"操盘播报"结构化文本推群（避免把决策理由 / 账户汇总泄漏到群里）。
    """
    from gsuid_core.logger import logger

    if int(account.enabled or 0) == 0:
        logger.info(f"[SayuStock][PaperTrade] 盘「{account.name}」已停用，跳过立即决策")
        return

    try:
        from gsuid_core.ai_core.capability_agents.runner import run_capability_agent
    except Exception as e:
        logger.exception(f"[SayuStock][PaperTrade] init 立即决策：依赖 import 失败: {e}")
        return

    strategy, params = _strat.resolve_with_params(
        account.strategy_id, _db.parse_strategy_params(account.strategy_params)
    )
    task_prompt = strategy.kick_task(account.name, params)

    # grant_write：这条路走 run_capability_agent 的 ad-hoc 分支，root_task_id 是现造的
    # adhoc_*，对不上账户的 Kanban 树，不发票会被 deny_write_reason 拒掉（它本该能真实成交）。
    try:
        async with _DECISION_KICK_SEM:
            with _scope.grant_write(account.id or 0):
                await run_capability_agent(
                    profile_id=strategy.agent_profile,
                    task=task_prompt,
                    ev=ev,
                    bot=None,
                    session_id_suffix=f"init_decision_acc_{account.id}",
                )
    except Exception as e:
        logger.exception(f"[SayuStock][PaperTrade] papertrade_decision_agent 执行异常: {e}")
        from . import broadcast as _bc

        await _bc.broadcast_text(
            account.id or 0,
            f"⚠️ 模拟盘「{account.name}」心跳异常：{type(e).__name__}: {str(e)[:200]}",
            trigger_reason=f"papertrade_init_kick_failed:{account.name}",
            source="kanban",
        )
        return

    logger.info(f"[SayuStock][PaperTrade] init 决策已跑完（成交由 trade_insert 工具即时播报）盘={account.name}")


async def _kick_after_kanban_ready(ev: Event, account: SayuPaperAccount, init_id: str | None) -> None:
    """Kanban 树就绪后立即 fire-and-forget 触发 init 验证 + （开盘时）一次决策。

    - init 树永远踢一次——验证账户 / 回填 root_id / papertrade_setup_agent 自检。
    - decision 仅在 ``_is_market_open_now()`` 为真时踢——非开盘时段让 cron 兜底，
      避免浪费 token。

    所有 kick 都是 ``asyncio.create_task``，不阻塞 send 成功消息。
    """
    from gsuid_core.logger import logger

    if not init_id:
        return

    try:
        from gsuid_core.ai_core.planning.kanban_executor import kick_root

        _init_task: asyncio.Task = asyncio.create_task(kick_root(init_id))
        _ = _init_task  # 显式持有 task 引用防 GC
    except Exception as e:
        logger.exception(f"[SayuStock][PaperTrade] kick init 失败: {e}")

    if _is_market_open_now():
        try:
            _decision_task: asyncio.Task = asyncio.create_task(_kick_immediate_decision(ev, account))
            _ = _decision_task
            logger.info("[SayuStock][PaperTrade] init-time 决策 kick 已派发（fire-and-forget）。")
        except Exception as e:
            logger.exception(f"[SayuStock][PaperTrade] kick decision 派发失败: {e}")


# ============================================================
# 2) 建盘：模拟盘初始化 / 模拟盘创建
# ============================================================
async def _create_account_flow(
    bot: Bot,
    ev: Event,
    *,
    name: str,
    strategy_id: str,
    initial_cash: float,
) -> list[str] | None:
    """建盘 + 建心跳树 + 订阅本群播报 + 立即 kick 的完整流程。"""
    err: str = _scope.validate_account_name(name)
    if err:
        return await bot.send(err)
    norm_name: str = _scope.normalize_account_name(name)

    if _strat.get_strategy(strategy_id) is None:
        return await bot.send(f"⚠️ 没有名为 {strategy_id!r} 的策略。\n{_strat.describe_all()}")

    existing = await _db.PaperAccountRepo.get_by_name(norm_name)
    if existing is not None:
        return await bot.send(
            f"ℹ️ 已存在名为「{norm_name}」的模拟盘（id={existing.id}，策略 {existing.strategy_id}）。\n"
            f"换个名字，或用「模拟盘查看 {norm_name}」看它现在怎么样。"
        )

    # 成本闸：每个启用中的盘每交易日约 13 次 capability agent 调用，N 个盘就是 13N 次
    enabled = await _db.PaperAccountRepo.list_enabled()
    if len(enabled) >= _scope.MAX_ENABLED_ACCOUNTS:
        return await bot.send(
            f"⚠️ 启用中的模拟盘已达上限 {_scope.MAX_ENABLED_ACCOUNTS} 个"
            f"（每个盘每个交易日约 13 次 LLM 调用，再加会显著抬高账单）。\n"
            f"请先用「模拟盘停用 <盘名>」停掉不用的盘，或「模拟盘删除 <盘名>」清掉。"
        )

    group_id, bot_id = _scope.origin_from_event(ev)
    try:
        acc = await _db.PaperAccountRepo.create(
            norm_name,
            group_id,
            bot_id,
            strategy_id=strategy_id,
            initial_cash=initial_cash,
            mode="balanced",
            initialized_by=str(ev.user_id),
        )
    except Exception as e:
        return await bot.send(f"⚠️ 建账户失败: {type(e).__name__}: {e}")

    # 建盘的群默认订阅它的播报（不然建完盘一条成交都看不到，用户会以为没跑）
    try:
        await _subscribe_current_group(ev, acc.id or 0)
    except Exception as e:
        from gsuid_core.logger import logger

        logger.warning(f"[SayuStock][PaperTrade] 建盘时订阅本群播报失败（可用「模拟盘推送添加」补）: {e}")

    try:
        init_id, period_id = await _setup_papertrade_kanban_trees(ev, acc)
    except RuntimeError as e:
        fresh = await _db.PaperAccountRepo.get_by_id(acc.id or 0)
        await _teardown_kanban_trees(fresh or acc)
        try:
            await _db.PaperAccountRepo.reset_account(acc.id or 0)
        except Exception:
            pass
        return await bot.send(f"⚠️ 创建失败：{e}\n账户已自动回滚。请检查 gsuid_core.ai_core 是否就绪后重试。")

    await _kick_after_kanban_ready(ev, acc, init_id)
    suffix = (
        " + 一次决策心跳（买/卖完成后推群播报；hold 按设计不推）"
        if _is_market_open_now()
        else "（非开盘时段，跳过决策；等下次 cron）"
    )
    await bot.send(
        f"✅ 模拟盘「{acc.name}」已创建\n"
        f"策略: {acc.strategy_id}\n"
        f"初始资金: {acc.initial_cash:,.0f}\n"
        f"模式: {acc.mode}\n"
        f"本群已自动订阅它的成交播报（「模拟盘推送删除 {acc.name}」可退订）\n"
        f"所有盘共用 30 分钟心跳节奏。\n\n"
        f"Kanban 心跳树：\n"
        f"  init_root   = {(init_id or '')[:16]}…\n"
        f"  period_root = {(period_id or '')[:16]}…\n\n"
        f"已立即触发 init 验证{suffix}。"
    )


def _parse_cash(raw: str) -> float | None:
    """解析初始资金；非法返回 None。范围 1w ~ 1 亿。"""
    try:
        parsed = float(raw.replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None
    return parsed if 10_000 <= parsed <= 1_000_000_000 else None


# 同时注册 fullmatch 与 prefix：``_check_prefix`` 显式排除了完全匹配的情况
# （``msg.startswith(kw) and not fullmatch``），只挂 prefix 的话光发「模拟盘初始化」
# 四个字反而不触发 —— 而那正是最常见的用法。to_ai 只挂一次，避免注册两个同名 AI 工具。
@sv_papertrade.on_fullmatch(("模拟盘初始化",))
@sv_papertrade.on_prefix(
    ("模拟盘初始化",),
    to_ai="""初始化默认模拟盘（默认 100w 现金，multi_factor 策略）。等价于
「模拟盘创建 默认模拟盘」。已存在时会自检并补挂丢失的 Kanban 心跳树。

仅群主/管理员可触发。

Args:
    text: 自定义初始资金（如 2000000），留空用默认 100w
""",
)
async def send_init_command(bot: Bot, ev: Event) -> list[str] | None:
    """「模拟盘初始化」= 建/修默认盘。

    与「模拟盘创建」的区别：本命令对**已存在**的默认盘做自愈（补挂心跳树），
    是用户在"AI 不动了"时的第一反应命令，所以必须幂等且能修复。
    """
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可初始化模拟盘")

    initial_cash: float = 1_000_000.0
    raw_text: str = ev.text.strip() if ev.text else ""
    if raw_text:
        parsed = _parse_cash(raw_text)
        if parsed is None:
            return await bot.send(f"⚠️ 初始资金格式错误：{raw_text!r}（须为 1w~1亿的数字）")
        initial_cash = parsed

    existing = await _db.PaperAccountRepo.get_by_name(DEFAULT_ACCOUNT_NAME)
    if existing is None:
        return await _create_account_flow(
            bot,
            ev,
            name=DEFAULT_ACCOUNT_NAME,
            strategy_id=_strat.DEFAULT_STRATEGY_ID,
            initial_cash=initial_cash,
        )

    # 心跳树"丢了"的三种形态都要能自愈重挂：
    #   1. root_id 为空（手动清盘 / 升级迁移）；
    #   2. root_id 指向的树已不存在（DB 被清）；
    #   3. 树还在但已终结（fail_task_tree / 取消）——2026-07-06 踩坑：
    #      persona 把旧心跳树 fail 掉之后，账户里还挂着死树的 root_id，
    #      旧逻辑只查"是否为空"就直接返回"已开户"，用户永远无法用
    #      「模拟盘初始化」修复心跳，只能手工改库。
    need_rebuild: bool = not existing.kanban_init_root_id or not existing.kanban_period_root_id
    if not need_rebuild and existing.kanban_period_root_id:
        try:
            from gsuid_core.ai_core.planning.models import AIAgentTask

            period_root = await AIAgentTask.get_by_id(existing.kanban_period_root_id)
            if period_root is None or period_root.status in ("failed", "cancelled", "completed"):
                need_rebuild = True
        except Exception:
            pass  # 框架查询异常时保持旧行为（不误重建）

    if not need_rebuild:
        try:
            await _subscribe_current_group(ev, existing.id or 0)
        except Exception:
            pass
        return await bot.send(
            f"✅ 默认模拟盘已就绪（策略={existing.strategy_id}）\n"
            f"本群已加入推送列表。新建其它策略盘请用：模拟盘创建 放量盘 volume_extremum"
        )

    try:
        init_id, period_id = await _rebuild_kanban_trees(ev, existing)
    except RuntimeError as e:
        return await bot.send(
            f"⚠️ 账户已存在（id={existing.id}），但补挂 Kanban 心跳失败：{e}\n"
            f"开盘后不会自动决策。请联系 SUPERUSER 通过「模拟盘清盘」重置。"
        )
    await _kick_after_kanban_ready(ev, existing, init_id)
    return await bot.send(
        f"ℹ️ 模拟盘「{existing.name}」已存在（id={existing.id}），已补挂 Kanban 心跳：\n"
        f"  init_root_id   = {init_id or '(空)'}\n"
        f"  period_root_id = {period_id or '(空)'}\n"
        f"已立即触发 init 验证"
        f"{' + 一次决策心跳' if _is_market_open_now() else '（非开盘时段，跳过决策）'}。"
    )


@sv_papertrade.on_fullmatch(("模拟盘创建", "新建模拟盘"))
@sv_papertrade.on_prefix(
    ("模拟盘创建", "新建模拟盘"),
    to_ai="""新建一个**命名**模拟盘。多个盘各跑各的策略、各有各的账本，互不干扰。

Args:
    text: "<盘名> [策略id] [初始资金]"。策略必须是「模拟盘策略列表」里的 id，
        省略则用 multi_factor。例："放量盘 volume_extremum 500000"。
""",
)
async def send_create_account(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可创建模拟盘")

    tokens: list[str] = [t for t in _scope.normalize_account_name(ev.text).split(" ") if t]
    if not tokens:
        return await bot.send("用法：模拟盘创建 <盘名> <策略id> [初始资金]\n\n" + _strat.describe_all())

    name: str = tokens[0]
    strategy_id: str = _strat.DEFAULT_STRATEGY_ID
    initial_cash: float = 1_000_000.0
    for token in tokens[1:]:
        cash = _parse_cash(token)
        if cash is not None:
            initial_cash = cash
        else:
            strategy_id = token

    return await _create_account_flow(bot, ev, name=name, strategy_id=strategy_id, initial_cash=initial_cash)


# ============================================================
# 3) 盘管理：列表 / 改名 / 删除 / 启停
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘列表", "模拟盘一览"),
    to_ai="列出所有模拟盘（盘名 / 策略 / 状态 / 现金）。多盘时先看这个再查具体的盘。",
)
async def send_account_list(bot: Bot, ev: Event) -> list[str] | None:
    accounts = await _db.PaperAccountRepo.list_all()
    if not accounts:
        return await bot.send(_scope.not_opened_message())
    lines: list[str] = [f"【模拟盘列表 · 共 {len(accounts)} 个】"]
    for a in accounts:
        snap = await _db.PaperSnapshotRepo.latest(a.id or 0)
        equity = f"{snap.total_equity:,.0f}" if snap else "—"
        pnl = f"{snap.total_pnl_pct:+.2f}%" if snap else "—"
        lines.append(
            f"{'🟢' if a.enabled else '🔴'} {a.name}  [{a.strategy_id}]\n"
            f"    现金 {a.cash:,.0f}  总资产 {equity}  收益 {pnl}"
        )
    lines.append("\n查询：模拟盘查看 <盘名> / 模拟盘持仓 <盘名> / 模拟盘收益 <盘名> <周期>")
    await bot.send("\n".join(lines))


@sv_papertrade.on_fullmatch(("模拟盘改名",))
@sv_papertrade.on_prefix(
    ("模拟盘改名",),
    to_ai='给模拟盘改名。Args: text = "<旧名> <新名>"',
)
async def send_rename_account(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可改名")
    tokens = [t for t in _scope.normalize_account_name(ev.text).split(" ") if t]
    if len(tokens) != 2:
        return await bot.send("⚠️ 用法：模拟盘改名 <旧名> <新名>")
    old_name, new_name = tokens

    acc = await _resolve_or_complain(bot, old_name)
    if acc is None:
        return None
    err = _scope.validate_account_name(new_name)
    if err:
        return await bot.send(err)
    norm_new = _scope.normalize_account_name(new_name)
    if await _db.PaperAccountRepo.get_by_name(norm_new) is not None:
        return await bot.send(f"⚠️ 已存在名为「{norm_new}」的模拟盘，换一个名字。")

    updated = await _db.PaperAccountRepo.rename(acc.id or 0, norm_new)
    if updated is None:
        return await bot.send("⚠️ 改名失败（账户可能刚被删除）")
    # 心跳树的 scope_key 用的是 account_id 而不是盘名，所以改名不需要重建树
    await bot.send(f"✅ 「{acc.name}」已改名为「{updated.name}」（心跳树不受影响，account_id 未变）")


@sv_papertrade.on_fullmatch(("模拟盘删除",))
@sv_papertrade.on_prefix(
    ("模拟盘删除",),
    to_ai='删除一个模拟盘及其全部账本数据。不可撤销。Args: text = "<盘名>"',
)
async def send_delete_account(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可删除模拟盘")
    name = _scope.normalize_account_name(ev.text)
    if not name:
        return await bot.send("⚠️ 用法：模拟盘删除 <盘名>（发送「模拟盘列表」看有哪些盘）")

    acc = await _resolve_or_complain(bot, name)
    if acc is None:
        return None

    # 先停心跳再删数据：反过来的话 cron 可能在两步之间 fire，决策代理拿不到账户报错
    await _teardown_kanban_trees(acc)
    deleted = await _db.PaperAccountRepo.reset_account(acc.id or 0)
    await bot.send(
        f"✅ 模拟盘「{acc.name}」已删除\n"
        f"清理：持仓 {deleted.get('position', 0)} · 流水 {deleted.get('trade', 0)} · "
        f"决策 {deleted.get('decision', 0)} · 快照 {deleted.get('snapshot', 0)} · "
        f"候选 {deleted.get('agent_pool', 0)} · 播报订阅 {deleted.get('broadcast', 0)}"
    )


@sv_papertrade.on_fullmatch(("模拟盘停用", "模拟盘启用"))
@sv_papertrade.on_prefix(
    ("模拟盘停用", "模拟盘启用"),
    to_ai='停用/启用一个模拟盘（停用后不再消耗 LLM 额度）。Args: text = "<盘名>"',
)
async def send_toggle_account(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可启停模拟盘")
    enable: bool = ev.command == "模拟盘启用"
    acc = await _require_named_account(bot, ev.text, f"⚠️ 用法：{ev.command} <盘名>")
    if acc is None:
        return None
    if enable and acc.enabled:
        return await bot.send(f"ℹ️ 模拟盘「{acc.name}」本来就是启用状态。")
    if (not enable) and (not acc.enabled):
        return await bot.send(f"ℹ️ 模拟盘「{acc.name}」本来就是停用状态。")

    if enable:
        enabled = await _db.PaperAccountRepo.list_enabled()
        if not acc.enabled and len(enabled) >= _scope.MAX_ENABLED_ACCOUNTS:
            return await bot.send(f"⚠️ 启用中的模拟盘已达上限 {_scope.MAX_ENABLED_ACCOUNTS} 个，请先停用别的盘。")
        updated = await _db.PaperAccountRepo.update(acc.id or 0, enabled=1)
        if updated is None:
            return await bot.send("⚠️ 启用失败（账户可能刚被删除）")
        try:
            init_id, _ = await _rebuild_kanban_trees(ev, updated)
        except RuntimeError as e:
            return await bot.send(
                f"⚠️ 已启用「{updated.name}」，但心跳树重建失败：{e}\n请再发一次「模拟盘启用 {updated.name}」。"
            )
        await _kick_after_kanban_ready(ev, updated, init_id)
        return await bot.send(f"🟢 已启用模拟盘「{updated.name}」，心跳已恢复（所有盘共用 30 分钟节奏）")

    await _teardown_kanban_trees(acc)
    await _db.PaperAccountRepo.update(acc.id or 0, enabled=0)
    await bot.send(f"🔴 已停用模拟盘「{acc.name}」（心跳树已终止，不再烧 LLM 额度）")


# ============================================================
# 4) 策略
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘策略列表", "模拟盘策略"),
    to_ai="列出可用的模拟盘策略及其可调参数。",
)
async def send_strategy_list(bot: Bot, ev: Event) -> list[str] | None:
    await bot.send(_strat.describe_all() + "\n切换：模拟盘策略切换 <盘名> <策略id>")


@sv_papertrade.on_fullmatch(("模拟盘策略切换",))
@sv_papertrade.on_prefix(
    ("模拟盘策略切换",),
    to_ai='给某个模拟盘换策略。Args: text = "<盘名> <策略id>"',
)
async def send_switch_strategy(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可切换策略")
    tokens = [t for t in _scope.normalize_account_name(ev.text).split(" ") if t]
    if len(tokens) != 2:
        return await bot.send("用法：模拟盘策略切换 <盘名> <策略id>\n\n" + _strat.describe_all())
    name, strategy_id = tokens

    acc = await _resolve_or_complain(bot, name)
    if acc is None:
        return None
    if _strat.get_strategy(strategy_id) is None:
        return await bot.send(f"⚠️ 没有名为 {strategy_id!r} 的策略。\n{_strat.describe_all()}")
    if acc.strategy_id == strategy_id:
        return await bot.send(f"ℹ️ 模拟盘「{acc.name}」当前已经是 {strategy_id} 策略。")

    # 换策略必须重建心跳树：策略的 prompt_block 是**建树时**写进子任务描述的快照，
    # 只改 DB 字段的话，硬闸和候选池偏好立刻换了，但 LLM 收到的仍是旧策略的纪律
    # —— 它会按旧纪律干活然后被新硬闸一路拒到心跳空转。
    updated = await _db.PaperAccountRepo.update(acc.id or 0, strategy_id=strategy_id, strategy_params="{}")
    if updated is None:
        return await bot.send("⚠️ 切换失败（账户可能刚被删除）")
    try:
        init_id, _ = await _rebuild_kanban_trees(ev, updated)
    except RuntimeError as e:
        return await bot.send(
            f"⚠️ 策略已切到 {strategy_id}，但心跳树重建失败：{e}\n"
            f"请再发一次「模拟盘策略切换 {updated.name} {strategy_id}」"
            f"或「模拟盘启用 {updated.name}」。"
        )
    await _kick_after_kanban_ready(ev, updated, init_id)
    await bot.send(
        f"✅ 模拟盘「{updated.name}」策略已切换：{acc.strategy_id} → {strategy_id}\n"
        f"心跳树已按新策略重建（旧树已终止），策略参数重置为默认值。"
    )


# ============================================================
# 5) 播报订阅
# ============================================================
@sv_papertrade.on_fullmatch(("模拟盘推送添加", "模拟盘订阅"))
@sv_papertrade.on_prefix(
    ("模拟盘推送添加", "模拟盘订阅"),
    to_ai='让**当前群**订阅某个模拟盘的成交播报。Args: text = "<盘名>"',
)
async def send_broadcast_add(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可管理播报订阅")
    if not ev.group_id:
        return await bot.send("⚠️ 播报订阅只能在群里设置（私聊没有群号可订阅）")
    acc = await _require_named_account(bot, ev.text, "⚠️ 用法：模拟盘推送添加 <盘名>")
    if acc is None:
        return None
    await _subscribe_current_group(ev, acc.id or 0)
    await bot.send(f"✅ 本群已订阅模拟盘「{acc.name}」的成交播报（买卖各一行冒泡，决策推理不推群）")


@sv_papertrade.on_fullmatch(("模拟盘推送删除", "模拟盘退订"))
@sv_papertrade.on_prefix(
    ("模拟盘推送删除", "模拟盘退订"),
    to_ai='让**当前群**退订某个模拟盘的成交播报。Args: text = "<盘名>"',
)
async def send_broadcast_remove(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可管理播报订阅")
    if not ev.group_id:
        return await bot.send("⚠️ 播报订阅只能在群里设置")
    acc = await _require_named_account(bot, ev.text, "⚠️ 用法：模拟盘推送删除 <盘名>")
    if acc is None:
        return None
    n = await _db.PaperBroadcastRepo.remove(acc.id or 0, str(ev.group_id))
    if n <= 0:
        return await bot.send(f"ℹ️ 本群本来就没有订阅「{acc.name}」的播报。")
    await bot.send(f"✅ 本群已退订模拟盘「{acc.name}」的成交播报。")


@sv_papertrade.on_fullmatch(
    ("模拟盘推送列表",),
    to_ai="看**当前群**订阅了哪些模拟盘的播报。",
)
async def send_broadcast_list(bot: Bot, ev: Event) -> list[str] | None:
    if not ev.group_id:
        return await bot.send("⚠️ 该命令只能在群里使用")
    rows = await _cross.query_accounts_by_group(str(ev.group_id))
    if not rows:
        return await bot.send("ℹ️ 本群未订阅任何模拟盘的播报。用「模拟盘推送添加 <盘名>」订阅。")
    lines = [f"【本群订阅的模拟盘 · {len(rows)} 个】"]
    for r in rows:
        lines.append(f"· {r['account_name']}  [{r['strategy_id']}]")
    await bot.send("\n".join(lines))


# ============================================================
# 6) 查询
# ============================================================
@sv_papertrade.on_fullmatch(("模拟盘查看",))
@sv_papertrade.on_prefix(
    ("模拟盘查看",),
    to_ai="出一张模拟盘账户视图图（账户信息 + 持仓 + 最近 5 笔交易）。Args: text = 盘名（必填）",
)
async def send_view(bot: Bot, ev: Event) -> list[str] | None:
    acc = await _require_named_account(bot, ev.text, "⚠️ 用法：模拟盘查看 <盘名>")
    if acc is None:
        return None
    await bot.send(await draw_account_view(acc.id or 0))


@sv_papertrade.on_fullmatch(("模拟盘自选", "模拟盘持仓"), block=True)
@sv_papertrade.on_prefix(
    ("模拟盘自选", "模拟盘持仓"),
    block=True,
    to_ai="""查看 AI 模拟盘当前持仓的简化版卡片图（类似「我的自选」）。

    当用户问「模拟盘自选」「模拟盘持仓」「模拟盘持仓图」「你持仓怎么样发张图」「仓位图」时调用。
    图上含：账户摘要（现金/总资产/浮盈）、每只持仓的数量/成本/现价、
    **今日涨跌** 与 **持仓收益率**。

    ⚠️ 这是**简化版**：不含交易流水、决策日志、候选池；完整账本请用
    papertrade_account_query / papertrade_position_list / papertrade_trade_list，
    或命令「模拟盘查看」「模拟盘记录」。

    Args:
        text: 盘名（必填）
    """,
)
async def send_holdings(bot: Bot, ev: Event) -> list[str] | None:
    """用户命令：模拟盘自选 / 模拟盘持仓 → 持仓简图（无需 agent）。"""
    from gsuid_core.logger import logger
    from gsuid_core.ai_core.trigger_bridge import ai_return

    logger.info("[SayuStock] 开始执行[模拟盘自选/模拟盘持仓]")
    acc = await _require_named_account(bot, ev.text, f"⚠️ 用法：{ev.command} <盘名>")
    if acc is None:
        return None

    result = await build_holdings_snapshot_image(acc.id or 0)
    if isinstance(result, str):
        return await bot.send(result)

    # 有图必有文字：trigger 桥接 / 多模态模型可读
    try:
        ai_return(
            f"【模拟盘自选·简化版】已出「{acc.name}」持仓图：含今日涨跌与持仓浮盈，"
            "不含交易流水/决策日志。完整数据见「模拟盘查看」「模拟盘记录」。"
        )
    except Exception:
        pass
    await bot.send(result)


# 周期 → 起始时间的映射。ytd 单独走 since_calc（= 今年 1/1 至今，不是 now-365d，
# 否则 6 月份触发会少算 6 个月）。
_PERIOD_DAYS: dict[str, int | None] = {
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
    "总": None,
    "全部": None,
    "all": None,
}


@sv_papertrade.on_fullmatch(("模拟盘收益",))
@sv_papertrade.on_prefix(
    ("模拟盘收益",),
    to_ai='查模拟盘某周期的已实现盈亏。Args: text = "<盘名> [周期]"，周期 = 日/周/月/季/年/ytd/总',
)
async def send_pnl(bot: Bot, ev: Event) -> list[str] | None:
    name, period = _scope.split_name_and_period(ev.text)
    if not name:
        return await bot.send("⚠️ 用法：模拟盘收益 <盘名> [周期]\n周期 = 日/周/月/季/年/ytd/总")
    period = period or "总"

    if period == "ytd":
        now = _dt.datetime.now()
        since: _dt.datetime | None = _dt.datetime(now.year, 1, 1)
    elif period not in _PERIOD_DAYS:
        return await bot.send(f"⚠️ 周期须为 日/周/月/季/年/ytd/总（收到 {period!r}）")
    else:
        days = _PERIOD_DAYS[period]
        since = None if days is None else _dt.datetime.now() - _dt.timedelta(days=days)

    acc = await _resolve_or_complain(bot, name)
    if acc is None:
        return None
    agg = await _db.PaperTradeRepo.aggregate_pnl(acc.id or 0, since=since)
    label: str = period if period in ("总", "全部", "all") else f"近{period}"
    await bot.send(
        f"📊 模拟盘「{acc.name}」· {label}盈亏\n"
        f"已实现盈亏: {agg['total_pnl']:+,.2f}\n"
        f"总成交额: {agg['total_amount']:,.0f}\n"
        f"总手续费: {agg['total_fee']:,.2f}\n"
        f"交易笔数: {agg['trade_count']}"
    )


@sv_papertrade.on_fullmatch(("模拟盘记录",))
@sv_papertrade.on_prefix(
    ("模拟盘记录",),
    to_ai="查模拟盘最近 20 笔交易流水。Args: text = 盘名（必填）",
)
async def send_records(bot: Bot, ev: Event) -> list[str] | None:
    acc = await _require_named_account(bot, ev.text, "⚠️ 用法：模拟盘记录 <盘名>")
    if acc is None:
        return None
    rows = await _db.PaperTradeRepo.list_by_account(acc.id or 0, limit=20)
    if not rows:
        return await bot.send(f"ℹ️ 模拟盘「{acc.name}」暂无交易记录")
    lines: list[str] = [f"【模拟盘「{acc.name}」· 最近 20 笔交易】"]
    for t in rows:
        side = "买" if t.side == "buy" else "卖"
        executed_at: _dt.datetime | None = t.executed_at
        at = executed_at.strftime("%m-%d %H:%M") if executed_at else "?"
        pnl = f" 盈{t.realized_pnl:+.0f}" if t.side == "sell" and t.realized_pnl else ""
        lines.append(f"[{at}] {side} {t.stock_name or t.stock_code} {t.qty}@{t.price:.2f} 费{t.fee:.1f}{pnl}")
    await bot.send("\n".join(lines))


@sv_papertrade.on_fullmatch(
    ("模拟盘排行",),
    to_ai="跨盘收益排行图（各盘最新快照按收益率降序）。",
)
async def send_leaderboard(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可看跨盘排行")
    await bot.send(await draw_leaderboard())


@sv_papertrade.on_fullmatch(("模拟盘查询",))
@sv_papertrade.on_prefix(
    ("模拟盘查询",),
    to_ai='查某个盘的文本明细（账户 + 持仓 + 最新快照）。Args: text = "<盘名>"',
)
async def send_query_group(bot: Bot, ev: Event) -> list[str] | None:
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可查询盘明细")
    raw = _scope.normalize_account_name(ev.text)
    if not raw:
        return await bot.send("⚠️ 用法：模拟盘查询 <盘名>")
    acc = await _scope.resolve_account(name=raw, fallback_default=False)
    if acc is None and raw.isdigit():
        acc = await _db.PaperAccountRepo.get_by_id(int(raw))
        if acc is None:
            matches = await _db.PaperAccountRepo.list_by_origin_group(raw)
            if len(matches) == 1:
                acc = matches[0]
            elif len(matches) > 1:
                names = "、".join(a.name for a in matches)
                return await bot.send(f"ℹ️ 原群 {raw} 下有多个盘：{names}。请用盘名再查。")
    if acc is None:
        return await bot.send(_scope.not_opened_message(name=raw))
    account_id: int = acc.id or 0
    positions = await _cross.query_positions(account_id)
    snap = await _cross.query_latest_snapshot(account_id)
    lines: list[str] = [
        f"【模拟盘「{acc.name}」】",
        f"策略: {acc.strategy_id}  模式: {acc.mode}  状态: {'🟢' if acc.enabled else '🔴'}",
        f"现金: {acc.cash:,.0f}  初始: {acc.initial_cash:,.0f}",
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
