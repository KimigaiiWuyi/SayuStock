import asyncio
from typing import Optional
from pathlib import Path

from PIL import Image, ImageDraw

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.ai_core.trigger_bridge import ai_return

from .draw_info import DIFF_MAP
from ..utils.utils import convert_list, number_to_chinese
from ..utils.market import Quote, DisplayItem, get_market, is_market_error, board_rows_to_items
from ..utils.sparkline import sparkline_from_series
from ..utils.my_stock_html import SPARK_H, SPARK_W, build_my_stock_html, my_stock_canvas_size
from ..utils.database.models import SsBind

TEXT_PATH = Path(__file__).parent / "texture2d"


def bar_labels_from_quote(q: Quote, fallback_code: str = "") -> tuple[str, str]:
    """列表条主标题 / 小字代码。

    名称走 ``SymbolRef.display_name``（带 沪深A / 港股 等后缀）；
    代码必须用该票自己的 ``symbol.code``，不能沿用调用方误传的基金代码。
    """
    name = q.symbol.display_name or (q.symbol.name or "").strip()
    code = (q.symbol.code or "").strip() or fallback_code
    return name, code


def draw_bar_from_quote(
    q: Quote,
    u: str,
    percent: Optional[str] = None,
) -> Image.Image:
    e_money = number_to_chinese(q.amount) if q.amount is not None else "-"
    hs = q.turnover_rate if q.turnover_rate is not None else 0
    p = float(q.change_pct) if q.change_pct is not None else 0.0
    now_price = q.price
    b_title, code = bar_labels_from_quote(q, u)
    s_title = f"({code}) 换: {hs}% 额: {e_money} 价: {now_price}"
    if p > 0:
        bar = Image.open(TEXT_PATH / "myup.png")
        p_color = (213, 102, 102)
    elif p == 0:
        bar = Image.open(TEXT_PATH / "myeq.png")
        p_color = (240, 240, 240)
    else:
        bar = Image.open(TEXT_PATH / "mydown.png")
        p_color = (175, 231, 170)
    bar_draw = ImageDraw.Draw(bar)
    bar_draw.text((82, 40), b_title, (255, 255, 255), ss_font(32), "lm")
    bar_draw.text((82, 75), s_title, p_color, ss_font(20), "lm")
    bar_draw.text((758, 55), f"+{p}%" if p >= 0 else f"{p}%", (255, 255, 255), ss_font(28), "mm")
    if percent is not None:
        bar_draw.text((613, 55), percent, (240, 240, 240), ss_font(28), "mm")
    return bar


async def draw_my_stock_img(ev: Event) -> str | bytes:
    user_id = ev.at if ev.at else ev.user_id
    uid = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)

    if not uid:
        return "您还未添加自选呢~请输入 添加自选 查看帮助!"

    uid = convert_list(uid)
    market = get_market()
    zs_snap = await market.board("主要指数", limit=100, sort_asc=False)
    if is_market_error(zs_snap):
        return zs_snap.message
    zs_items = board_rows_to_items(zs_snap.rows)

    two_col = len(uid) >= 18
    zyzs = (
        ["上证指数", "深证成指", "中证A500", "中证2000"]
        if not two_col
        else ["上证指数", "深证成指", "创业板指", "上证50", "沪深300", "中证A500", "中证2000", "国债指数"]
    )
    index_items: list[DisplayItem] = []
    for zs_name in zyzs:
        for item in zs_items:
            if zs_name != item.name.split("(")[0].strip() and zs_name not in item.name:
                continue
            index_items.append(item)
            break

    all_p = 0.0
    stock_details: list[dict[str, object]] = []
    quotes: list[tuple[Quote, str] | None] = [None] * len(uid)
    sparks: dict[str, str] = {}

    async def sg(index: int, u: str) -> None:
        nonlocal all_p
        query = u[4:] if u.startswith("VIX.") else u
        series = await market.intraday(query)
        if is_market_error(series):
            return
        q = series.quote
        if q is None:
            return
        all_p += float(q.change_pct) if q.change_pct is not None else 0.0
        stock_details.append(
            {
                "code": u,
                "name": q.symbol.name,
                "price": q.price,
                "change": q.change_pct if q.change_pct is not None else 0,
                "turnover": q.turnover_rate,
                "amount": q.amount,
            }
        )
        quotes[index] = (q, u)
        svg = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
        if svg:
            sparks[u] = svg
            sparks[q.symbol.code] = svg

    await asyncio.gather(*[sg(index, u) for index, u in enumerate(uid)])
    filled = [row for row in quotes if row is not None]
    if not filled:
        return "暂无自选行情"

    avg_p = all_p / len(uid)
    title_num = "11"
    for i in DIFF_MAP:
        if avg_p >= i:
            title_num = DIFF_MAP[i]
            break

    html = build_my_stock_html(
        quotes=filled,
        index_items=index_items,
        title_num=title_num,
        sparklines=sparks,
    )
    width, height, _ = my_stock_canvas_size(len(filled))
    _ai_return_my_stock(uid, all_p, stock_details)
    try:
        return await render_html_to_bytes(
            html,
            max_width=float(width * 2),
            dpi=192.0,
            device_height=float(height * 2),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(width),
        )
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception(f"[SayuStock] 我的自选 HTML 出图失败: {e}")
        return "自选出图失败"


def _ai_return_my_stock(
    uid_list: list[str],
    all_p: float,
    stock_details: list[dict[str, object]] | None = None,
) -> None:
    """从自选股语义数据提取文本，经 ai_return 给 AI。"""
    try:
        avg_p = all_p / len(uid_list) if uid_list else 0
        result = f"【我的自选】共 {len(uid_list)} 只，平均涨跌幅: {avg_p:+.2f}%\n"

        if stock_details:
            sorted_details = sorted(
                stock_details,
                key=lambda x: float(x["change"]) if "change" in x and isinstance(x["change"], (int, float)) else 0.0,
                reverse=True,
            )
            result += "\n个股行情:\n"
            for s in sorted_details:
                change = s["change"] if "change" in s else 0
                sign = "+" if isinstance(change, (int, float)) and change >= 0 else ""
                amount = s["amount"] if "amount" in s else "N/A"
                if isinstance(amount, (int, float)):
                    from ..utils.utils import number_to_chinese as _n2c

                    amount = _n2c(amount)
                result += (
                    f"  {s['name']}({s['code']}): "
                    f"最新价 {s['price']}  {sign}{change}%  "
                    f"换手率 {s['turnover']}%  成交额 {amount}\n"
                )
        else:
            result += f"自选代码: {', '.join(uid_list[:10])}"

        ai_return(result)
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return 自选股数据提取失败: {e}")
