import random
import asyncio
from datetime import datetime
from typing import Dict, Union

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.aps import scheduler
from gsuid_core.logger import logger
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.database.models import Subscribe

from ..utils.request import get_news, clean_news

sv_stock_subscribe = SV('订阅新闻', pm=2, area='GROUP')

TASK_NAME = '雪球新闻订阅'


@sv_stock_subscribe.on_fullmatch(('订阅雪球新闻', '订阅雪球热点'))
async def send_add_subscribe_info(bot: Bot, ev: Event):
    logger.info('✅ [SayuStock] 开始执行[订阅新闻]')
    new = await get_news()
    if isinstance(new, int):
        logger.error(f'[SayuStock] 订阅新闻失败, 取消发送, 错误码：{new}!')
        return await bot.send(f'❌ [SayuStock] 订阅新闻失败！错误码：{new}!')

    await gs_subscribe.add_subscribe(
        'session',
        TASK_NAME,
        ev,
        extra_message=str(new[0]),
    )
    await bot.send('✅ [SayuStock] 订阅雪球新闻成功！')


@sv_stock_subscribe.on_fullmatch(('取消订阅雪球新闻', '取消订阅雪球热点'))
async def send_delete_subscribe_info(bot: Bot, ev: Event):
    logger.info('✅ [SayuStock] 开始执行[取消订阅新闻]')
    await gs_subscribe.delete_subscribe('session', TASK_NAME, ev)
    await bot.send('✅ [SayuStock] 取消订阅雪球新闻成功！')


# 每隔十分钟检查一次订阅
@scheduler.scheduled_job('cron', minute='1-59/5')
async def send_subscribe_info():
    await asyncio.sleep(15 + random.random() * 10)
    datas = await gs_subscribe.get_subscribe(TASK_NAME)
    if datas:
        news = await get_news()
        if isinstance(news, int):
            logger.error(
                f'[SayuStock] 发送订阅新闻失败, 取消发送, 错误码：{news}!'
            )
            return

        for subscribe in datas:
            # 发送
            for new in reversed(news[1]['items']):
                em = subscribe.extra_message
                if em and new['id'] > int(em) and new['mark'] in [1]:
                    dt_local = datetime.fromtimestamp(
                        new['created_at'] / 1000
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    await subscribe.send(
                        f"【{dt_local}】雪球7x24消息\n{new['text']}"
                    )
                    await asyncio.sleep(2 + random.random() * 3)

            # 更新max_id
            opt: Dict[str, Union[str, int, None]] = {
                'bot_id': subscribe.bot_id,
                'task_name': TASK_NAME,
            }

            upd = {}
            for i in [
                'user_id',
                'bot_id',
                'group_id',
                'bot_self_id',
                'user_type',
            ]:
                if i not in opt:
                    opt[i] = subscribe.__getattribute__(i)

            upd['extra_message'] = str(news[0])
            await Subscribe.update_data_by_data(
                opt,
                upd,
            )


# 每天凌晨零点，清空NEWS
@scheduler.scheduled_job('cron', hour=0, minute=0)
async def clean_news_data():
    logger.info('[SayuStock] 开始执行[清空新闻缓存]')
    await clean_news()
    logger.success('[SayuStock] 清空新闻缓存成功!')
