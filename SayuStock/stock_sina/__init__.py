from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .gen_image import get_sina_pe_compare

sv_stock_sina = SV("市盈率对比")


@sv_stock_sina.on_prefix(("市盈率对比"))
async def send_stock_info(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[市盈率对比]")
    im = await get_sina_pe_compare(ev.text.strip())
    await bot.send(im)
