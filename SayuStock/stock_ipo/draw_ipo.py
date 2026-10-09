"""IPO 日历：拉取 → 渲染数据 → ai_return 文字 → HTML 出图。"""

from __future__ import annotations

import re
import asyncio

from gsuid_core.logger import logger
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.ai_core.trigger_bridge import ai_return

from .ipo_html import CSS_WIDTH, build_ipo_calendar_html, ipo_calendar_canvas_size
from ..utils.market import IpoEvent, IpoMarket, get_market, is_market_error
from ..utils.render_data import build_ipo_calendar_render_data
from ..utils.render_text import ipo_calendar_text

_MARKET_LABEL: dict[IpoMarket, str] = {IpoMarket.CN: "A股", IpoMarket.HK: "港股", IpoMarket.US: "美股"}
# 「a股」要先于单字母，避免被拆成 a + 股
_FILTER_TOKEN_RE = re.compile(r"a股|[a-z]+|[\u4e00-\u9fff]+")
_FILTER_TOKEN: dict[str, IpoMarket] = {
    "a股": IpoMarket.CN,
    "cn": IpoMarket.CN,
    "沪深": IpoMarket.CN,
    "港股": IpoMarket.HK,
    "港": IpoMarket.HK,
    "hk": IpoMarket.HK,
    "美股": IpoMarket.US,
    "美": IpoMarket.US,
    "us": IpoMarket.US,
    "纳斯达克": IpoMarket.US,
}


def parse_ipo_market_filter(text: str) -> list[IpoMarket] | None:
    """命令参数 → 市场筛选；无完整别名返回 None（= 全部市场）。"""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    picked: list[IpoMarket] = []
    for token in _FILTER_TOKEN_RE.findall(raw):
        market = _FILTER_TOKEN.get(token)
        if market is not None and market not in picked:
            picked.append(market)
    return picked or None


async def draw_ipo_calendar_img(text: str = "") -> bytes | str:
    """``IPO日历 [市场]``：三市场 T-2~T+7 新股甘特图。"""
    markets = parse_ipo_market_filter(text)
    wanted = markets or list(IpoMarket)
    market = get_market()
    results = await asyncio.gather(*(market.ipo_calendar(m) for m in wanted))

    events: list[IpoEvent] = []
    failed: list[str] = []
    for m, res in zip(wanted, results):
        if is_market_error(res):
            failed.append(_MARKET_LABEL[m])
            logger.warning(f"[SayuStock][IPO日历] {m.value} 数据源失败: {res.code} {res.message}")
        else:
            events.extend(res)

    if not events and failed:
        return f"IPO日历数据获取失败：{'、'.join(failed)}市场数据源暂时不可用，请稍后再试"

    data = build_ipo_calendar_render_data(events, markets=markets)
    text_summary = ipo_calendar_text(data)
    if failed:
        text_summary += f"\n⚠ {'、'.join(failed)}市场数据源暂时不可用，未计入"
    # 铁律：有图必有文字，且必须在出图之前
    ai_return(text_summary)

    html = build_ipo_calendar_html(data)
    _, height = ipo_calendar_canvas_size(data)
    try:
        return await render_html_to_bytes(
            html,
            max_width=float(CSS_WIDTH * 2),
            dpi=192.0,
            device_height=float(height * 2),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(CSS_WIDTH),
        )
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception(f"[SayuStock][IPO日历] HTML 出图失败: {e}")
        return "IPO日历出图失败"
