"""宏观定调子包：宏观事件表 + 宏观知识库 / 技能 + 宏观复核心跳。

模块分工：
- ``repo.py``       宏观事件仓储 + LLM 入参归一（纯函数可单测）
- ``ai_tools.py``   ``macro_event_list`` / ``macro_event_refresh_due`` / ``macro_event_upsert``
- ``prompts.py``    宏观三问 / 三档仓位 / 速查表（模拟盘、持仓分析、研究代理共用）
- ``knowledge.py``  ``skills/macro-regime-analysis/references`` → 知识库小节实体 + ai_skill
- ``agent.py``      ``macro_event_agent`` 能力代理
- ``heartbeat.py``  APScheduler 定时复核（08:40 / 12:40 / 20:40）
- ``commands.py``   「宏观事件」「宏观事件刷新」命令

GsCore 只 import 子包 ``__init__``，兄弟模块必须在这里显式 import 才会注册。
"""

from gsuid_core.ai_core.register import ai_alias

ai_alias(
    "macro_event",
    ["宏观事件", "宏观大事", "关税战进展", "宏观定调", "宏观档位"],
    scope="SayuStock",
)

from .knowledge import register_macro_skill, register_macro_knowledge  # noqa: E402

register_macro_knowledge()
register_macro_skill()

from .agent import register_macro_event_agent  # noqa: E402

register_macro_event_agent()

from . import ai_tools, commands, heartbeat  # noqa: E402,F401
from .sv import sv_macro  # noqa: E402,F401
