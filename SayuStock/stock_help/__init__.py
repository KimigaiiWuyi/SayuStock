from PIL import Image

from gsuid_core.sv import SV, get_plugin_available_prefix
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

from .get_help import ICON, get_help

sv_stock_help = SV("SayuStock帮助", priority=1)


@sv_stock_help.on_fullmatch(("股票帮助", "SayuStock帮助"), block=True)
async def send_stock_help_img(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[股票帮助]")
    await bot.send(await get_help())


register_help(
    "SayuStock",
    f"{get_plugin_available_prefix('SayuStock')}帮助",
    Image.open(ICON),
)
