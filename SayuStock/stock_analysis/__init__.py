"""股票分析：技术分析 / 股票卡片 / 自动选股 / 组合体检。"""

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .service import (
    run_stock_card,
    run_auto_screener,
    run_portfolio_check,
    run_technical_analysis,
)
from ..utils.database.models import SsBind

sv_analysis = SV("股票分析")


@sv_analysis.on_command(
    ("技术分析",),
    block=True,
    to_ai="""对股票/ETF 做技术面分析（评分+趋势/动量/量能/位置+信号风险）。

    当用户说"技术分析"、"技术面怎么样"、"帮我看看技术面"时调用。
    默认日K，可指定周期（日k/周k/月k/60k，勿用单字日/周/月）。

    Args:
        text: "[周期] 代码或名称"
              - 技术分析 茅台
              - 技术分析 日k 600519
              - 技术分析 周k 证券ETF
              - 技术分析 60k 000001
    """,
)
async def send_technical_analysis(bot: Bot, ev: Event):
    logger.info("[SayuStock] 技术分析")
    text = ev.text.strip()
    if text.startswith("技术分析"):
        text = text[len("技术分析") :].strip()
    im = await run_technical_analysis(text)
    await bot.send(im)


@sv_analysis.on_command(
    ("股票卡片", "交易卡片", "交易卡"),
    block=True,
    to_ai="""生成单股一页纸交易卡片（盘口+技术面+财务快照+行业）。

    当用户说"股票卡片"、"交易卡"、"一页纸"、"帮我出张卡片"时调用。

    Args:
        text: 股票代码或名称，例如 "茅台"、"600519"、"证券ETF"
    """,
)
async def send_stock_card(bot: Bot, ev: Event):
    logger.info("[SayuStock] 股票卡片")
    text = ev.text.strip()
    for p in ("股票卡片", "交易卡片", "交易卡"):
        if text.startswith(p):
            text = text[len(p) :].strip()
            break
    im = await run_stock_card(text)
    await bot.send(im)


@sv_analysis.on_command(
    ("自动选股",),
    block=True,
    to_ai="""按条件自动筛选股票（市值/PE/涨跌幅/换手/量比/行业/概念）。

    当用户说"自动选股"、"帮我选股"、"筛选股票"时调用。
    未指定行业/概念时，股票池为沪深A按市值排序的前约2000只（非全市场）。
    行业与概念请二选一。

    Args:
        text: 条件表达式，空格分隔 AND。
              示例：
              - 自动选股 市值50-200 PE<30 涨跌幅>2
              - 自动选股 行业 半导体 换手>1 量比>1.2
              - 自动选股 概念 人工智能 涨跌幅>3
              支持字段：涨跌幅/涨幅/市盈率PE/市值(亿)/换手/量比/成交额/价格
              支持：行业 名称、概念 名称（不可同时）
    """,
)
async def send_auto_screener(bot: Bot, ev: Event):
    logger.info("[SayuStock] 自动选股")
    text = ev.text.strip()
    if text.startswith("自动选股"):
        text = text[len("自动选股") :].strip()
    im = await run_auto_screener(text)
    await bot.send(im)


@sv_analysis.on_command(
    ("组合体检", "行业集中度"),
    block=True,
    to_ai="""分析自选/给定组合的行业集中度风险（HHI、Top行业占比）。

    当用户问"组合集中吗"、"行业会不会太集中"、"组合体检"时调用。
    不传参数则用当前用户自选（等权）。

    Args:
        text: 可选，空格分隔的股票代码/名称；为空则读自选
    """,
)
async def send_portfolio_check(bot: Bot, ev: Event):
    logger.info("[SayuStock] 组合体检")
    text = ev.text.strip()
    for p in ("组合体检", "行业集中度"):
        if text.startswith(p):
            text = text[len(p) :].strip()
            break

    user_codes: list[str] | None = None
    if not text:
        user_id = ev.at if ev.at else ev.user_id
        uid = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)
        if uid:
            # convert_list 在 service 层统一做一次
            user_codes = list(uid)

    im = await run_portfolio_check(text, user_codes=user_codes)
    await bot.send(im)
