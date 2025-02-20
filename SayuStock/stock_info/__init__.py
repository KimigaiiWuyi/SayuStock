from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from .draw_info import draw_info_img

sv_stock_info = SV('大盘概览')
sv_my_stock = SV('我的自选')


@sv_stock_info.on_fullmatch(('大盘概览', '大盘概况'))
async def send_stock_info(bot: Bot, ev: Event):
    logger.info('[SayuStock] 开始执行[大盘概览]')
    im = await draw_info_img()
    await bot.send(im)


@sv_my_stock.on_fullmatch(('我的自选'))
async def send_my_stock(bot: Bot, ev: Event):
    logger.info('[SayuStock] 开始执行[我的自选]')
    await bot.send('暂未开放')


# 每日晚上十一点保存当天数据
@scheduler.scheduled_job('cron', hour=23, minute=0)
async def save_data_sayustock():
    await draw_info_img(is_save=True)
