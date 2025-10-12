import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, cast

from PIL import Image, ImageOps, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as ss_font

from ..utils.image import get_footer
from ..utils.utils import number_to_chinese
from ..utils.stock.request import get_gg, get_bar, get_mtdata
from ..utils.stock.request_utils import get_hours_from_em, get_image_from_em

TEXT_PATH = Path(__file__).parent / 'texture2d'
DIFF_MAP = {
    3.3: '1',
    2.7: '2',
    2: '3',
    1: '4',
    0: '5',
    -0.5: '6',
    -1.3: '7',
    -2.1: '8',
    -3.1: '9',
    -4: '10',
}


def remove_color_range(img: Image.Image, lower_bound, upper_bound):
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


def invert_colors(img: Image.Image):
    r, g, b, a = img.split()

    rgb = Image.merge('RGB', (r, g, b))
    inverted_rgb = ImageOps.invert(rgb)

    inverted_img = Image.composite(
        Image.merge('RGBA', (*inverted_rgb.split(), a)),
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


async def draw_block(zs_diff: Dict, _type: str = 'diff'):
    if _type == 'single':
        zs_diff['f14'] = zs_diff['f58']
        zs_diff['f3'] = zs_diff['f170']
        zs_diff['f6'] = zs_diff['f48']
        zs_diff['f2'] = zs_diff['f43']
        zs_diff['f100'] = '-'

    if isinstance(zs_diff['f3'], str):
        diff: float = 0
    else:
        diff = round(zs_diff['f3'], 2)

    zs_img = Image.new('RGBA', (200, 140))
    zs_draw = ImageDraw.Draw(zs_img)
    if diff >= 0:
        zsc = calculate_gradient_rgb_from_gray(diff)
        zsc2 = (206, 34, 30)
    else:
        zsc = calculate_gradient_rgb_from_gray(diff)
        zsc2 = (36, 206, 30)

    zs_draw.rounded_rectangle((15, 13, 185, 127), 0, zsc)

    t_font = ss_font(24)

    if len(zs_diff["f14"]) >= 15:
        name = zs_diff["f14"][:6]
    else:
        if len(zs_diff["f14"]) >= 10:
            t_font = ss_font(18)

        name = zs_diff["f14"]

    zs_draw.text(
        (100, 99),
        name,
        (255, 255, 255),
        t_font,
        'mm',
    )

    zs_draw.text(
        (100, 38),
        f'{zs_diff["f2"]}',
        zsc2,
        ss_font(30),
        'mm',
    )

    zs_draw.text(
        (100, 70),
        f'{"+" if diff >= 0 else ""}{diff}%',
        zsc2,
        ss_font(30),
        'mm',
    )
    return zs_img


async def draw_info_img(is_save: bool = False):
    tasks = [
        get_mtdata('主要指数', pz=100),
        get_mtdata('行业板块', po=1),
        get_mtdata('行业板块', po=0),
        get_mtdata('概念板块', po=1),
        get_mtdata('概念板块', po=0),
        get_gg('118.AU9999', 'single-stock'),
        get_gg('220.TLM', 'single-stock'),
        get_bar(),
    ]

    results = await asyncio.gather(*tasks)

    for result in results:
        if isinstance(result, str):
            return result

    (
        data_zs,
        data_hy_z,
        data_hy_f,
        data_gn_z,
        data_gn_f,
        data_au,
        data_tlm,
        bars,
    ) = cast(List[Dict], results)

    data_hy_z = data_hy_z['data']['diff']
    data_hy_f = data_hy_f['data']['diff']
    data_gn_z = data_gn_z['data']['diff']
    data_gn_f = data_gn_f['data']['diff']

    data_aud: Dict = data_au['data']
    data_tlmd: Dict = data_tlm['data']

    data_aud['f14'] = data_aud['f58']
    data_aud['f3'] = data_aud['f170']
    data_aud['f6'] = data_aud['f48']
    data_aud['f2'] = data_aud['f43']
    data_aud['f100'] = '-'

    data_tlmd['f14'] = data_tlmd['f58']
    data_tlmd['f3'] = data_tlmd['f170']
    data_tlmd['f6'] = data_tlmd['f48']
    data_tlmd['f2'] = data_tlmd['f43']
    data_tlmd['f100'] = '-'

    data_zs['data']['diff'].append(data_aud)
    data_zs['data']['diff'].append(data_tlmd)

    zf: List[int] = bars['2']
    df: List[int] = bars['3']
    diff_bar: Dict[str, int] = {
        '10+': bars['5'],
        '5~10': zf[5] + zf[6] + zf[7] + zf[8] + zf[9],
        '3~5': zf[3] + zf[4],
        '2~3': zf[2],
        '1~2': zf[1],
        '0~1': zf[0],
        # '0': bars['4'],
        '0~-1': df[0],
        '-1~-2': df[1],
        '-2~-3': df[2],
        '-3~-5': df[3] + df[4],
        '-5~-10': df[5] + df[6] + df[7] + df[8] + df[9],
        '-10+': bars['6'],
    }
    up_value = (
        diff_bar['0~1']
        + diff_bar['1~2']
        + diff_bar['2~3']
        + diff_bar['3~5']
        + diff_bar['5~10']
        + diff_bar['10+']
    )
    down_value = (
        diff_bar['0~-1']
        + diff_bar['-1~-2']
        + diff_bar['-2~-3']
        + diff_bar['-3~-5']
        + diff_bar['-5~-10']
        + diff_bar['-10+']
    )
    h0 = 90
    h = 1060 + 20 * h0
    img = Image.new('RGBA', (1700, h), (7, 9, 27))
    img_draw = ImageDraw.Draw(img)

    bar1 = Image.open(TEXT_PATH / 'bar1.png')
    bar2 = Image.open(TEXT_PATH / 'bar2.png')
    bar3 = Image.open(TEXT_PATH / 'bar3.png')
    bar4 = Image.open(TEXT_PATH / 'bar4.png')

    zyzs = [
        '上证指数',
        '中证全指',
        '创业板指',
        '科创综指',
        '沪深300',
        '中证500',
        '中证1000',
        '中证2000',
        '中证A500',
        '北证50',
        # '上证50',
        # '国债指数',
        '黄金9999',
        '三十债主连',
    ]

    # 主要指数
    n = 0
    qz_diff = 0
    sz_diff = 0

    for zs_name in zyzs:
        for zs_diff in data_zs['data']['diff']:
            diff_name: str = zs_diff['f14']
            diff_name = diff_name.split('(')[0].strip()

            if zs_name != diff_name:
                continue

            zs_diff['f14'] = zs_name

            if diff_name == '中证全指':
                qz_diff = zs_diff['f3']

            if diff_name == '上证指数':
                sz_diff = zs_diff['f3']

            zs_img = await draw_block(zs_diff)

            img.paste(
                zs_img,
                (25 + 200 * (n % 4), 440 + 140 * (n // 4)),
                zs_img,
            )
            n += 1

    img_draw.rectangle((16, 434, 834, 584), None, (246, 180, 0), 5)

    # 分布统计
    div = Image.open(TEXT_PATH / 'div.png')
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
        f'{down_value}',
        (255, 255, 255),
        ss_font(24),
        'mm',
    )
    div_draw.text(
        (790, 20),
        f'{up_value}',
        (255, 255, 255),
        ss_font(24),
        'mm',
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
            f'{ij_num}',
            (255, 255, 255),
            ss_font(24),
            'mm',
        )
    img.paste(div, (850, 420), div)

    # 流入流出
    web_em_img = await get_image_from_em(size=(500, 274))
    web_em_img = web_em_img.convert('RGBA')
    web_em_img = remove_color_range(
        web_em_img,
        (200, 200, 200),
        (255, 255, 255),
    )
    web_em_img = invert_colors(web_em_img)
    img.paste(web_em_img, (882, 32), web_em_img)

    time_color = (186, 26, 27, 100) if sz_diff >= 0 else (18, 199, 30, 100)
    img_draw.rectangle((1395, 62, 1655, 229), time_color)

    now = datetime.now()
    weekday = now.strftime('星期' + '一二三四五六日'[now.weekday()])
    time = now.strftime('%H:%M')
    date = now.strftime('%Y.%m.%d')

    img_draw.text(
        (1524, 145),
        f'{time}',
        (255, 255, 255),
        ss_font(58),
        'mm',
    )
    img_draw.text(
        (1524, 95),
        f'{weekday}',
        (255, 255, 255),
        ss_font(36),
        'mm',
    )
    img_draw.text(
        (1524, 197),
        f'{date}',
        (255, 255, 255),
        ss_font(36),
        'mm',
    )

    all_f6, f6diff = await get_hours_from_em()
    all_f6_str = number_to_chinese(all_f6)

    if f6diff > 0:
        f6diff_str = f'放量: {number_to_chinese(abs(f6diff))}'
        fcolor = (186, 26, 27, 100)
    else:
        f6diff_str = f'缩量: {number_to_chinese(abs(f6diff))}'
        fcolor = (18, 199, 30, 100)

    img_draw.text(
        (1529, 263),
        f'成交额: {all_f6_str}',
        time_color,
        ss_font(34),
        'mm',
    )
    img_draw.text(
        (1529, 305),
        f6diff_str,
        fcolor,
        ss_font(34),
        'mm',
    )

    for i in DIFF_MAP:
        if qz_diff >= i:
            title_num = DIFF_MAP[i]
            break
    else:
        title_num = 11

    title = Image.open(TEXT_PATH / f'title{title_num}.png')

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


async def draw_bar(
    sd: List[dict], img: Image.Image, start: int, y: int, h: int = 90
):
    ls = len(sd)
    for hindex, hy in enumerate(sd):
        hy_diff = hy['f3']
        hy_img = Image.new('RGBA', (425, h))
        base_o = int(255 * (((ls + 1) - hindex) / ls))
        if hy_diff >= 0:
            hyc2 = (140, 18, 22, base_o)
            dd = (201, 26, 32, 200)
        else:
            hyc2 = (59, 140, 18, base_o)
            dd = (25, 199, 16, 200)

        hy_draw = ImageDraw.Draw(hy_img)
        hy_draw.rounded_rectangle((23, 2, 403, 57), 0, hyc2)
        hy_draw.text(
            (53, 30),
            f'{hy["f14"]}',
            (255, 255, 255),
            ss_font(30),
            'lm',
        )

        hy_draw.text(
            (53, 75),
            f'{hy["f128"] if hy_diff >= 0 else hy["f207"]}',
            dd,
            # (140, 18, 22) if hy_diff >= 0 else (59, 140, 18),
            ss_font(24),
            'lm',
        )
        hy_draw.text(
            (384, 75),
            f'{"+" if hy_diff >= 0 else ""}{hy["f136"] if hy_diff >= 0 else hy["f222"]}%',
            dd,
            # (140, 18, 22) if hy_diff >= 0 else (59, 140, 18),
            ss_font(24),
            'rm',
        )

        hy_draw.text(
            (384, 30),
            f'{"+" if hy_diff >= 0 else ""}{hy_diff}%',
            (255, 255, 255),
            ss_font(30),
            'rm',
        )

        img.paste(
            hy_img,
            (start, y + h * hindex),
            hy_img,
        )
