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
    DisplayItem,
    from_quote,
    get_market,
    is_market_error,
    board_rows_to_items,
)
from ..utils.stock.request import get_bar, get_hours_from_em
from ..utils.stock.request_utils import get_image_from_em

TEXT_PATH = Path(__file__).parent / "texture2d"
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


async def draw_info_img(is_save: bool = False) -> str | bytes:
    market = get_market()
    results = await asyncio.gather(
        market.board("主要指数", limit=100, sort_asc=False),
        market.board("行业板块", limit=20, sort_asc=False),
        market.board("行业板块", limit=20, sort_asc=True),
        market.board("概念板块", limit=20, sort_asc=False),
        market.board("概念板块", limit=20, sort_asc=True),
        market.quote("118.AU9999"),
        market.quote("220.TLM"),
        get_bar(),
    )

    zs_r, hy_z_r, hy_f_r, gn_z_r, gn_f_r, au_q, tlm_q, bars_raw = results
    # 黄金/国债报价失败不拖垮整页；仅主指数与板块为硬依赖
    for result in (zs_r, hy_z_r, hy_f_r, gn_z_r, gn_f_r):
        if is_market_error(result):
            return result.message
    if isinstance(bars_raw, str):
        return bars_raw
    from ..utils.market.models import Quote, BoardSnapshot

    if not isinstance(zs_r, BoardSnapshot):
        return "主要指数数据异常"
    if not isinstance(hy_z_r, BoardSnapshot) or not isinstance(hy_f_r, BoardSnapshot):
        return "行业板块数据异常"
    if not isinstance(gn_z_r, BoardSnapshot) or not isinstance(gn_f_r, BoardSnapshot):
        return "概念板块数据异常"

    data_zs_items = board_rows_to_items(zs_r.rows)
    data_hy_z = board_rows_to_items(hy_z_r.rows)
    data_hy_f = board_rows_to_items(hy_f_r.rows)
    data_gn_z = board_rows_to_items(gn_z_r.rows)
    data_gn_f = board_rows_to_items(gn_f_r.rows)
    if isinstance(au_q, Quote):
        data_zs_items.append(from_quote(au_q))
    elif is_market_error(au_q):
        logger.warning(f"[SayuStock] 大盘概览黄金报价跳过: {au_q.message}")
    if isinstance(tlm_q, Quote):
        data_zs_items.append(from_quote(tlm_q))
    elif is_market_error(tlm_q):
        logger.warning(f"[SayuStock] 大盘概览三十债报价跳过: {tlm_q.message}")

    bars = bars_raw if isinstance(bars_raw, dict) else {}

    def _int_list(key: str, size: int) -> List[int]:
        raw = bars.get(key, [])
        if not isinstance(raw, list):
            return [0] * size
        vals = [int(x) if isinstance(x, (int, float, str)) else 0 for x in raw]
        if len(vals) < size:
            vals.extend([0] * (size - len(vals)))
        return vals

    def _int_val(key: str) -> int:
        raw = bars.get(key, 0)
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(float(raw))
            except ValueError:
                return 0
        return 0

    zf: List[int] = _int_list("2", 10)
    df: List[int] = _int_list("3", 10)
    diff_bar: Dict[str, int] = {
        "10+": _int_val("5"),
        "5~10": zf[5] + zf[6] + zf[7] + zf[8] + zf[9],
        "3~5": zf[3] + zf[4],
        "2~3": zf[2],
        "1~2": zf[1],
        "0~1": zf[0],
        "0~-1": df[0],
        "-1~-2": df[1],
        "-2~-3": df[2],
        "-3~-5": df[3] + df[4],
        "-5~-10": df[5] + df[6] + df[7] + df[8] + df[9],
        "-10+": _int_val("6"),
    }
    up_value = (
        diff_bar["0~1"] + diff_bar["1~2"] + diff_bar["2~3"] + diff_bar["3~5"] + diff_bar["5~10"] + diff_bar["10+"]
    )
    down_value = (
        diff_bar["0~-1"]
        + diff_bar["-1~-2"]
        + diff_bar["-2~-3"]
        + diff_bar["-3~-5"]
        + diff_bar["-5~-10"]
        + diff_bar["-10+"]
    )
    _ai_return_market_overview(data_zs_items, data_hy_z, data_hy_f, up_value, down_value, diff_bar)

    h0 = 90
    h = 1060 + 20 * h0
    img = Image.new("RGBA", (1700, h), (7, 9, 27))
    img_draw = ImageDraw.Draw(img)

    bar1 = Image.open(TEXT_PATH / "bar1.png")
    bar2 = Image.open(TEXT_PATH / "bar2.png")
    bar3 = Image.open(TEXT_PATH / "bar3.png")
    bar4 = Image.open(TEXT_PATH / "bar4.png")

    zyzs = [
        "上证指数",
        "中证全指",
        "创业板指",
        "科创综指",
        "沪深300",
        "中证500",
        "中证1000",
        "中证2000",
        "中证A500",
        "北证50",
        # '上证50',
        # '国债指数',
        "黄金9999",
        "三十债主连",
    ]

    # 主要指数
    n = 0
    qz_diff = 0
    sz_diff = 0

    def _match_index(display_name: str, item: DisplayItem) -> bool:
        base = item.name.split("(")[0].strip()
        if display_name == base or display_name in item.name:
            return True
        if display_name == "黄金9999" and ("黄金" in item.name or "AU9999" in item.code):
            return True
        if display_name == "三十债主连" and ("三十债" in item.name or "TL" in item.code.upper()):
            return True
        return False

    for zs_name in zyzs:
        for item in data_zs_items:
            if not _match_index(zs_name, item):
                continue
            if "中证全指" in item.name:
                qz_diff = item.change_pct
            if "上证指数" in item.name:
                sz_diff = item.change_pct
            disp = DisplayItem(
                name=zs_name,
                price=item.price,
                change_pct=item.change_pct,
                amount=item.amount,
                code=item.code,
            )
            zs_img = await draw_block(disp)
            img.paste(zs_img, (25 + 200 * (n % 4), 440 + 140 * (n // 4)), zs_img)
            n += 1
            break

    img_draw.rectangle((16, 434, 834, 584), None, (246, 180, 0), 5)

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
    for dindex, ij_num in enumerate(diff_bar.values().__reversed__()):
        if dindex <= 5:
            color = (23, 199, 30)
        else:
            color = (187, 26, 26)

        if ij_num == 0:
            continue
        offset = dindex * 66
        lenth = int(max_h * ij_num / max_num)
        div_draw.rectangle(
            (45 + offset, 413 - lenth, 81 + offset, 413),
            color,
        )
        div_draw.text(
            (66 + offset, 413 - lenth - 25),
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

    all_f6, f6diff, last_trade_date = await get_hours_from_em()
    all_f6_str = number_to_chinese(all_f6)

    if f6diff > 0:
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

    if last_trade_date is not None:
        today_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days_ago = (today_date - last_trade_date).days
        days_label = {1: "上日", 2: "前日", 3: "三日前"}.get(days_ago, f"{days_ago}日前")
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
        lp = f"{'+' if (lead_pct or 0) >= 0 else ''}{lead_pct}%" if lead_pct is not None else ""
        hy_draw.text((384, 75), lp, dd, ss_font(24), "rm")
        hy_draw.text(
            (384, 30),
            f"{'+' if hy_diff >= 0 else ''}{hy_diff}%",
            (255, 255, 255),
            ss_font(30),
            "rm",
        )
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
