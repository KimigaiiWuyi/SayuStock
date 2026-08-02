"""分析命令编排：拉数 → 报告 → 出图。

出图结果统一为 ``BotSendContent``（``str | bytes``），与 ``Bot.send`` 入参对齐；
内部 PIL ``Image`` 仅在 render 层出现，编排层负责 ``convert_img``。
"""

from __future__ import annotations

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img

from .card import build_trade_card
from .render import (
    render_card_image,
    render_screener_image,
    render_portfolio_image,
    render_technical_image,
)
from .screener import run_screener
from .portfolio import analyze_portfolio
from .technical import (
    report_to_text,
    build_technical_report,
    parse_period_and_query,
)
from ..utils.utils import convert_list
from ..utils.market import KlinePeriod, get_market, kline_to_df, is_market_error
from ..stock_stockinfo.chart_base import BotSendContent

_RENDER_ERRORS = (OSError, RuntimeError, ValueError, TypeError, MemoryError)


def _period_to_enum(code: str) -> KlinePeriod:
    try:
        return KlinePeriod(code)
    except ValueError:
        return KlinePeriod.D1


async def run_technical_analysis(text: str) -> BotSendContent:
    period, query = parse_period_and_query(text)
    if not query:
        return "❌请后跟股票代码或名称，例如：技术分析 茅台\n可选周期：日k/周k/月k/60k  例如：技术分析 周k 600519"
    series = await get_market().kline(query, _period_to_enum(period))
    if is_market_error(series):
        return series.message
    df = kline_to_df(series)
    report = build_technical_report(
        name=series.symbol.name,
        code=series.symbol.code,
        period_code=period,
        ohlcv_df=df,
    )
    if isinstance(report, str):
        return report
    try:
        return await convert_img(render_technical_image(report))
    except _RENDER_ERRORS as e:
        logger.exception(f"[stock_analysis] technical render fail: {e}")
        return report_to_text(report)


async def run_stock_card(text: str) -> BotSendContent:
    card = await build_trade_card(text)
    if isinstance(card, str):
        return card
    try:
        return await convert_img(render_card_image(card))
    except _RENDER_ERRORS as e:
        logger.exception(f"[stock_analysis] card render fail: {e}")
        lines = [
            f"【{card.name}({card.code}) 股票卡片】",
            (f"现价 {_fmt(card.price)}  涨跌 {card.pct:+.2f}%" if card.pct is not None else f"现价 {_fmt(card.price)}"),
            f"开盘 {_fmt(card.open_price)}  昨收 {_fmt(card.prev_close)}",
            f"行业 {card.industry}",
            f"PE {_fmt(card.pe)}  PB {_fmt(card.pb)}",
        ]
        if card.technical:
            lines.append(report_to_text(card.technical))
        return "\n".join(lines)


async def run_auto_screener(text: str) -> BotSendContent:
    result = await run_screener(text)
    if result.error:
        return result.error
    try:
        return await convert_img(render_screener_image(result))
    except _RENDER_ERRORS as e:
        logger.exception(f"[stock_analysis] screener render fail: {e}")
        if result.df.empty:
            return "无匹配结果"
        lines = [f"自动选股 · {result.scope}  命中 {result.matched}/{result.total_pool}  展示 {result.shown}"]
        for _, row in result.df.head(15).iterrows():
            pct = row["pct"] if "pct" in row.index else None
            pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "—"
            code = row["code"] if "code" in row.index else ""
            name = row["name"] if "name" in row.index else ""
            pe = row["pe"] if "pe" in row.index else None
            lines.append(f"{code} {name} {pct_s} PE={pe}")
        return "\n".join(lines)


async def run_portfolio_check(text: str, *, user_codes: list[str] | None = None) -> BotSendContent:
    """text 可为空（用自选）或空格分隔代码。"""
    codes: list[str] = []
    if text and text.strip():
        codes = [x for x in text.replace("，", " ").replace(",", " ").split() if x]
    elif user_codes:
        codes = convert_list([str(x) for x in user_codes])

    if not codes:
        return "❌请先添加自选，或：组合体检 600519 000001"

    report = await analyze_portfolio(codes)
    if isinstance(report, str):
        return report
    try:
        return await convert_img(render_portfolio_image(report))
    except _RENDER_ERRORS as e:
        logger.exception(f"[stock_analysis] portfolio render fail: {e}")
        return "\n".join(report.messages)


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "—"
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}"
    return str(v)
