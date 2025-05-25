import asyncio
from pathlib import Path
from typing import Dict, List, Callable, Optional

from PIL import Image
from gsuid_core.utils.image.convert import convert_img

from .get_jp_data import get_jpy
from .draw_info import draw_block
from ..utils.image import get_footer
from ..stock_cloudmap.get_cloudmap import get_data
from ..utils.constant import bond, whsc, i_code, commodity

TEXT_PATH = Path(__file__).parent / 'texture2d'


async def __get_data(result: Dict, stock: str):
    data = await get_data(stock, 'single-stock')
    if isinstance(data, str):
        return data
    result[data['data']['f58']] = data['data']
    return result


async def _get_data(_d: Dict, other_call: Optional[Callable] = None):
    TASK = []
    result = {}
    if other_call:
        TASK.append(other_call(result))

    for i in _d:
        if _d[i]:
            TASK.append(__get_data(result, _d[i]))

    await asyncio.gather(*TASK)
    return result


async def append_jpy(result: Dict):
    data = await get_jpy()
    if data is None:
        return result
    result.update(data)
    return result


async def draw_future_img():
    data1 = await get_data('国际市场')

    if isinstance(data1, str):
        return data1

    data2 = await _get_data(commodity)
    data3 = await _get_data(bond, append_jpy)
    print(data3)
    data4 = await _get_data(whsc)

    img = Image.open(TEXT_PATH / 'bg1.jpg').convert('RGBA')

    ox = 223
    oy = 140

    data_gz: List[Dict] = data1['data']['diff']
    index = 0
    for d in i_code:
        for i in data_gz:
            if i['f14'] != d:
                continue
            block = await draw_block(i)
            img.paste(
                block,
                (62 + ox * (index % 4), 487 + oy * (index // 4)),
                block,
            )
            index += 1

    index = 0
    for d in commodity:
        for i in data2:
            if data2[i]['f58'] != d:
                continue
            block = await draw_block(data2[i], 'single')
            img.paste(
                block,
                (62 + ox * (index % 4), 1007 + oy * (index // 4)),
                block,
            )
            index += 1

    index = 0
    for d in bond:
        for i in data3:
            if data3[i]['f58'] != d:
                continue
            block = await draw_block(data3[i], 'single')
            img.paste(
                block,
                (62 + ox * (index % 4), 1395 + oy * (index // 4)),
                block,
            )
            index += 1

    index = 0
    for d in whsc:
        for i in data4:
            if data4[i]['f58'] != d:
                continue
            block = await draw_block(data4[i], 'single')
            img.paste(
                block,
                (62 + ox * (index % 4), 1773 + oy * (index // 4)),
                block,
            )
            index += 1

    footer = get_footer()

    img.paste(footer, (75, 1951), footer)
    img = await convert_img(img)

    return img
