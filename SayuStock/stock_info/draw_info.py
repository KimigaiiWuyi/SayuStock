from typing import List
from pathlib import Path

from PIL import Image, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as ss_font

from ..utils.image import get_footer
from ..stock_cloudmap.get_cloudmap import get_data

TEXT_PATH = Path(__file__).parent / 'texture2d'


async def draw_info_img():
    data_zs = await get_data('主要指数')
    data_hy = await get_data('行业板块')

    if isinstance(data_zs, str):
        return data_zs
    if isinstance(data_hy, str):
        return data_hy

    img = Image.new('RGBA', (850, 1650), (7, 9, 27))
    title = Image.open(TEXT_PATH / 'title.png')
    bar1 = Image.open(TEXT_PATH / 'bar1.png')
    bar2 = Image.open(TEXT_PATH / 'bar2.png')

    zyzs = [
        '上证指数',
        '深证成指',
        '创业板指',
        '创业大盘',
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
    for zs_diff in data_zs['data']['diff']:
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
                f"{'+' if diff >=0 else ''}{diff}%",
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

    img.paste(bar1, (0, 351), bar1)
    img.paste(bar2, (0, 863), bar2)
    img.paste(title, (10, 10), title)

    sorted_hy = sorted(
        data_hy['data']['diff'],
        key=lambda x: x["f3"],
        reverse=True,
    )

    await draw_bar(sorted_hy[:10], img, 10)
    await draw_bar(sorted_hy[-1:-11:-1], img, 415)

    footer = get_footer()
    img.paste(footer, (0, 1605), footer)

    res = await convert_img(img)
    return res


async def draw_bar(sd: List[dict], img: Image.Image, start: int):
    for hindex, hy in enumerate(sd):
        hy_diff = hy['f3']
        hy_img = Image.new('RGBA', (425, 60))
        base_o = int(255 * ((11 - hindex) / 10))
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
            f"{'+' if hy_diff >=0 else ''}{hy_diff}%",
            (255, 255, 255),
            ss_font(30),
            'rm',
        )

        img.paste(
            hy_img,
            (start, 967 + 60 * hindex),
            hy_img,
        )
