from typing import List
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as ss_font

from ..utils.image import get_footer
from ..utils.request import get_image_from_em
from ..stock_cloudmap.get_cloudmap import get_data

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


async def draw_info_img():
    data_zs = await get_data('主要指数')
    data_hy = await get_data('行业板块')
    data_gn = await get_data('概念板块')
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

    for i in raw_data['data']['diff']:
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
        '深证成指',
        '创业板指',
        '中证A500',
        '沪深300',
        '中证500',
        '中证1000',
        '中证2000',
        '北证50',
        '中证全指',
        '上证50',
        '国债指数',
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

            diff = zs_diff['f3']
            zs_img = Image.new('RGBA', (200, 140))
            zs_draw = ImageDraw.Draw(zs_img)
            if diff >= 0:
                zsc = (140, 18, 22, 55)
                zsc2 = (206, 34, 30)
            else:
                zsc = (59, 140, 18, 55)
                zsc2 = (36, 206, 30)

            zs_draw.rounded_rectangle((15, 13, 185, 127), 0, zsc)

            zs_draw.text(
                (100, 99),
                f'{zs_diff['f14']}',
                (255, 255, 255),
                ss_font(24),
                'mm',
            )

            zs_draw.text(
                (100, 38),
                f'{zs_diff['f2']}',
                zsc2,
                ss_font(30),
                'mm',
            )

            zs_draw.text(
                (100, 70),
                f'{'+' if diff >= 0 else ''}{diff}%',
                zsc2,
                ss_font(30),
                'mm',
            )
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
    img_draw.rectangle((1395, 92, 1655, 259), time_color)

    now = datetime.now()
    weekday = now.strftime('星期' + '一二三四五六日'[now.weekday()])
    time = now.strftime('%H:%M')
    date = now.strftime('%Y.%m.%d')

    img_draw.text(
        (1524, 175),
        f'{time}',
        (255, 255, 255),
        ss_font(58),
        'mm',
    )
    img_draw.text(
        (1524, 125),
        f'{weekday}',
        (255, 255, 255),
        ss_font(36),
        'mm',
    )
    img_draw.text(
        (1524, 227),
        f'{date}',
        (255, 255, 255),
        ss_font(36),
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
            f'{hy['f14']}',
            (255, 255, 255),
            ss_font(30),
            'lm',
        )

        hy_draw.text(
            (384, 30),
            f'{'+' if hy_diff >= 0 else ''}{hy_diff}%',
            (255, 255, 255),
            ss_font(30),
            'rm',
        )

        img.paste(
            hy_img,
            (start, y + 60 * hindex),
            hy_img,
        )
