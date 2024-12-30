from SayuStock.utils.constant import STOCK_SECTOR
from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from .get_cloudmap import render_image
from ..utils.resource_path import DATA_PATH

sv_stock_cloudmap = SV("大盘云图")


# 每日零点二十删除全部缓存数据
@scheduler.scheduled_job('cron', hour=0, minute=20)
async def delete_all_data():
    logger.info("[SayuStock] 开始执行[删除全部缓存数据]")
    for i in DATA_PATH.iterdir():
        if i.is_file():
            i.unlink()
    logger.success("[SayuStock] [删除全部缓存数据] 执行完成！")


@sv_stock_cloudmap.on_command(("大盘云图"))
async def send_cloudmap_img(bot: Bot, ev: Event):
    logger.info("开始执行[大盘云图]")
    im = await render_image(ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(("板块云图", "行业云图"))
async def send_typemap_img(bot: Bot, ev: Event):
    logger.info("开始执行[板块云图]")
    im = await render_image('沪深A', ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(("概念云图", "概念板块云图"))
async def send_gn_img(bot: Bot, ev: Event):
    logger.info("开始执行[概念云图]")
    im = await render_image(ev.text.strip(), ev.text.strip())
    await bot.send(im)

@sv_stock_cloudmap.on_command(("个股数据", "个股"))
async def send_stock_img(bot: Bot, ev: Event):
    logger.info("开始执行[个股数据]")
    im = await render_image(ev.text.strip(),  STOCK_SECTOR)
    await bot.send(im)