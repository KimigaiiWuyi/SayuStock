"""AI 模拟盘触发器层入口。

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
    ["AI操盘", "AI模拟盘", "虚拟盘", "AI模拟", "模拟盘", "模拟炒股"],
    scope="SayuStock",
)
ai_alias(
    "papertrade_setup",
    ["AI操盘初始化", "AI模拟盘初始化", "建模拟盘"],
    scope="SayuStock",
)
ai_alias(
    "papertrade_query",
    ["AI操盘查看", "AI操盘收益", "AI操盘记录", "AI操盘排行"],
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
                title="SayuStock AI 模拟盘 · 早柚人格操作指南",
                content=content,
                tags=[
                    "AI操盘",
                    "AI模拟盘",
                    "虚拟盘",
                    "模拟盘",
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
