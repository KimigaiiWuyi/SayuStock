import asyncio
from typing import Dict, List, Tuple
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps, ImageDraw

from gsuid_core.logger import logger
from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.image import get_footer
from ..utils.utils import number_to_chinese
from ..utils.market import (
    BREADTH_BANDS,
    BREADTH_DIRECTION,
    Quote,
    BreadthBar,
    DisplayItem,
    get_market,
    breadth_counts,
    breadth_up_down,
    is_market_error,
    board_rows_to_items,
)
from ..utils.market.models import BoardSnapshot, BreadthBucket, MarketTurnover
from ..utils.market.display import source_label
from ..utils.stock.request_utils import get_image_from_em

TEXT_PATH = Path(__file__).parent / "texture2d"

# 概览图涨跌分布柱几何（div.png 内坐标系，画布 850×500）：
# 首柱左缘 / 柱宽固定，末柱右缘恒定，柱间距按实际档位数自算。
# 12 档时间距正好是原来的 66px（45 + 11×66 + 36 = 807），分档增删后
# 也不会再把最后一根柱连同家数标签画出画布（13 档时原实现右缘到 873）。
_BREADTH_BAR_LEFT = 45
_BREADTH_BAR_WIDTH = 36
_BREADTH_BAR_RIGHT = 807

# 方向 → 柱色：涨红、跌绿、平灰。方向由 BREADTH_DIRECTION 单点给出，
# 不再按「第几根柱」对半切（那是 12 档时代的写法，插入「平」之后会错色）。
_BREADTH_DIRECTION_COLORS: Dict[int, Tuple[int, int, int]] = {
    1: (187, 26, 26),
    0: (150, 150, 150),
    -1: (23, 199, 30),
}
_BREADTH_BAR_COLORS: Dict[str, Tuple[int, int, int]] = {
    label: _BREADTH_DIRECTION_COLORS[direction] for label, direction in BREADTH_DIRECTION.items()
}

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


def breadth_bar_left(index: int, total: int) -> int:
    """分布柱左缘 x：index 从 0 起（0 = 跌停侧），末柱右缘恒为 _BREADTH_BAR_RIGHT。"""
    if total < 2:
        return _BREADTH_BAR_LEFT
    span = _BREADTH_BAR_RIGHT - _BREADTH_BAR_WIDTH - _BREADTH_BAR_LEFT
    return round(_BREADTH_BAR_LEFT + index * span / (total - 1))


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
    datas = img.getdata()

    new_data = []
    for item in datas:
        # 检查像素是否在颜色范围内
        if (
            lower_bound[0] <= item[0] <= upper_bound[0]
            and lower_bound[1] <= item[1] <= upper_bound[1]
            and lower_bound[2] <= item[2] <= upper_bound[2]
        ):
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)

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
    # 中性色为深灰色
    neutral_gray_level = 26
    r, g, b = neutral_gray_level, neutral_gray_level, neutral_gray_level

    if diff > 0:
        # 上涨：从灰色渐变到红色
        intensity = min(diff, max_diff) / max_diff
        # R通道从40增加到255
        r = int(neutral_gray_level + intensity * (170 - neutral_gray_level))
    elif diff < 0:
        # 下跌：从灰色渐变到绿色
        intensity = min(abs(diff), max_diff) / max_diff
        # G通道从40增加到255
        g = int(neutral_gray_level + intensity * (170 - neutral_gray_level))

    return r, g, b, 150


async def draw_block(item: DisplayItem, _type: str = "diff") -> Image.Image:
    """绘制指数/标的块（语义 DisplayItem）。"""
    name_s = item.name
    price_s = item.price
    diff = round(float(item.change_pct), 2)

    zs_img = Image.new("RGBA", (200, 140))
    zs_draw = ImageDraw.Draw(zs_img)
    if diff >= 0:
        zsc = calculate_gradient_rgb_from_gray(diff)
        zsc2 = (206, 34, 30)
    else:
        zsc = calculate_gradient_rgb_from_gray(diff)
        zsc2 = (36, 206, 30)

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
    )


def _empty_breadth() -> BreadthBar:
    buckets = tuple(BreadthBucket(label=label, count=0) for label in BREADTH_BANDS)
    return BreadthBar(buckets=buckets)


async def draw_info_img(is_save: bool = False) -> str | bytes:
    market = get_market()
    index_queries = [secid for _, secid in (*_OVERVIEW_INDEXES, *_OVERVIEW_OPTIONAL)]
    results = await asyncio.gather(
        market.quotes(index_queries),
        market.board("行业板块", limit=20, sort_asc=False),
        market.board("行业板块", limit=20, sort_asc=True),
        market.board("概念板块", limit=20, sort_asc=False),
        market.board("概念板块", limit=20, sort_asc=True),
        market.breadth(),
    )
    quote_rows, hy_z_r, hy_f_r, gn_z_r, gn_f_r, bar_r = results
    labels = [label for label, _ in (*_OVERVIEW_INDEXES, *_OVERVIEW_OPTIONAL)]
    data_zs_items: list[DisplayItem] = []
    index_providers: list[str | None] = []
    index_errors: list[str] = []
    optional_names = {label for label, _ in _OVERVIEW_OPTIONAL}
    if not isinstance(quote_rows, list):
        return "主要指数数据异常"
    for label, quote in zip(labels, quote_rows, strict=True):
        if isinstance(quote, Quote):
            data_zs_items.append(_overview_item(label, quote))
            index_providers.append(quote.provider)
            continue
        if is_market_error(quote):
            if label in optional_names:
                logger.warning(f"[SayuStock] 大盘概览{label}报价跳过: {quote.message}")
            else:
                index_errors.append(f"{label}: {quote.message}")
    if not any(item.name not in optional_names for item in data_zs_items):
        detail = "；".join(index_errors) if index_errors else "无报价"
        return f"主要指数报价失败：{detail}"
    for result in (hy_z_r, hy_f_r, gn_z_r, gn_f_r):
        if is_market_error(result):
            return result.message
    if not isinstance(hy_z_r, BoardSnapshot) or not isinstance(hy_f_r, BoardSnapshot):
        return "行业板块数据异常"
    if not isinstance(gn_z_r, BoardSnapshot) or not isinstance(gn_f_r, BoardSnapshot):
        return "概念板块数据异常"

    data_hy_z = board_rows_to_items(hy_z_r.rows)
    data_hy_f = board_rows_to_items(hy_f_r.rows)
    data_gn_z = board_rows_to_items(gn_z_r.rows)
    data_gn_f = board_rows_to_items(gn_f_r.rows)
    breadth_provider: str | None = None
    if is_market_error(bar_r):
        logger.warning(f"[SayuStock] 大盘概览涨跌分布跳过: {bar_r.message}")
        bar_r = _empty_breadth()
    elif isinstance(bar_r, BreadthBar):
        breadth_provider = bar_r.provider
    else:
        bar_r = _empty_breadth()
    # breadth_counts 已按 BREADTH_BANDS 补全并按该序输出，渲染端直接画
    diff_bar: Dict[str, int] = dict(breadth_counts(bar_r))
    up_value, down_value = breadth_up_down(diff_bar)
    _ai_return_market_overview(data_zs_items, data_hy_z, data_hy_f, up_value, down_value, diff_bar)

    h0 = 90
    h = 1060 + 20 * h0
    img = Image.new("RGBA", (1700, h), (7, 9, 27))
    img_draw = ImageDraw.Draw(img)

    bar1 = Image.open(TEXT_PATH / "bar1.png")
    bar2 = Image.open(TEXT_PATH / "bar2.png")
    bar3 = Image.open(TEXT_PATH / "bar3.png")
    bar4 = Image.open(TEXT_PATH / "bar4.png")

    n = 0
    qz_diff = 0.0
    sz_diff = 0.0
    for item in data_zs_items:
        if item.name == "中证全指":
            qz_diff = item.change_pct
        if item.name == "上证指数":
            sz_diff = item.change_pct
        zs_img = await draw_block(item)
        img.paste(zs_img, (25 + 200 * (n % 4), 440 + 140 * (n // 4)), zs_img)
        n += 1

    if n > 0:
        # 卡片 140px，首行顶在 440；框住全部指数行，不要只圈第一行
        rows = (n + 3) // 4
        img_draw.rectangle((16, 434, 834, 440 + rows * 140 + 4), None, (246, 180, 0), 5)

    # 分布统计
    div = Image.open(TEXT_PATH / "div.png")
    div_draw = ImageDraw.Draw(div)
    max_num = max(diff_bar.values())
    max_h = 366

    div_draw.rectangle(
        (20, 0, 100, 40),
        (23, 199, 30, 150),
    )
    div_draw.rectangle(
        (750, 0, 830, 40),
        (187, 26, 26, 150),
    )

    div_draw.text(
        (60, 20),
        f"{down_value}",
        (255, 255, 255),
        ss_font(24),
        "mm",
    )
    div_draw.text(
        (790, 20),
        f"{up_value}",
        (255, 255, 255),
        ss_font(24),
        "mm",
    )
    bands = list(diff_bar.items())[::-1]
    for dindex, (band, ij_num) in enumerate(bands):
        if ij_num == 0:
            continue
        left = breadth_bar_left(dindex, len(bands))
        lenth = int(max_h * ij_num / max_num)
        div_draw.rectangle(
            (left, 413 - lenth, left + _BREADTH_BAR_WIDTH, 413),
            _BREADTH_BAR_COLORS[band],
        )
        div_draw.text(
            (left + _BREADTH_BAR_WIDTH // 2, 413 - lenth - 25),
            f"{ij_num}",
            (255, 255, 255),
            ss_font(24),
            "mm",
        )
    img.paste(div, (850, 420), div)

    # 流入流出
    web_em_img = await get_image_from_em(size=(500, 274))
    web_em_img = web_em_img.convert("RGBA")
    web_em_img = remove_color_range(
        web_em_img,
        (200, 200, 200),
        (255, 255, 255),
    )
    web_em_img = invert_colors(web_em_img)
    img.paste(web_em_img, (882, 32), web_em_img)

    turnover = await market.market_turnover()
    turnover_provider: str | None = None
    trade_date = None
    if is_market_error(turnover):
        logger.warning(f"[SayuStock] 大盘概览成交额跳过: {turnover.message}")
        all_f6_str = "暂缺"
        f6diff_str = ""
        fcolor = (186, 26, 27, 100)
    else:
        if not isinstance(turnover, MarketTurnover):
            return "两市成交额数据异常"
        turnover_provider = turnover.provider
        trade_date = turnover.last_trade_date
        all_f6 = turnover.amount
        all_f6_str = number_to_chinese(all_f6)
        # 单市场源拿不到昨成交额，此时不谎报放量/缩量
        prev_amount = turnover.prev_amount
        f6diff = all_f6 - prev_amount if prev_amount is not None else 0
        if prev_amount is None:
            f6diff_str = ""
            fcolor = (186, 26, 27, 100)
        elif f6diff > 0:
            f6diff_str = f"放量: {number_to_chinese(abs(f6diff))}"
            fcolor = (186, 26, 27, 100)
        else:
            f6diff_str = f"缩量: {number_to_chinese(abs(f6diff))}"
            fcolor = (18, 199, 30, 100)

    time_color = (186, 26, 27, 100) if sz_diff >= 0 else (18, 199, 30, 100)

    now = datetime.now()
    weekday = now.strftime("星期" + "一二三四五六日"[now.weekday()])
    time = now.strftime("%H:%M")
    date = now.strftime("%Y.%m.%d")

    # 休市看「数据是不是今天的」，不是看字段有没有值：东财正常交易日回 None，
    # 新浪回数据所属交易日（盘中就是今天）。只判 None 会让新浪供数时盘中显示休市。
    # 按 .date() 相减：带时分相减会把同一天的差算成 -1 天，天数标签也会偏一天。
    stale_days = 0 if trade_date is None else (now.date() - trade_date.date()).days

    if stale_days > 0:
        days_label = {1: "上日", 2: "前日", 3: "三日前"}.get(stale_days, f"{stale_days}日前")
        img_draw.rectangle((1395, 62, 1655, 229), (60, 60, 60, 180))
        img_draw.text((1524, 95), f"{weekday}", (160, 160, 160), ss_font(36), "mm")
        img_draw.text((1524, 145), "休  市", (255, 200, 0), ss_font(58), "mm")
        img_draw.text((1524, 197), f"{date}", (160, 160, 160), ss_font(36), "mm")
        vol_label = f"成交额({days_label}): {all_f6_str}"
    else:
        img_draw.rectangle((1395, 62, 1655, 229), time_color)
        img_draw.text((1524, 95), f"{weekday}", (255, 255, 255), ss_font(36), "mm")
        img_draw.text((1524, 145), f"{time}", (255, 255, 255), ss_font(58), "mm")
        img_draw.text((1524, 197), f"{date}", (255, 255, 255), ss_font(36), "mm")
        vol_label = f"成交额: {all_f6_str}"

    img_draw.text((1529, 263), vol_label, time_color, ss_font(28), "mm")
    img_draw.text((1529, 305), f6diff_str, fcolor, ss_font(34), "mm")

    for i in DIFF_MAP:
        if qz_diff >= i:
            title_num = DIFF_MAP[i]
            break
    else:
        title_num = 11

    title = Image.open(TEXT_PATH / f"title{title_num}.png")

    img.paste(bar1, (0, 331), bar1)
    img.paste(bar4, (850, 331), bar4)

    img.paste(bar2, (0, 875), bar2)
    img.paste(bar3, (850, 875), bar3)

    img.paste(title, (0, -30), title)

    await draw_bar(data_hy_z[:20], img, 10, 980, h0)
    await draw_bar(data_hy_f[:20], img, 415, 980, h0)

    await draw_bar(data_gn_z[:20], img, 860, 980, h0)
    await draw_bar(data_gn_f[:20], img, 1265, 980, h0)

    footer = get_footer()
    img.paste(footer, (425, h - 50), footer)

    # 指数、板块、涨跌、成交额可能来自不同源，脚注按段标明。
    source_bits = [f"指数{source_label(*index_providers)}"]
    source_bits.append(f"板块{source_label(hy_z_r.provider, hy_f_r.provider, gn_z_r.provider, gn_f_r.provider)}")
    if breadth_provider:
        source_bits.append(f"涨跌{source_label(breadth_provider)}")
    if turnover_provider:
        source_bits.append(f"成交额{source_label(turnover_provider)}")
    img_draw.text(
        (20, h - 26),
        f"数据来源：{' '.join(source_bits)} | SayuStock",
        (150, 150, 150),
        ss_font(24),
        "lm",
    )

    res = await convert_img(img)
    return res


async def draw_bar(sd: List[DisplayItem], img: Image.Image, start: int, y: int, h: int = 90) -> None:
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
    data_hy_z: list[DisplayItem],
    data_hy_f: list[DisplayItem],
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
        for hy in data_hy_z[:5]:
            result += (
                f"  {hy.name}: {'+' if hy.change_pct >= 0 else ''}{hy.change_pct}% (领涨: {hy.lead_name or 'N/A'})\n"
            )
        result += "\n【领跌行业板块】\n"
        for hy in data_hy_f[:5]:
            leader = hy.fall_name or hy.lead_name or "N/A"
            result += f"  {hy.name}: {'+' if hy.change_pct >= 0 else ''}{hy.change_pct}% (领跌: {leader})\n"
        ai_return(result)
    except (TypeError, ValueError, KeyError) as e:
        logger.warning(f"[SayuStock] ai_return 大盘概览数据提取失败: {e}")
