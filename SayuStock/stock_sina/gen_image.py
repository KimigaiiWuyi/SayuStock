from io import BytesIO
from typing import List

import aiohttp
from PIL import Image

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.stock.request_utils import get_code_id


async def get_sina_pepb_compare(_input: str, _type: str = "pe"):
    _input = _input.replace(",", " ")
    _list = _input.split()
    _id_list: List[str] = []
    _name_list: List[str] = []
    for i in _list:
        _id = await get_code_id(i)
        if _id is not None:
            _id_list.append(_id[0].split(".")[1])
            _name_list.append(f"{_id[1]}({_id[0]})")

    img = await fetch_sina_image(
        _id_list,
        720,
        _type,
    )
    res = await convert_img(img)

    # AI 注入：在返回图片前提供文字摘要
    _type_name = "市盈率(PE)" if _type == "pe" else "市净率(PB)"
    _ai_return_pepb_compare(_name_list, _type_name)

    return res


def _ai_return_pepb_compare(name_list, type_name):
    """从PE/PB对比数据中提取文本信息，通过 ai_return 返回给 AI"""
    try:
        stocks = "、".join(name_list) if name_list else "未知"
        ai_return(f"【{type_name}对比】\n对比标的: {stocks}\n已生成{type_name}历史走势对比图")
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return {type_name}对比数据提取失败: {e}")


async def fetch_sina_image(stock_codes: List[str], limit: int = 720, _type: str = "pe") -> Image.Image:
    """
    异步获取新浪财经股票对比图，并返回 PIL.Image 对象。

    Args:
        stock_codes (List[str]): 股票代码列表，例如 ["601919", "512880"]
        limit (int): 限制数据数量

    Returns:
        Image.Image: 返回的 PIL 图像对象
    """
    stock_code_str = ",".join(stock_codes)

    if _type == "pb":
        _tool = "img_sjl_compare"
    else:
        _tool = "img_syl_compare"

    url = f"https://biz.finance.sina.com.cn/company/compare/{_tool}.php?stock_code={stock_code_str}&limit={limit}"

    logger.info(f"[SayuStock][Sina]: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, ssl=False) as response:
            if response.status != 200:
                raise ValueError(f"请求失败，状态码：{response.status}")
            img_bytes = await response.read()

    return Image.open(BytesIO(img_bytes))
