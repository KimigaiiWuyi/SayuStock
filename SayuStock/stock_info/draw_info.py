from pathlib import Path
from datetime import datetime
from typing import Dict, List

from PIL import Image, ImageOps, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as ss_font

from ..utils.image import get_footer
from ..stock_cloudmap.get_cloudmap import get_data
from ..utils.utils import save_history, number_to_chinese
from ..utils.request import get_hours_from_em, get_image_from_em

TEXT_PATH = Path(__file__).parent / 'texture2d'
DIFF_MAP = {
    1.3: '1',
    0.7: '2',
    0.2: '3',
    -0.5: '4',
    -1.3: '5',
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


async def draw_block(zs_diff: Dict, _type: str = 'diff'):
    if _type == 'single':
        zs_diff['f14'] = zs_diff['f58']
        zs_diff['f3'] = zs_diff['f170']
        zs_diff['f6'] = zs_diff['f48']
        zs_diff['f2'] = zs_diff['f43']
        zs_diff['f100'] = '-'

    diff = round(zs_diff['f3'], 2)
    zs_img = Image.new('RGBA', (200, 140))
    zs_draw = ImageDraw.Draw(zs_img)
    if diff >= 0:
        zsc = (140, 18, 22, 55)
        zsc2 = (206, 34, 30)
    else:
        zsc = (59, 140, 18, 55)
        zsc2 = (36, 206, 30)

    zs_draw.rounded_rectangle((15, 13, 185, 127), 0, zsc)

    if len(zs_diff["f14"]) >= 10:
        t_font = ss_font(18)
    else:
        t_font = ss_font(24)

    zs_draw.text(
        (100, 99),
        f'{zs_diff["f14"]}',
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
    data_zs = await get_data('主要指数')
    data_hy = await get_data('行业板块')
    data_gn = await get_data('概念板块')
    data_au = await get_data(
        '118.AU9999',
        'single-stock',
    )
    data_tlm = await get_data(
        '220.TLM',
        'single-stock',
    )
    raw_data = await get_data()

    # data_a500 = await get_data('A500')

    if isinstance(data_zs, str):
        return data_zs
    if isinstance(data_hy, str):
        return data_hy
    if isinstance(data_gn, str):
        return data_gn
    if isinstance(raw_data, str):
        return raw_data
    if isinstance(data_au, str):
        return data_au
    if isinstance(data_tlm, str):
        return data_tlm

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

    diffs = {
        10: [],
        5: [],
        3: [],
        2: [],
        1: [],
        0: [],
        -1: [],
        -2: [],
        -3: [],
        -5: [],
        -10: [],
        -100: [],
    }
    all_f6: float = 0
    for i in raw_data['data']['diff']:
        if i['f6'] != '-':
            all_f6 += i['f6']
        if i['f20'] != '-' and i['f100'] != '-' and i['f3'] != '-':
            for _d in diffs:
                if i['f3'] >= _d:
                    diffs[_d].append(i)
                    break

    img = Image.new('RGBA', (1700, 2260), (7, 9, 27))
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
    sz_diff = 0

    for zs_name in zyzs:
        for zs_diff in data_zs['data']['diff']:
            if zs_name != zs_diff['f14']:
                continue

            if zs_diff['f14'] == '上证指数':
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
    max_num = 0
    max_h = 366
    for ij in diffs:
        ij_num = len(diffs[ij])
        if ij_num > max_num:
            max_num = ij_num

    for dindex, ij in enumerate(diffs.__reversed__()):
        if ij < 0:
            color = (23, 199, 30)
        else:
            color = (187, 26, 26)
        ij_num = len(diffs[ij])
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

    if is_save:
        save_history(all_f6)

    f6diff = await get_hours_from_em()
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
        if sz_diff >= i:
            title_num = DIFF_MAP[i]
            break
    else:
        title_num = 6

    title = Image.open(TEXT_PATH / f'title{title_num}.png')

    img.paste(bar1, (0, 331), bar1)
    img.paste(bar4, (850, 331), bar4)

    img.paste(bar2, (0, 875), bar2)
    img.paste(bar3, (850, 875), bar3)

    img.paste(title, (0, -30), title)

    sorted_hy = sorted(
        data_hy['data']['diff'],
        key=lambda x: x['f3'],
        reverse=True,
    )
    sorted_gn = sorted(
        data_gn['data']['diff'],
        key=lambda x: x['f3'],
        reverse=True,
    )

    await draw_bar(sorted_hy[:20], img, 10, 980)
    await draw_bar(sorted_hy[-1:-21:-1], img, 415, 980)

    await draw_bar(sorted_gn[:20], img, 860, 980)
    await draw_bar(sorted_gn[-1:-21:-1], img, 1265, 980)

    footer = get_footer()
    img.paste(footer, (425, 2210), footer)

    res = await convert_img(img)
    return res


async def draw_bar(sd: List[dict], img: Image.Image, start: int, y: int):
    ls = len(sd)
    for hindex, hy in enumerate(sd):
        hy_diff = hy['f3']
        hy_img = Image.new('RGBA', (425, 60))
        base_o = int(255 * (((ls + 1) - hindex) / ls))
        if hy_diff >= 0:
            hyc2 = (140, 18, 22, base_o)
        else:
            hyc2 = (59, 140, 18, base_o)
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
            (384, 30),
            f'{"+" if hy_diff >= 0 else ""}{hy_diff}%',
            (255, 255, 255),
            ss_font(30),
            'rm',
        )

        img.paste(
            hy_img,
            (start, y + 60 * hindex),
            hy_img,
        )
