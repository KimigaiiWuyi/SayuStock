from time import monotonic
from itertools import count
from collections import OrderedDict
from dataclasses import dataclass

from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .draw_info import draw_info_img
from .draw_future import draw_future_img
from .etf_compare import (
    MAX_COMPARE,
    SearchResult,
    load_baskets,
    resolve_target,
    expand_fund_theme,
    search_listed_funds,
)
from .draw_my_info import draw_my_stock_img
from .draw_fund_info import draw_fund_info
from .draw_etf_compare import draw_etf_search, draw_etf_comparison
from ..utils.time_range import now_bjt
from ..utils.market.adapters.etf_basket import is_etf_code, fund_quote_symbol

sv_stock_info = SV("大盘概览")
sv_my_stock = SV("我的自选")
sv_fund_info = SV("基金持仓信息")
sv_etf_compare = SV("场内基金对比")

_SEARCH_TTL = 600.0
_SEARCH_LIMIT = 128
_SEARCH_ORDER = count()


@dataclass(frozen=True, slots=True)
class _SearchContext:
    sequence: int
    expires_at: float
    result: SearchResult


_SEARCHES: OrderedDict[tuple[str, str], _SearchContext] = OrderedDict()


def _fund_search_key(ev: Event) -> tuple[str, str]:
    return ev.session_id, ev.user_id


def _recent_fund_search(ev: Event) -> SearchResult | None:
    key = _fund_search_key(ev)
    recent = _SEARCHES[key] if key in _SEARCHES else None
    if recent is not None and monotonic() >= recent.expires_at:
        _SEARCHES.pop(key, None)
        return None
    return recent.result if recent is not None else None


@sv_etf_compare.on_command(("场内找股", "ETF找股"), block=True)
async def send_listed_fund_search(bot: Bot, ev: Event) -> None:
    """按目标股票查场内基金，展示当天申赎篮子的主题权重。"""
    query = ev.text.strip()
    if not query:
        await bot.send("用法：ETF找股 工业富联")
        return
    sequence = next(_SEARCH_ORDER)
    stock = await resolve_target(query)
    if stock is None:
        await bot.send("未识别到沪深 A 股，请输入股票名称或六位代码。")
        return
    result = await search_listed_funds(stock, now_bjt().date())
    if isinstance(result, str):
        await bot.send(result)
        return
    message = await draw_etf_search(result)
    key = _fund_search_key(ev)
    if key in _SEARCHES and sequence < _SEARCHES[key].sequence:
        return
    _SEARCHES[key] = _SearchContext(sequence, monotonic() + _SEARCH_TTL, result)
    _SEARCHES.move_to_end(key)
    if len(_SEARCHES) > _SEARCH_LIMIT:
        _SEARCHES.popitem(last=False)
    await bot.send(message)


@sv_etf_compare.on_command(("ETF展开", "展开"), block=False)
async def send_listed_fund_theme(bot: Bot, ev: Event) -> None:
    """对比当前成员最近一次找股结果中指定主题的 ETF。"""
    explicit = ev.command == "ETF展开"
    theme = "".join(ev.text.split())
    if not theme:
        if explicit:
            await bot.send("用法：ETF展开 食品饮料")
        return
    search = _recent_fund_search(ev)
    if search is None:
        if explicit:
            await bot.send("请先发送「ETF找股 股票名称」，再展开结果里的主题。")
        return
    result = await expand_fund_theme(search, theme, now_bjt().date())
    if isinstance(result, str):
        if explicit:
            await bot.send(result)
        return
    await bot.send(await draw_etf_comparison(result, f"{theme} ETF 申赎篮子对比", search.stock))


@sv_etf_compare.on_command(("ETF对比", "etf对比"), block=True)
async def send_etf_comparison(bot: Bot, ev: Event) -> None:
    """按代码对比两至四只 ETF，可指定一只关注股票。"""
    raw_codes, _, query = ev.text.partition("关注")
    codes: list[str] = []
    for token in raw_codes.lower().replace(",", " ").replace("，", " ").split():
        code = token[-6:]
        if not is_etf_code(code) or token not in (code, fund_quote_symbol(code)):
            await bot.send(f"「{token}」不是有效的沪深 ETF 代码，请检查代码和交易所前缀。")
            return
        if code not in codes:
            codes.append(code)
    if not 2 <= len(codes) <= MAX_COMPARE:
        await bot.send("用法：ETF对比 515170 516900 159843 159862，可追加「关注 贵州茅台」。")
        return
    stock = await resolve_target(query) if query.strip() else None
    if query.strip() and stock is None:
        await bot.send("未识别到关注的沪深 A 股，请检查名称或代码。")
        return
    result = await load_baskets(codes, day=now_bjt().date())
    await bot.send(await draw_etf_comparison(result, "ETF 申赎篮子对比", stock))


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
async def send_fund_info(bot: Bot, ev: Event) -> None:
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
async def send_stock_info(bot: Bot, ev: Event) -> None:
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
async def send_my_stock(bot: Bot, ev: Event) -> None:
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
async def send_future_stock(bot: Bot, ev: Event) -> None:
    logger.info("[SayuStock] 开始执行[全天候板块]")
    await bot.send(await draw_future_img())


# 每日晚上十一点保存当天数据
@scheduler.scheduled_job("cron", hour=23, minute=0)
async def save_data_sayustock() -> None:
    await draw_info_img(is_save=True)
