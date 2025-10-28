from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.logger import logger

from .draw_ai_map import draw_ai_kline_with_forecast

sv_stock_kronos = SV('模型预测')


@sv_stock_kronos.on_prefix(('模型预测', 'ai预测', 'AI预测'))
async def send_stock_kronos(bot: Bot, ev: Event):
    logger.info('[SayuStock] 开始执行[模型预测]')
    im = await draw_ai_kline_with_forecast(ev.text.strip())
    await bot.send(im)
