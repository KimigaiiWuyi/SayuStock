"""模拟盘触发器层入口。

初始化流程：
1. 注册 ``ai_alias`` 路由 + ``ai_entity`` 知识库（KB PAPERTRADE_GUIDE.md）
2. 显式 import 子模块以触发 @sv_*.on_* decorator
   —— GS 框架的 ``load_dir_plugins`` 只 import 各子目录 ``__init__.py``，
     不递归加载兄弟文件，所以这里必须显式 import 才能让装饰器生效。

模块分工：
- ``sv.py``: 所有 SV 实例集中处（``sv_papertrade`` pm=3、``sv_papertrade_admin`` pm=0）
- ``permissions.py``: 权限校验 helpers（``user_pm_level`` / ``check_admin``）
- ``commands.py``: 6 个业务命令（``sv_papertrade`` 注册）
- ``admin.py``: 1 个 master-only 压测命令（``sv_papertrade_admin`` 注册）
- 其它兄弟文件（ai_tools / db / cross_group / indicators / matcher / render /
  strategy / candidate_pool / trading_calendar）按需导入。
"""

# pyright/basedpyright 文件级指令 —— 仅作用于本文件。
# - gsuid_core.* 根包在本文件解析路径下不可达
# - @with_session 等装饰器动态隐藏 session 形参，基于 pyright 看不到该变换
# - @sv_*.on_* 装饰器接收未注解的 func
# - framework.Event / Event.user_pm 联级未注解
# 上游这些是已知限制，不是本文件代码错误。
# pyright: reportMissingImports=false, reportImportCycles=false, reportCallIssue=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUntypedFunctionDecorator=false, reportUnusedParameter=false, reportUnusedImport=false, reportImplicitStringConcatenation=false

from pathlib import Path

from gsuid_core.logger import logger
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
    ["模拟盘查看", "模拟盘收益", "模拟盘记录", "模拟盘排行"],
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

    旧版框架无 register_recurring_gate 时降级为无门（行为同旧版）。
    """
    try:
        from gsuid_core.ai_core.planning.recurring import register_recurring_gate
    except ImportError:
        logger.warning("[SayuStock][PaperTrade] 框架不支持 recurring gate（版本过旧），跳过注册")
        return
    from .trading_calendar import should_run_papertrade, is_a_share_trading_day

    register_recurring_gate("papertrade_decision_agent", should_run_papertrade)
    register_recurring_gate("papertrade_pool_refresh_agent", should_run_papertrade)
    register_recurring_gate("papertrade_snapshot_agent", is_a_share_trading_day)
    logger.info("[SayuStock][PaperTrade] A 股交易日历 recurring gate 已注册（decision/pool_refresh/snapshot）")


_register_recurring_gates()

# ── SV 实例 + 子模块导入触发装饰器 ───────────────────────────────
from . import db, admin, ai_tools, commands  # noqa: E402,F401
from .sv import sv_papertrade, sv_papertrade_admin  # noqa: E402,F401
from .admin import send_dry_run, send_clear_all  # noqa: E402,F401

# 兼容旧 import 路径：业务命令从 commands 模块再 re-export 出去
from .commands import (  # noqa: E402,F401
    send_pnl,
    send_view,
    send_records,
    send_leaderboard,
    send_query_group,
    send_init_command,
)
