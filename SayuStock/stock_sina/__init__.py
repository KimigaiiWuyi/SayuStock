from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .eastmoney_value import get_eastmoney_pepb_compare

sv_stock_sina = SV("市盈市净对比工具")


@sv_stock_sina.on_prefix(
    ("市盈率对比"),
    to_ai="""对比多只股票的市盈率(PE)历史走势，支持输入板块名称自动展开为成分股对比。

    当用户询问几只股票的市盈率对比、PE对比、估值对比、
    "帮我对比一下茅台和五粮液的市盈率"、"这几只股票PE谁高"时调用。
    也支持直接输入板块名称（如"证券"、"白酒"），系统会自动获取该板块内前13只成分股并生成PE历史走势对比图。
    数据来源为东方财富，生成PE历史走势对比图，并向大模型注入可分析的文本摘要。

    Args:
        text: 股票代码或名称列表，以空格或逗号分隔
              例如 "600000 000001" 或 "贵州茅台,五粮液" 或 "证券ETF 白酒ETF"
              也支持板块名称：例如 "证券"，自动展开为板块内前13只成分股的PE对比
    """,
)
async def send_stock_PE_info(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[市盈率对比]")
    im = await get_eastmoney_pepb_compare(
        ev.text.strip(),
        "pe",
    )
    await bot.send(im)


@sv_stock_sina.on_prefix(
    ("市净率对比"),
    to_ai="""对比多只股票的市净率(PB)历史走势，支持输入板块名称自动展开为成分股对比。

    当用户询问几只股票的市净率对比、PB对比、净资产估值对比、
    "帮我对比一下这几只股票的PB"、"谁的市净率更低"时调用。
    也支持直接输入板块名称（如"证券"、"白酒"），系统会自动获取该板块内前13只成分股并生成PB历史走势对比图。
    数据来源为东方财富，生成PB历史走势对比图，并向大模型注入可分析的文本摘要。

    Args:
        text: 股票代码或名称列表，以空格或逗号分隔
              例如 "600000 000001" 或 "贵州茅台,五粮液" 或 "证券ETF 白酒ETF"
              也支持板块名称：例如 "证券"，自动展开为板块内前13只成分股的PB对比
    """,
)
async def send_stock_PB_info(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[市净率对比]")
    im = await get_eastmoney_pepb_compare(
        ev.text.strip(),
        "pb",
    )
    await bot.send(im)
