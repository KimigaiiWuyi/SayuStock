"""分析命令编排：拉数 → 报告文字 → 出图，出图失败时直接返回报告。"""

from __future__ import annotations

from gsuid_core.logger import logger
from gsuid_core.ai_core.trigger_bridge import ai_return

from .card import TradeCardData, build_trade_card
from .render import (
    render_card_image,
    render_screener_image,
    render_portfolio_image,
    render_technical_image,
)
from .screener import ScreenerResult, run_screener
from .portfolio import analyze_portfolio
from .technical import (
    report_to_text,
    build_technical_report,
    parse_period_and_query,
)
from ..utils.utils import convert_list
from ..utils.market import KlinePeriod, get_market, kline_to_df, is_market_error
from ..render.extras_ui import fmt_number

BotSendContent = str | bytes
_RENDER_ERRORS = (OSError, RuntimeError, ValueError, TypeError, MemoryError)


def _period_to_enum(code: str) -> KlinePeriod:
    try:
        return KlinePeriod(code)
    except ValueError:
        return KlinePeriod.D1


def _ai_return_report(text: str) -> None:
    try:
        ai_return(text)
    except (TypeError, ValueError) as error:
        logger.warning(f"[stock_analysis] ai_return 失败: {error}")


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
        source_ids=(series.provider,) if series.provider else (),
    )
    if isinstance(report, str):
        return report
    summary = report_to_text(report)
    _ai_return_report(summary)
    try:
        return await render_technical_image(report)
    except _RENDER_ERRORS as error:
        logger.exception(f"[stock_analysis] technical render fail: {error}")
        return summary


def _card_summary(card: TradeCardData) -> str:
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


async def run_stock_card(text: str) -> BotSendContent:
    card = await build_trade_card(text)
    if isinstance(card, str):
        return card
    summary = _card_summary(card)
    _ai_return_report(summary)
    try:
        return await render_card_image(card)
    except _RENDER_ERRORS as error:
        logger.exception(f"[stock_analysis] card render fail: {error}")
        return summary


def _screener_summary(result: ScreenerResult) -> str:
    if result.df.empty:
        return "无匹配结果"
    lines = [f"自动选股 · {result.scope}  命中 {result.matched}/{result.total_pool}  展示 {result.shown}"]
    for _, row in result.df.head(15).iterrows():
        pct = row["pct"] if "pct" in row.index else None
        pct_s = fmt_number(pct, signed=True, suffix="%")
        code = row["code"] if "code" in row.index else ""
        name = row["name"] if "name" in row.index else ""
        pe = row["pe"] if "pe" in row.index else None
        lines.append(f"{code} {name} {pct_s} PE={pe}")
    return "\n".join(lines)


async def run_auto_screener(text: str) -> BotSendContent:
    result = await run_screener(text)
    if result.error:
        return result.error
    summary = _screener_summary(result)
    _ai_return_report(summary)
    try:
        return await render_screener_image(result)
    except _RENDER_ERRORS as error:
        logger.exception(f"[stock_analysis] screener render fail: {error}")
        return summary


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
    requested = min(len(codes), 30)
    messages = list(report.messages)
    if len(report.holdings) < requested:
        messages.insert(0, f"仅覆盖 {len(report.holdings)}/{requested} 只标的，按已获取行情的标的计算。")
    summary = "\n".join(messages)
    _ai_return_report(summary)
    try:
        return await render_portfolio_image(report, requested_count=requested)
    except _RENDER_ERRORS as error:
        logger.exception(f"[stock_analysis] portfolio render fail: {error}")
        return summary


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "—"
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}"
    return str(v)
