import datetime
from datetime import timedelta

from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .get_cloudmap import render_image
from ..utils.resource_path import DATA_PATH
from ..stock_config.stock_config import STOCK_CONFIG

sv_stock_cloudmap = SV("大盘云图")


# 每日零点二十清理过期缓存数据，避免每天全量清空影响长周期缓存。
@scheduler.scheduled_job("cron", hour=0, minute=20)
async def delete_all_data():
    retention_days = int(STOCK_CONFIG.get_config("stock_cache_retention_days").data)
    expired_before = datetime.datetime.now() - timedelta(days=retention_days)
    logger.info(f"[SayuStock] 开始执行[清理{retention_days}天前缓存数据]")
    for cache_file in DATA_PATH.iterdir():
        if cache_file.is_file():
            file_mod_time = datetime.datetime.fromtimestamp(cache_file.stat().st_mtime)
            if file_mod_time < expired_before:
                cache_file.unlink()

    logger.success("[SayuStock] [清理过期缓存数据] 执行完成！")


@sv_stock_cloudmap.on_command(
    ("大盘云图"),
    to_ai="""查看A股大盘行业板块涨跌分布云图

    当用户询问大盘行情、今日市场整体表现、行业板块涨跌分布、大盘热力图、
    "帮我看看大盘"、"今天市场怎么样"、"行业板块涨跌"时调用。

    Args:
        text: 可选的板块筛选条件，留空显示全部行业板块的大盘云图。
              例如 "" 或 "医药" 或 "科技"
    """,
)
async def send_cloudmap_img(bot: Bot, ev: Event):
    logger.info("开始执行[大盘云图]")
    im = await render_image("大盘云图", ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(
    ("板块云图", "行业云图", "行业板块"),
    to_ai="""查看行业板块涨跌分布云图

    当用户询问某个行业板块行情、行业板块云图、"帮我看看半导体板块"、
    "新能源板块怎么样"、"行业板块涨跌"时调用。

    Args:
        text: 行业板块名称，例如 "半导体"、"新能源"、"医药"、"白酒"
    """,
)
async def send_typemap_img(bot: Bot, ev: Event):
    logger.info("开始执行[板块云图]")
    im = await render_image("行业云图", ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(
    ("概念云图", "概念板块云图", "概念板块"),
    to_ai="""查看概念板块涨跌分布云图

    当用户询问某个概念板块行情、概念板块云图、"帮我看看人工智能概念"、
    "华为欧拉概念怎么样"、"概念板块涨跌"时调用。
    注意：不指定概念名称时会提示需要后跟概念类型。

    Args:
        text: 概念板块名称，例如 "华为欧拉"、"人工智能"、"机器人"、"芯片"
    """,
)
async def send_gn_img(bot: Bot, ev: Event):
    logger.info("开始执行[概念云图]")
    im = await render_image("概念云图", ev.text.strip())
    await bot.send(im)
