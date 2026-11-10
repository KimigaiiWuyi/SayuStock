import io
import base64
import asyncio
from typing import Dict, List, Tuple
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps, ImageDraw

from gsuid_core.logger import logger
from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.utils import number_to_chinese
from ..utils.market import (
    BREADTH_BANDS,
    Quote,
    BreadthBar,
    DisplayItem,
    get_market,
    breadth_counts,
    breadth_up_down,
    is_market_error,
    board_rows_to_items,
)
from ..utils.sparkline import sparkline_from_series
from ..utils.market.models import BoardSnapshot, BreadthBucket, MarketTurnover
from ..utils.market.display import source_label
from ..utils.stock.request_utils import get_image_from_em
from ..utils.market_overview_html import (
    CSS_WIDTH,
    CANVAS_HEIGHT,
    _BREADTH_BAR_LEFT,
    _BREADTH_BAR_RIGHT,
    _BREADTH_BAR_WIDTH,
    _BREADTH_BAR_COLORS,
    _BREADTH_DIRECTION_COLORS,
    breadth_bar_left,
    build_market_overview_html,
)

__all__ = [
    "_BREADTH_BAR_LEFT",
    "_BREADTH_BAR_WIDTH",
    "_BREADTH_BAR_RIGHT",
    "_BREADTH_DIRECTION_COLORS",
    "_BREADTH_BAR_COLORS",
    "breadth_bar_left",
    "_OVERVIEW_INDEXES",
    "_OVERVIEW_OPTIONAL",
    "DIFF_MAP",
    "draw_info_img",
    "draw_block",
    "draw_bar",
    "remove_color_range",
    "invert_colors",
    "calculate_gradient_rgb_from_gray",
]

TEXT_PATH = Path(__file__).parent / "texture2d"

# 主要指数按代码报价。行情中心「涨跌幅前 100」里没有上证/沪深300。
_OVERVIEW_INDEXES: tuple[tuple[str, str], ...] = (
    ("上证指数", "1.000001"),
    ("中证全指", "1.000985"),
    ("创业板指", "0.399006"),
    ("科创综指", "1.000680"),
    ("沪深300", "1.000300"),
    ("中证500", "1.000905"),
    ("中证1000", "1.000852"),
    ("中证2000", "2.932000"),
    ("中证A500", "1.000510"),
    ("北证50", "0.899050"),
)
_OVERVIEW_OPTIONAL: tuple[tuple[str, str], ...] = (
    ("黄金9999", "118.AU9999"),
    ("三十债主连", "220.TLM"),
)

DIFF_MAP = {
    3.3: "1",
    2.7: "2",
    2: "3",
    1: "4",
    0: "5",
    -0.5: "6",
    -1.3: "7",
    -2.1: "8",
    -3.1: "9",
    -4: "10",
}


def remove_color_range(
    img: Image.Image,
    lower_bound: tuple[int, int, int],
    upper_bound: tuple[int, int, int],
) -> Image.Image:
    new_data: list[tuple[int, ...]] = []
    for item in img.get_flattened_data():
        if isinstance(item, tuple) and len(item) >= 3:
            matched = (
                lower_bound[0] <= item[0] <= upper_bound[0]
                and lower_bound[1] <= item[1] <= upper_bound[1]
                and lower_bound[2] <= item[2] <= upper_bound[2]
            )
            new_data.append((255, 255, 255, 0) if matched else item)
        elif isinstance(item, (int, float)):
            new_data.append((int(item),))

    img.putdata(new_data)
    return img


def invert_colors(img: Image.Image) -> Image.Image:
    r, g, b, a = img.split()

    rgb = Image.merge("RGB", (r, g, b))
    inverted_rgb = ImageOps.invert(rgb)

    inverted_img = Image.composite(
        Image.merge("RGBA", (*inverted_rgb.split(), a)),
        img,
        a,
    )
    return inverted_img


def calculate_alpha(diff: float) -> Tuple[int, int, int, int]:
    abs_diff = abs(diff)
    _max = 170
    _min = 10

    if abs_diff >= 10.0:
        alpha: int = _max
    elif abs_diff < 0.2:
        alpha = _min
    else:
        alpha = int(_min + abs_diff * (_max - _min) / 10)

    if alpha > _max:
        alpha = _max

    if diff >= 0.1:
        return (185, 0, 6, alpha)
    elif diff <= 0.1:
        return (59, 140, 18, alpha)

    return (41, 41, 41, 200)


def calculate_gradient_rgb_from_gray(diff: float) -> tuple[int, int, int, int]:
    max_diff = 4
    neutral_gray_level = 26
    r, g, b = neutral_gray_level, neutral_gray_level, neutral_gray_level

    if diff > 0:
        intensity = min(diff, max_diff) / max_diff
        r = int(neutral_gray_level + intensity * (170 - neutral_gray_level))
    elif diff < 0:
        intensity = min(abs(diff), max_diff) / max_diff
        g = int(neutral_gray_level + intensity * (170 - neutral_gray_level))

    return r, g, b, 150


async def draw_block(item: DisplayItem, _type: str = "diff") -> Image.Image:
    """绘制指数/标的块（保留供兼容测试/外部模块）。"""
    name_s = item.name
    price_s = item.price
    diff = round(float(item.change_pct), 2)

    zs_img = Image.new("RGBA", (200, 140))
    zs_draw = ImageDraw.Draw(zs_img)
    zsc = calculate_gradient_rgb_from_gray(diff)
    zsc2 = (206, 34, 30) if diff >= 0 else (36, 206, 30)

    zs_draw.rounded_rectangle((15, 13, 185, 127), 0, zsc)

    t_font = ss_font(24)
    name = name_s
    if len(name) >= 15:
        name = name[:6]
    elif len(name) >= 10:
        t_font = ss_font(18)

    zs_draw.text((100, 99), name, (255, 255, 255), t_font, "mm")
    zs_draw.text((100, 38), f"{price_s}", zsc2, ss_font(30), "mm")
    zs_draw.text((100, 70), f"{'+' if diff >= 0 else ''}{diff}%", zsc2, ss_font(30), "mm")
    return zs_img


def _overview_item(label: str, quote: Quote) -> DisplayItem:
    change = float(quote.change_pct) if quote.change_pct is not None else 0.0
    return DisplayItem(
        name=label,
        price=float(quote.price),
        change_pct=change,
        amount=quote.amount,
        code=quote.symbol.code,
        provider=quote.provider,
    )


def _empty_breadth() -> BreadthBar:
    buckets = tuple(BreadthBucket(label=label, count=0) for label in BREADTH_BANDS)
    return BreadthBar(buckets=buckets)


async def draw_bar(sd: List[DisplayItem], img: Image.Image, start: int, y: int, h: int = 90) -> None:
    """兼容旧绘图接口。"""
    ls = len(sd)
    for hindex, hy in enumerate(sd):
        hy_diff = hy.change_pct
        hy_img = Image.new("RGBA", (425, h))
        base_o = int(255 * (((ls + 1) - hindex) / ls))
        if hy_diff >= 0:
            hyc2 = (140, 18, 22, base_o)
            dd = (201, 26, 32, 200)
            lead = hy.lead_name or ""
            lead_pct = hy.lead_change_pct
        else:
            hyc2 = (59, 140, 18, base_o)
            dd = (25, 199, 16, 200)
            lead = hy.fall_name or hy.lead_name or ""
            lead_pct = hy.fall_change_pct if hy.fall_change_pct is not None else hy.lead_change_pct

        hy_draw = ImageDraw.Draw(hy_img)
        hy_draw.rounded_rectangle((23, 2, 403, 57), 0, hyc2)
        hy_draw.text((53, 30), hy.name, (255, 255, 255), ss_font(30), "lm")
        hy_draw.text((53, 75), f"{lead}", dd, ss_font(24), "lm")
        lp = f"{lead_pct:+.2f}%" if lead_pct is not None else ""
        hy_draw.text((384, 75), lp, dd, ss_font(24), "rm")
        hy_draw.text((384, 30), f"{hy_diff:+.2f}%", (255, 255, 255), ss_font(30), "rm")
        img.paste(hy_img, (start, y + h * hindex), hy_img)


def _ai_return_market_overview(
    data_zs: list[DisplayItem],
    data_hy_rise: list[DisplayItem],
    data_hy_fall: list[DisplayItem],
    up_value: object,
    down_value: object,
    diff_bar: dict[str, int],
) -> None:
    """从大盘概览语义数据中提取文本，经 ai_return 给 AI。"""
    try:
        result = "【A股大盘概览】\n【主要指数】\n"
        for item in data_zs[:12]:
            result += f"  {item.name}: {item.price} ({'+' if item.change_pct >= 0 else ''}{item.change_pct}%)\n"
        result += f"\n【涨跌分布】上涨 {up_value} 家  下跌 {down_value} 家\n"
        for label, count in diff_bar.items():
            if count > 0:
                result += f"  {label}: {count}\n"
        result += "\n【领涨行业板块】\n"
        for hy in data_hy_rise[:5]:
            result += (
                f"  {hy.name}: {'+' if hy.change_pct >= 0 else ''}{hy.change_pct}% (领涨: {hy.lead_name or 'N/A'})\n"
            )
        result += "\n【领跌行业板块】\n"
        for hy in data_hy_fall[:5]:
            leader = hy.fall_name or hy.lead_name or "N/A"
            result += f"  {hy.name}: {'+' if hy.change_pct >= 0 else ''}{hy.change_pct}% (领跌: {leader})\n"
        ai_return(result)
    except (TypeError, ValueError, KeyError) as e:
        logger.warning(f"[SayuStock] ai_return 大盘概览数据提取失败: {e}")


async def draw_info_img(is_save: bool = False) -> str | bytes:
    market = get_market()
    index_queries = [secid for _, secid in (*_OVERVIEW_INDEXES, *_OVERVIEW_OPTIONAL)]
    labels = [label for label, _ in (*_OVERVIEW_INDEXES, *_OVERVIEW_OPTIONAL)]
    optional_names = {label for label, _ in _OVERVIEW_OPTIONAL}

    # 并发拉取数据
    intraday_tasks = [market.intraday(secid) for secid in index_queries]
    intraday_coro = asyncio.gather(*intraday_tasks, return_exceptions=True)
    boards_coro = asyncio.gather(
        market.board("行业板块", limit=20, sort_asc=False),
        market.board("行业板块", limit=20, sort_asc=True),
        market.board("概念板块", limit=20, sort_asc=False),
        market.board("概念板块", limit=20, sort_asc=True),
        market.breadth(),
        market.market_turnover(),
    )
    img_coro = get_image_from_em(size=(500, 274))

    intraday_results, boards_results, raw_em_img = await asyncio.gather(
        intraday_coro,
        boards_coro,
        img_coro,
    )
    board_hy_1, board_hy_2, board_gn_1, board_gn_2, bar_r, turnover = boards_results

    # 1. 检验板块结果
    for result in (board_hy_1, board_hy_2, board_gn_1, board_gn_2):
        if is_market_error(result):
            return result.message
    if not isinstance(board_hy_1, BoardSnapshot) or not isinstance(board_hy_2, BoardSnapshot):
        return "行业板块数据异常"
    if not isinstance(board_gn_1, BoardSnapshot) or not isinstance(board_gn_2, BoardSnapshot):
        return "概念板块数据异常"

    data_hy_fall = board_rows_to_items(board_hy_1.rows)
    data_hy_rise = board_rows_to_items(board_hy_2.rows)
    data_gn_fall = board_rows_to_items(board_gn_1.rows)
    data_gn_rise = board_rows_to_items(board_gn_2.rows)

    # 2. 处理主要指数与分时折线图
    data_zs_items: list[DisplayItem] = []
    sparks: dict[str, str] = {}
    index_providers: list[str | None] = []
    index_errors: list[str] = []

    for label, secid, res in zip(labels, index_queries, intraday_results, strict=True):
        if not isinstance(res, BaseException) and not is_market_error(res) and res.points:
            q = res.quote
            price = float(q.price) if q else 0.0
            change = float(q.change_pct) if q and q.change_pct is not None else 0.0
            amount = q.amount if q else None
            code = q.symbol.code if q else secid
            prov = res.provider or (q.provider if q else None)
            item = DisplayItem(name=label, price=price, change_pct=change, amount=amount, code=code, provider=prov)
            data_zs_items.append(item)
            index_providers.append(prov)
            svg = sparkline_from_series(res, width=175.0, height=50.0)
            if svg:
                sparks[label] = svg
                sparks[code] = svg
        else:
            # 分时异常时降级尝试获取 quote 单报价
            q = await market.quote(secid)
            if isinstance(q, Quote):
                item = _overview_item(label, q)
                data_zs_items.append(item)
                index_providers.append(q.provider)
            elif is_market_error(q):
                if label in optional_names:
                    logger.warning(f"[SayuStock] 大盘概览{label}报价跳过: {q.message}")
                else:
                    index_errors.append(f"{label}: {q.message}")

    if not any(item.name not in optional_names for item in data_zs_items):
        detail = "；".join(index_errors) if index_errors else "无报价"
        return f"主要指数报价失败：{detail}"

    # 3. 涨跌分布
    breadth_provider: str | None = None
    if is_market_error(bar_r):
        logger.warning(f"[SayuStock] 大盘概览涨跌分布跳过: {bar_r.message}")
        bar_r = _empty_breadth()
    elif isinstance(bar_r, BreadthBar):
        breadth_provider = bar_r.provider
    else:
        bar_r = _empty_breadth()

    diff_bar: Dict[str, int] = dict(breadth_counts(bar_r))
    up_value, down_value = breadth_up_down(diff_bar)

    # 4. 指数涨跌
    qz_diff = 0.0
    sz_diff = 0.0
    for item in data_zs_items:
        if item.name == "中证全指":
            qz_diff = item.change_pct
        if item.name == "上证指数":
            sz_diff = item.change_pct

    # 5. 触发 AI 文本（有图必有文字规范）
    _ai_return_market_overview(data_zs_items, data_hy_rise, data_hy_fall, up_value, down_value, diff_bar)

    # 6. 两市成交额
    turnover_provider: str | None = None
    trade_date = None
    if is_market_error(turnover):
        logger.warning(f"[SayuStock] 大盘概览成交额跳过: {turnover.message}")
        all_f6_str = "暂缺"
        f6diff_str = ""
        fcolor = "#ba1a1b"
    else:
        if not isinstance(turnover, MarketTurnover):
            return "两市成交额数据异常"
        turnover_provider = turnover.provider
        trade_date = turnover.last_trade_date
        all_f6_str = number_to_chinese(turnover.amount)
        prev_amount = turnover.prev_amount
        f6diff = turnover.amount - prev_amount if prev_amount is not None else 0
        if prev_amount is None:
            f6diff_str = ""
            fcolor = "#ba1a1b"
        elif f6diff > 0:
            f6diff_str = f"放量: {number_to_chinese(abs(f6diff))}"
            fcolor = "#ba1a1b"
        else:
            f6diff_str = f"缩量: {number_to_chinese(abs(f6diff))}"
            fcolor = "#12c71e"

    # 7. 主力资金图片处理（抠白底与反色）
    em_flow_uri = ""
    try:
        em_img = raw_em_img.convert("RGBA")
        em_img = remove_color_range(em_img, (200, 200, 200), (255, 255, 255))
        em_img = invert_colors(em_img)
        buf = io.BytesIO()
        em_img.save(buf, format="PNG")
        em_flow_uri = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except (OSError, ValueError) as e:
        logger.warning(f"[SayuStock] 主力净流入图片处理失败: {e}")

    # 8. 表情包编号
    for i in DIFF_MAP:
        if qz_diff >= i:
            title_num = int(DIFF_MAP[i])
            break
    else:
        title_num = 11

    # 9. 数据来源信息
    source_bits = [f"指数{source_label(*index_providers)}"]
    source_bits.append(f"板块{source_label(board_hy_1.provider, board_gn_1.provider)}")
    if breadth_provider:
        source_bits.append(f"涨跌{source_label(breadth_provider)}")
    if turnover_provider:
        source_bits.append(f"成交额{source_label(turnover_provider)}")
    source_line = f"数据来源：{' '.join(source_bits)} | SayuStock"

    # 10. 构建 HTML
    html = build_market_overview_html(
        data_zs_items=data_zs_items,
        sparks=sparks,
        diff_bar=diff_bar,
        up_value=up_value,
        down_value=down_value,
        em_flow_uri=em_flow_uri,
        now=datetime.now(),
        trade_date=trade_date,
        all_f6_str=all_f6_str,
        f6diff_str=f6diff_str,
        fcolor=fcolor,
        qz_diff=qz_diff,
        sz_diff=sz_diff,
        data_hy_fall=data_hy_fall,
        data_hy_rise=data_hy_rise,
        data_gn_fall=data_gn_fall,
        data_gn_rise=data_gn_rise,
        source_line=source_line,
        title_num=title_num,
    )

    try:
        img_bytes = await render_html_to_bytes(
            html,
            max_width=float(CSS_WIDTH),
            dpi=96.0,
            device_height=float(CANVAS_HEIGHT),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(CSS_WIDTH),
        )
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception(f"[SayuStock] 大盘概览 HTML 出图失败: {e}")
        return "大盘概览出图失败"

    # 保存预览图给用户审阅
    for out_dir in (
        Path(__file__).resolve().parent.parent.parent / "preview_output",
        Path("E:/preview_output"),
    ):
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "market_overview.png").write_bytes(img_bytes)
        except OSError:
            pass

    return img_bytes
