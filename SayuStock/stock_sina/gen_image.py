from io import BytesIO
from typing import List

import aiohttp
from PIL import Image

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img

from ..utils.stock.request_utils import get_code_id


async def get_sina_pe_compare(_input: str):
    _input = _input.replace(",", " ")
    _list = _input.split()
    _id_list: List[str] = []
    for i in _list:
        _id = await get_code_id(i)
        if _id is not None:
            _id_list.append(_id[0].split(".")[1])

    img = await fetch_sina_image(_id_list, 720)
    res = await convert_img(img)
    return res


async def fetch_sina_image(stock_codes: List[str], limit: int = 720) -> Image.Image:
    """
    异步获取新浪财经股票对比图，并返回 PIL.Image 对象。

    Args:
        stock_codes (List[str]): 股票代码列表，例如 ["601919", "512880"]
        limit (int): 限制数据数量

    Returns:
        Image.Image: 返回的 PIL 图像对象
    """
    stock_code_str = ",".join(stock_codes)
    url = (
        f"https://biz.finance.sina.com.cn/company/compare/img_syl_compare.php?stock_code={stock_code_str}&limit={limit}"
    )

    logger.info(f"[SayuStock][Sina]: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, ssl=False) as response:
            if response.status != 200:
                raise ValueError(f"请求失败，状态码：{response.status}")
            img_bytes = await response.read()

    return Image.open(BytesIO(img_bytes))
