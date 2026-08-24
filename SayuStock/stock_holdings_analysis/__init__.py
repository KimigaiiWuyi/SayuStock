"""持仓分析命令：自选/手填标的 → 分析 agent → render_agent → 出图。

非 @ai_tools；用户命令入口。
"""

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .service import run_holdings_analysis_command

sv_holdings_analysis = SV("持仓分析")


@sv_holdings_analysis.on_command(
    ("持仓分析",),
    block=True,
    to_ai="""对用户自选股或指定股票做多维持仓综合分析并出图。

    当用户说「持仓分析」「帮我分析一下自选/持仓」「自选股综合评级」时调用。
    可无参数（读我的自选，最多 8 只）；或在 text 中传代码/名称列表（空格分隔，最多 8）。
    每用户每天成功出图限 1 次（配置 holdings_analysis_unlimited_users 的 user_id 不限）。
    本工具会触发分析 agent + render_agent，耗时较长；结果会 @ 发起人。

    Args:
        text: 可选，股票代码或名称，空格/逗号分隔；留空则用「我的自选」
    """,
)
async def send_holdings_analysis(bot: Bot, ev: Event) -> None:
    logger.info("[SayuStock] 开始执行[持仓分析]")
    await run_holdings_analysis_command(bot, ev)
