import random
import asyncio
from typing import Dict, Union, Optional
from datetime import datetime
from collections import deque

from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.database.models import Subscribe

from ..utils.request import get_news, clean_news

sv_stock_subscribe = SV("订阅新闻", pm=2, area="GROUP")

TASK_NAME = "雪球新闻订阅"

# 进程内发送去重（不持久化，重启即清空，仅作安全网）
# 不同群会推送相同新闻，因此以 group_id 为 key
# 结构: group_id -> 最近发送过的 news id 队列（最多 50 条，超出自动驱逐最旧）
_SENT_HISTORY: Dict[str, deque] = {}
_SENT_HISTORY_MAX = 50


def _already_sent(group_id: Optional[str], news_id: int) -> bool:
    """检查该群最近是否已发送过这条新闻"""
    if not group_id:
        return False
    history = _SENT_HISTORY.get(group_id)
    if not history:
        return False
    return news_id in history


def _mark_sent(group_id: Optional[str], news_id: int) -> None:
    """记录该群已发送过这条新闻"""
    if not group_id:
        return
    history = _SENT_HISTORY.get(group_id)
    if history is None:
        history = deque(maxlen=_SENT_HISTORY_MAX)
        _SENT_HISTORY[group_id] = history
    history.append(news_id)


@sv_stock_subscribe.on_fullmatch(
    ("订阅雪球新闻", "订阅雪球热点"),
    to_ai="""订阅雪球7x24小时财经新闻推送

    当用户说"订阅新闻"、"开启新闻推送"、"订阅雪球热点"、
    "帮我订阅财经新闻"、"开启新闻提醒"时调用。
    订阅后会自动推送最新的雪球财经新闻。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_add_subscribe_info(bot: Bot, ev: Event):
    logger.info("✅ [SayuStock] 开始执行[订阅新闻]")
    new = await get_news()
    if isinstance(new, int):
        logger.error(f"[SayuStock] 订阅新闻失败, 取消发送, 错误码：{new}!")
        return await bot.send(f"❌ [SayuStock] 订阅新闻失败！错误码：{new}!")

    await gs_subscribe.add_subscribe(
        "session",
        TASK_NAME,
        ev,
        extra_message=str(new[0]),
    )
    await bot.send("✅ [SayuStock] 订阅雪球新闻成功！")


@sv_stock_subscribe.on_fullmatch(
    ("取消订阅雪球新闻", "取消订阅雪球热点"),
    to_ai="""取消订阅雪球财经新闻推送

    当用户说"取消订阅新闻"、"关闭新闻推送"、"取消雪球热点"、
    "不要再推送新闻了"、"关闭新闻提醒"时调用。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_delete_subscribe_info(bot: Bot, ev: Event):
    logger.info("✅ [SayuStock] 开始执行[取消订阅新闻]")
    await gs_subscribe.delete_subscribe("session", TASK_NAME, ev)
    await bot.send("✅ [SayuStock] 取消订阅雪球新闻成功！")


# 每隔十分钟检查一次订阅
@scheduler.scheduled_job("cron", minute="1-59/5")
async def send_subscribe_info():
    await asyncio.sleep(15 + random.random() * 10)
    datas = await gs_subscribe.get_subscribe(TASK_NAME)
    if datas:
        news = await get_news()
        if isinstance(news, int):
            logger.error(f"[SayuStock] 发送订阅新闻失败, 取消发送, 错误码：{news}!")
            return

        for subscribe in datas:
            # 用真正发送出去的最大 ID 作为水位线，
            # 避免被雪球撤回的新闻卡死导致下一轮重发
            sent_max_id: int = int(subscribe.extra_message or 0)

            # 发送
            for new in reversed(news[1]["items"]):
                em = subscribe.extra_message
                if em and new["id"] > int(em) and new["mark"] in [1]:
                    # 同一群内同一条新闻去重
                    if _already_sent(subscribe.group_id, new["id"]):
                        continue
                    dt_local = datetime.fromtimestamp(new["created_at"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    await subscribe.send(f"【{dt_local}】雪球7x24消息\n{new['text']}")
                    await asyncio.sleep(2 + random.random() * 3)
                    sent_max_id = max(sent_max_id, new["id"])
                    _mark_sent(subscribe.group_id, new["id"])

            # 更新max_id
            opt: Dict[str, Union[str, int, None]] = {
                "bot_id": subscribe.bot_id,
                "task_name": TASK_NAME,
            }

            upd = {}
            for i in [
                "user_id",
                "bot_id",
                "group_id",
                "bot_self_id",
                "user_type",
            ]:
                if i not in opt:
                    opt[i] = subscribe.__getattribute__(i)

            upd["extra_message"] = str(sent_max_id)
            await Subscribe.update_data_by_data(
                opt,
                upd,
            )


# 每天凌晨零点，清空NEWS
@scheduler.scheduled_job("cron", hour=0, minute=0)
async def clean_news_data():
    logger.info("[SayuStock] 开始执行[清空新闻缓存]")
    await clean_news()
    logger.success("[SayuStock] 清空新闻缓存成功!")
