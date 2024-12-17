from typing import List
from pathlib import Path

from PIL import Image, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as ss_font

from ..utils.image import get_footer
from ..stock_cloudmap.get_cloudmap import get_data

TEXT_PATH = Path(__file__).parent / 'texture2d'
DIFF_MAP = {
    1.3: '1',
    0.7: '2',
    0.2: '3',
    -0.5: '4',
    -1.3: '5',
}


async def draw_info_img():
    data_zs = await get_data('主要指数')
    data_hy = await get_data('行业板块')
    data_gn = await get_data('概念板块')
    # data_a500 = await get_data('A500')

    if isinstance(data_zs, str):
        return data_zs
    if isinstance(data_hy, str):
        return data_hy
    if isinstance(data_gn, str):
        return data_gn

    img = Image.new('RGBA', (850, 2260), (7, 9, 27))

    bar1 = Image.open(TEXT_PATH / 'bar1.png')
    bar2 = Image.open(TEXT_PATH / 'bar2.png')
    bar3 = Image.open(TEXT_PATH / 'bar3.png')

    zyzs = [
        '上证指数',
        '深证成指',
        '创业板指',
        '中证A500',
        '北证50',
        '中证全指',
        '上证50',
        '沪深300',
        '中证500',
        '中证1000',
        '中证2000',
        '国债指数',
    ]

    n = 0
    sz_diff = 0

    for zs_diff in data_zs['data']['diff']:
        if zs_diff['f14'] == '上证指数':
            sz_diff = zs_diff['f3']
        if zs_diff['f14'] in zyzs:
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
                f"{zs_diff['f14']}",
                (255, 255, 255),
                ss_font(24),
                'mm',
            )

            zs_draw.text(
                (100, 38),
                f"{zs_diff['f2']}",
                zsc2,
                ss_font(30),
                'mm',
            )

            zs_draw.text(
                (100, 70),
                f"{'+' if diff >= 0 else ''}{diff}%",
                zsc2,
                ss_font(30),
                'mm',
            )
            img.paste(
                zs_img,
                (25 + 200 * (n % 4), 420 + 140 * (n // 4)),
                zs_img,
            )
            n += 1

    for i in DIFF_MAP:
        if sz_diff >= i:
            title_num = DIFF_MAP[i]
            break
    else:
        title_num = 6

    title = Image.open(TEXT_PATH / f'title{title_num}.png')

    img.paste(bar1, (0, 331), bar1)
    img.paste(bar2, (0, 843), bar2)
    img.paste(bar3, (0, 1730), bar3)
    img.paste(title, (0, -30), title)

    sorted_hy = sorted(
        data_hy['data']['diff'],
        key=lambda x: x["f3"],
        reverse=True,
    )
    sorted_gn = sorted(
        data_gn['data']['diff'],
        key=lambda x: x["f3"],
        reverse=True,
    )

    await draw_bar(sorted_hy[:13], img, 10, 947)
    await draw_bar(sorted_hy[-1:-14:-1], img, 415, 947)

    await draw_bar(sorted_gn[:6], img, 10, 1830)
    await draw_bar(sorted_gn[-1:-7:-1], img, 415, 1830)

    footer = get_footer()
    img.paste(footer, (0, 2210), footer)

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
            f"{hy['f14']}",
            (255, 255, 255),
            ss_font(30),
            'lm',
        )

        hy_draw.text(
            (384, 30),
            f"{'+' if hy_diff >= 0 else ''}{hy_diff}%",
            (255, 255, 255),
            ss_font(30),
            'rm',
        )

        img.paste(
            hy_img,
            (start, y + 60 * hindex),
            hy_img,
        )
