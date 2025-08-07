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
from ..utils.get_OKX import CRYPTO_MAP, get_all_crypto_price

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
    data4 = await _get_data(whsc)
    data5 = await get_all_crypto_price()

    img = Image.open(TEXT_PATH / 'bg1.jpg').convert('RGBA')

    ox = 223
    oy = 140

    data_gz: List[Dict] = data1['data']['diff']

    async def paste_blocks(data_list, keys, y_base, block_type=None):
        index = 0
        for d in keys:
            for i in data_list:
                item = data_list[i] if isinstance(data_list, dict) else i
                if item.get('f58', item.get('f14')) != d:
                    continue
                block = (
                    await draw_block(item, block_type)
                    if block_type
                    else await draw_block(item)
                )
                img.paste(
                    block,
                    (62 + ox * (index % 4), y_base + oy * (index // 4)),
                    block,
                )
                index += 1

    # 指数
    await paste_blocks(data_gz, i_code, 487)
    # 商品
    await paste_blocks(data2, commodity, 1007, 'single')
    # 债券
    await paste_blocks(data3, bond, 1395, 'single')
    # 外汇
    await paste_blocks(data4, whsc, 1773, 'single')
    # 加密货币
    await paste_blocks(data5, CRYPTO_MAP, 1988, 'single')

    footer = get_footer()

    img.paste(footer, (75, 2135), footer)
    res = await convert_img(img)

    return res
