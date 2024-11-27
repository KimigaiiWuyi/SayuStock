from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.logger import logger

from .get_cloudmap import render_image

sv_stock_cloudmap = SV("大盘云图")


@sv_stock_cloudmap.on_command(("大盘云图"))
async def send_cloudmap_img(bot: Bot, ev: Event):
    logger.info("开始执行[大盘云图]")
    im = await render_image(ev.text.strip())
    await bot.send(im)
