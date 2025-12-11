import random
import asyncio
from typing import Any, Dict, List, Union, Callable, Optional
from pathlib import Path

from PIL import Image

from gsuid_core.utils.image.convert import convert_img

from .draw_info import draw_block
from .get_jp_data import get_jpy
from ..utils.image import get_footer
from ..utils.get_OKX import CRYPTO_MAP, get_all_crypto_price
from ..utils.constant import bond, whsc, i_code, commodity
from ..utils.stock.request import get_gg, get_mtdata

TEXT_PATH = Path(__file__).parent / "texture2d"
DataLike = Optional[Union[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]]


async def __get_data(result: Dict, stock: str):
    await asyncio.sleep(random.uniform(0.2, 1))
    data = await get_gg(stock, "single-stock")
    if isinstance(data, str):
        return data
    pure_name = data["data"]["f58"].split(" (")[0]
    data["data"]["f58"] = pure_name
    result[pure_name] = data["data"]
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
    data1 = await get_mtdata("国际市场")
    if isinstance(data1, str):
        return data1

    # 并发获取数据
    results = await asyncio.gather(
        _get_data(commodity),
        _get_data(bond, append_jpy),
        _get_data(whsc),
        get_all_crypto_price(),
        return_exceptions=True,
    )

    def safe_data(result) -> DataLike:
        if isinstance(result, Exception):
            return None
        return result

    data2: DataLike = safe_data(results[0])
    data3: DataLike = safe_data(results[1])
    data4: DataLike = safe_data(results[2])
    data5: DataLike = safe_data(results[3])

    img = Image.open(TEXT_PATH / "bg1.jpg").convert("RGBA")
    ox = 223
    oy = 140
    data_gz: List[Dict] = data1["data"]["diff"]

    async def paste_blocks(data_list: DataLike, keys, y_base, block_type=None):
        if data_list is None:
            return

        index = 0
        # 统一迭代：支持 dict.values() 或 list
        items = data_list.values() if isinstance(data_list, dict) else data_list
        for d in keys:
            for item in items:
                name = item.get("f58", item.get("f14"))
                if name != d:
                    continue
                block = await draw_block(item, block_type) if block_type else await draw_block(item)
                img.paste(
                    block,
                    (62 + ox * (index % 4), y_base + oy * (index // 4)),
                    block,
                )
                index += 1

    # 绘制各板块
    await paste_blocks(data_gz, i_code, 487)
    await paste_blocks(data2, commodity, 1007, "single")
    await paste_blocks(data3, bond, 1395, "single")
    await paste_blocks(data4, whsc, 1773, "single")
    await paste_blocks(data5, CRYPTO_MAP, 1988, "single")

    footer = get_footer()
    img.paste(footer, (75, 2135), footer)
    res = await convert_img(img)
    return res
