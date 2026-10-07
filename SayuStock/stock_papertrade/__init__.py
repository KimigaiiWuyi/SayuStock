"""模拟盘触发器层入口。

初始化流程：
1. 注册 ``ai_alias`` 路由 + ``ai_entity`` 知识库（KB PAPERTRADE_GUIDE.md）
2. 显式 import 子模块以触发 @sv_*.on_* decorator
   —— GS 框架的 ``load_dir_plugins`` 只 import 各子目录 ``__init__.py``，
     不递归加载兄弟文件，所以这里必须显式 import 才能让装饰器生效。

模块分工：
- ``sv.py``: SV 集中处（``sv_papertrade`` pm=3、``sv_papertrade_watchlist`` pm=6、
  ``sv_papertrade_admin`` pm=0）
- ``permissions.py``: 权限校验 helpers（``user_pm_level`` / ``check_admin``）
- ``commands.py``: 业务命令（``sv_papertrade`` 建盘/策略/订阅；``sv_papertrade_watchlist`` 自选图）
- ``admin.py``: master-only 压测 / 清库命令（``sv_papertrade_admin`` 注册）
- ``account_scope.py``: 盘名解析 / 账户解析 / 写入授权
- ``broadcast.py``: 一个盘 → 多个群的成交播报扇出
- ``strategies/``: 策略注册表；每个策略通过提示词注入 / 候选池偏好 / 数据库硬闸
  三个生效点影响真实行为（见 ``strategies/base.py`` 的说明）
- 其它兄弟文件（ai_tools / db / cross_group / indicators / matcher / render /
  strategy / candidate_pool / trading_calendar）按需导入。
"""

from pathlib import Path

from gsuid_core.logger import logger
from gsuid_core.server import on_core_start
from gsuid_core.ai_core.models import KnowledgeBase
from gsuid_core.ai_core.register import ai_alias, ai_entity

# ── ai_alias 路由 ────────────────────────────────────────────────
ai_alias(
    "papertrade",
    ["模拟盘", "虚拟盘", "模拟炒股"],
    scope="SayuStock",
)
ai_alias(
    "papertrade_setup",
    ["模拟盘初始化", "建模拟盘"],
    scope="SayuStock",
)
ai_alias(
    "papertrade_query",
    ["模拟盘查看", "模拟盘自选", "模拟盘持仓", "模拟盘收益", "模拟盘记录", "模拟盘排行", "模拟盘列表"],
    scope="SayuStock",
)
ai_alias(
    "papertrade_manage",
    ["模拟盘创建", "模拟盘删除", "模拟盘改名", "模拟盘策略", "模拟盘推送"],
    scope="SayuStock",
)

# ── 知识库注册 ────────────────────────────────────────────────
GUIDE_PATH: Path = Path(__file__).parent / "PAPERTRADE_GUIDE.md"


def _register_papertrade_kb() -> None:
    """注册 ``PAPERTRADE_GUIDE.md`` 作为 persona 知识库。

    该函数只在模块导入时跑一次；失败也不会 raise，仅 logger.exception。
    """
    if not GUIDE_PATH.exists():
        logger.warning(f"[SayuStock][PaperTrade] PAPERTRADE_GUIDE.md 不存在: {GUIDE_PATH}")
        return
    try:
        content: str = GUIDE_PATH.read_text(encoding="utf-8")
        ai_entity(
            KnowledgeBase(
                id="sayustock_papertrade_guide",
                plugin="SayuStock",
                title="SayuStock 模拟盘 · 早柚人格操作指南",
                content=content,
                tags=[
                    "模拟盘",
                    "虚拟盘",
                    "SayuStock",
                    "stock_agent",
                    "papertrade",
                ],
                source="plugin",
            )
        )
        logger.info("[SayuStock][PaperTrade] PAPERTRADE_GUIDE 知识库已注册")
    except Exception as e:
        logger.exception(f"[SayuStock][PaperTrade] 知识库注册失败: {e}")


_register_papertrade_kb()


# ── 周期触发前置门（recurring gate）注册 ─────────────────────────
def _register_recurring_gates() -> None:
    """把 A 股交易日历注册为 Kanban 周期触发的前置门。

    效果：节假日/周末/非交易时段 cron 到点时，框架在克隆实例树**之前**
    就静默跳过——不派能力代理、不消耗 LLM token（此前 LLM 会被叫醒一句
    "今天不开盘"再睡回去，周六一天白烧十几次 token）。

    gate 按 agent_profile 注册：
      - decision / pool_refresh → 交易日 + 交易时段（9:30-11:30 / 13:00-15:00）
      - snapshot → 仅要求交易日（15:05 收盘后写快照，不在交易时段内）
      - reporter（月报）→ 不设门，任何日子都可出报告

    一律挂 **async** 版（``*_async``）：它们先拉上证指数分时自证"今天到底开没
    开市"，再走同步判定。同步判定只读缓存，而缓存由启动钩子 + 每次 gate 触发
    刷新，硬编码假期表仅作离线兜底。

    旧版框架无 register_recurring_gate 时降级为无门（行为同旧版）。
    """
    try:
        from gsuid_core.ai_core.planning.recurring import register_recurring_gate
    except ImportError:
        logger.warning("[SayuStock][PaperTrade] 框架不支持 recurring gate（版本过旧），跳过注册")
        return
    from . import strategies as _pt_strategies
    from .trading_calendar import is_trading_day_async, should_run_papertrade_async

    for s in _pt_strategies.decision_profiles():
        register_recurring_gate(s.agent_profile, should_run_papertrade_async)
    register_recurring_gate("papertrade_pool_refresh_agent", should_run_papertrade_async)
    register_recurring_gate("papertrade_snapshot_agent", is_trading_day_async)
    logger.info("[SayuStock][PaperTrade] recurring gate 已按策略注册表挂上（async 自证版）")


_register_recurring_gates()


# ── 启动自愈：刷交易日历 + 清假期脏成交 ───────────────────────────
@on_core_start()
async def _papertrade_holiday_boot() -> None:
    """核心起来后跑一次：刷新权威交易日历，并清掉落在非交易日的成交。

    挂 ``on_core_start``（WS 已开始服务）而不是 ``on_core_start_before``：
    两步都要打行情接口，不该阻塞启动。异常只记日志，绝不让模拟盘拖挂 core。

    开头那行 ``booting`` 是**故意**的：它是"这个钩子到底有没有被调用"的第一手
    证据。Core 那边 ``on_core_start`` 是 ``create_task`` 出来的后台任务，不阻塞
    启动日志；没有这行就分不清"钩子没跑"和"跑了但没找到脏数据"。
    """
    logger.info("[SayuStock][PaperTrade] 启动自愈开始：刷交易日历 → 清假期脏成交")
    from .trading_calendar import refresh_intraday, refresh_daily_calendar

    try:
        closed = await refresh_daily_calendar()
        logger.info(
            "[SayuStock][PaperTrade] 权威休市表刷新："
            + (f"成功，{len(closed)} 个休市工作日" if closed else "失败（将退回缓存/兜底表）")
        )
    except Exception as e:
        logger.warning(f"[SayuStock][PaperTrade] 交易日历刷新异常（沿用缓存/兜底表）: {e}")

    try:
        await refresh_intraday()
    except Exception as e:
        logger.warning(f"[SayuStock][PaperTrade] 分时自证异常（沿用缓存）: {e}")

    try:
        from .holiday_heal import heal_holiday_trades

        summary = await heal_holiday_trades()
        logger.info(
            "[SayuStock][PaperTrade] 启动自愈结束："
            f"删除流水 {summary['deleted_trades']} 笔 / 决策 {summary['deleted_decisions']} 条，"
            f"跳过原因={summary['skipped'] or '无'}"
        )
    except Exception as e:
        logger.exception(f"[SayuStock][PaperTrade] 假期成交自愈异常，账本保持原样: {e}")


# ── SV 实例 + 子模块导入触发装饰器 ───────────────────────────────
from . import (  # noqa: E402,F401
    db,
    admin,
    ai_tools,
    commands,
    holiday_heal,  # noqa: E402,F401
)
from .sv import sv_papertrade, sv_papertrade_admin, sv_papertrade_watchlist  # noqa: E402,F401
from .admin import (  # noqa: E402,F401
    send_dry_run,
    send_clear_all,
    send_heal_ledger,
    send_heal_holiday,
)

# 兼容旧 import 路径：业务命令从 commands 模块再 re-export 出去
from .commands import (  # noqa: E402,F401
    send_pnl,
    send_view,
    send_records,
    send_holdings,
    send_leaderboard,
    send_query_group,
    send_account_list,
    send_init_command,
    send_broadcast_add,
    send_strategy_list,
    send_broadcast_list,
    send_create_account,
    send_delete_account,
    send_rename_account,
    send_toggle_account,
    send_switch_strategy,
    send_broadcast_remove,
)
