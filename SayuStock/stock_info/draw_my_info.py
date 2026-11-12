import asyncio
from dataclasses import replace

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..render import RenderError, render_html
from ..utils.utils import convert_list, number_to_chinese
from ..utils.market import Quote, MarketDataPort, get_market, is_market_error
from ..utils.warn_box import Failure
from ..render.card_widgets import watch_sparkline
from ..utils.my_stock_html import (
    SPARK_H,
    SPARK_W,
    WATCH_INDEXES,
    watchlist_width,
    watchlist_average,
    build_my_stock_html,
)
from ..utils.market.display import market_error_text
from ..utils.database.models import SsBind


async def _watch_index_quotes(market: MarketDataPort) -> list[Quote | None]:
    results = await market.quotes([code for _, code in WATCH_INDEXES])
    return [None if is_market_error(result) else result for result in results]


async def draw_my_stock_img(ev: Event) -> str | bytes:
    user_id = ev.at if ev.at else ev.user_id
    saved = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)
    if not saved:
        return "您还未添加自选呢~请输入 添加自选 查看帮助!"

    uid = convert_list(saved)
    market = get_market()
    index_quotes = await _watch_index_quotes(market)

    async def load(code: str) -> tuple[Quote, str, str | None] | Failure:
        query = code[4:] if code.startswith("VIX.") else code
        series = await market.intraday(query)
        q = None if is_market_error(series) else series.quote
        if q is None:
            result = await market.quote(query)
            if is_market_error(result):
                reason = market_error_text(series) if is_market_error(series) else market_error_text(result)
                return Failure(label=code, reason=reason)
            q = result
        if is_market_error(series):
            return q, "", None
        spark = watch_sparkline(replace(series, quote=q), q, width=SPARK_W, height=SPARK_H)
        return q, spark, series.provider if spark else None

    quotes: list[tuple[Quote, str]] = []
    failures: list[Failure] = []
    sparks: dict[str, str] = {}
    sources: list[str | None] = []
    for code, result in zip(uid, await asyncio.gather(*(load(code) for code in uid)), strict=True):
        if isinstance(result, Failure):
            failures.append(result)
            continue
        quote, spark, provider = result
        quotes.append((quote, code))
        if spark:
            sparks[code] = spark
            sources.append(provider)

    if failures:
        logger.warning(f"[SayuStock] 我的自选 {len(failures)}/{len(uid)} 只行情获取失败")
    saved_count = len(uid)
    _ai_return_my_stock(saved_count, quotes, failures)
    html = build_my_stock_html(
        quotes=quotes,
        index_quotes=index_quotes,
        saved_count=saved_count,
        sparklines=sparks,
        sources=sources,
        failures=failures,
    )
    try:
        return await render_html(html, width=watchlist_width(saved_count), label="我的自选")
    except RenderError as error:
        logger.warning(f"[SayuStock] 我的自选出图失败: {error}")
        return "自选出图失败"


def _ai_return_my_stock(total: int, quotes: list[tuple[Quote, str]], failures: list[Failure]) -> None:
    try:
        average = watchlist_average(quotes)
        average_text = f"{average:+.2f}%" if average is not None else "暂无数据"
        lines = [f"【我的自选】共 {total} 只，平均涨跌幅: {average_text}"]
        if failures:
            lines.append(f"⚠ {len(failures)} 只行情获取失败（未计入平均值）")
            lines.extend(f"  {failure.label}：{failure.reason}" for failure in failures)
        if quotes:
            lines.append("个股行情:")
            ranked = sorted(
                quotes,
                key=lambda pair: pair[0].change_pct if pair[0].change_pct is not None else float("-inf"),
                reverse=True,
            )
            for quote, code in ranked:
                change = f"{quote.change_pct:+.2f}%" if quote.change_pct is not None else "暂无数据"
                amount = number_to_chinese(quote.amount) if quote.amount is not None else "暂无数据"
                turnover = f"{quote.turnover_rate:.2f}%" if quote.turnover_rate is not None else "暂无数据"
                lines.append(
                    f"  {quote.symbol.name}({code}): 最新价 {quote.price} {change} 换手率 {turnover} 成交额 {amount}"
                )
        ai_return("\n".join(lines))
    except Exception as error:
        logger.warning(f"[SayuStock] ai_return 自选股数据提取失败: {error}")
