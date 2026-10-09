from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .draw_ipo import draw_ipo_calendar_img

sv_ipo_calendar = SV("IPO日历")


@sv_ipo_calendar.on_command(
    ("IPO日历", "ipo日历", "新股日历", "IPO", "ipo"),
    block=True,
    to_ai="""查看A股/港股/美股IPO日历

    当用户询问最近有什么新股上市、IPO日历、打新日历、"下周哪些公司上市"、
    "最近的新股"、"港股IPO"、"美股IPO"、"什么时候申购"时调用。
    显示 T-2 至 T+7 窗口内的新股申购与上市安排，彩色横条标注阶段
    （申购/待上市/已上市/已申报），名字旁标注所属市场（A股/港股/美股），
    已上市的显示发行价与首日表现。

    Args:
        text: 可选市场筛选：A股/港股/美股（可任选其一或组合），例如 "IPO日历 美股"；
        留空则显示全部市场
    """,
)
async def send_ipo_calendar(bot: Bot, ev: Event) -> None:
    logger.info("[SayuStock] 开始执行[IPO日历]")
    im = await draw_ipo_calendar_img(ev.text.strip())
    await bot.send(im)
