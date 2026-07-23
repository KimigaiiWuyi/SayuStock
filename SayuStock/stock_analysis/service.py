"""分析命令编排：拉数 → 报告 → 出图。"""

from __future__ import annotations

from typing import Union

from PIL import Image

from gsuid_core.logger import logger

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
from ..utils.stock.request import get_gg

DrawOut = Union[str, Image.Image]
_RENDER_ERRORS = (OSError, RuntimeError, ValueError, TypeError, MemoryError)


async def run_technical_analysis(text: str) -> DrawOut:
    period, query = parse_period_and_query(text)
    if not query:
        return "❌请后跟股票代码或名称，例如：技术分析 茅台\n可选周期：日k/周k/月k/60k  例如：技术分析 周k 600519"
    sector = f"single-stock-kline-{period}"
    raw = await get_gg(query, sector)
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return "❌无法获取K线数据"
    data = raw["data"] if "data" in raw and isinstance(raw["data"], dict) else {}
    klines_raw = data["klines"] if "klines" in data and isinstance(data["klines"], list) else []
    klines = [str(x) for x in klines_raw]
    name = str(
        data["name"] if "name" in data and data["name"] else (data["f58"] if "f58" in data and data["f58"] else query)
    )
    code = str(
        data["code"] if "code" in data and data["code"] else (data["f57"] if "f57" in data and data["f57"] else query)
    )
    report = build_technical_report(name=name, code=code, period_code=period, klines=klines)
    if isinstance(report, str):
        return report
    try:
        return render_technical_image(report)
    except _RENDER_ERRORS as e:
        logger.exception(f"[stock_analysis] technical render fail: {e}")
        return report_to_text(report)


async def run_stock_card(text: str) -> DrawOut:
    card = await build_trade_card(text)
    if isinstance(card, str):
        return card
    try:
        return render_card_image(card)
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


async def run_auto_screener(text: str) -> DrawOut:
    result = await run_screener(text)
    if result.error:
        return result.error
    try:
        return render_screener_image(result)
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


async def run_portfolio_check(text: str, *, user_codes: list[str] | None = None) -> DrawOut:
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
        return render_portfolio_image(report)
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
