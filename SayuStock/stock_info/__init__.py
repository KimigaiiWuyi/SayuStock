from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .draw_info import draw_info_img
from .draw_future import draw_future_img
from .draw_my_info import draw_my_stock_img
from .draw_fund_info import draw_fund_info

sv_stock_info = SV("大盘概览")
sv_my_stock = SV("我的自选")
sv_fund_info = SV("基金持仓信息")


@sv_fund_info.on_command(
    ("基金持仓", "持仓分布"),
    block=True,
    to_ai="""查询基金持仓股票分布信息

    当用户询问某只基金买了什么股票、基金持仓、重仓股、基金持仓分布时调用。
    例如"帮我看看000001的持仓"、"沪深300ETF重仓了哪些股票"。

    Args:
        text: 基金代码或名称，例如 "000001"、"沪深300ETF"、"易方达蓝筹精选"
    """,
)
async def send_fund_info(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[基金持仓信息]")
    im = await draw_fund_info(ev.text.strip())
    await bot.send(im)


@sv_stock_info.on_fullmatch(
    ("大盘概览", "大盘概况"),
    to_ai="""查看A股大盘整体概览

    当用户询问今天大盘怎么样、A股行情、市场概况、"帮我看看大盘"、
    "今天市场整体表现如何"、"涨跌分布"时调用。
    包括主要指数行情、涨跌分布统计、成交额、领涨领跌行业板块等。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_stock_info(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[大盘概览]")
    im = await draw_info_img()
    await bot.send(im)


@sv_my_stock.on_fullmatch(
    ("我的自选", "我的持仓", "我的股票"),
    to_ai="""查看自选股当日行情概览

    当用户询问"我的股票今天怎么样"、"我的持仓"、"自选股表现"、
    "帮我看看我的股票"、"自选股涨跌"时调用。
    自动读取当前用户的自选股列表，显示每只股票的价格、涨跌幅、换手率、成交额。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_my_stock(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[我的自选]")
    await bot.send(await draw_my_stock_img(ev))


@sv_my_stock.on_fullmatch(
    ("全天候", "全天候板块"),
    to_ai="""查看全天候策略板块综合行情

    当用户询问全天候策略、全球市场概况、"帮我看看全球市场"、
    "大宗商品和国债怎么样"、"加密货币行情"、"全球股市指数"时调用。
    包括全球股市指数、大宗商品、国债收益率、外汇、加密货币等综合行情。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_future_stock(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[全天候板块]")
    await bot.send(await draw_future_img())


# 每日晚上十一点保存当天数据
@scheduler.scheduled_job("cron", hour=23, minute=0)
async def save_data_sayustock():
    await draw_info_img(is_save=True)
