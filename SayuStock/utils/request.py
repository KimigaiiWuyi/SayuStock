from io import BytesIO
from typing import Tuple, Optional
from datetime import datetime, timedelta

import aiofiles
from gsuid_core.logger import logger
from aiohttp.client import ClientSession
from PIL import Image, UnidentifiedImageError
from aiohttp.client_exceptions import ClientConnectorError

from .utils import get_file, calculate_difference
from ..stock_config.stock_config import STOCK_CONFIG

minutes: int = STOCK_CONFIG.get_config('mapcloud_refresh_minutes').data

WEBPIC = 'https://webquotepic.eastmoney.com/GetPic.aspx'


async def get_hours_from_em() -> float:
    URL = 'https://push2his.eastmoney.com/api/qt/stock/trends2/get?fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ndays=2'  # noqa: E501
    y = 0
    for mk in ['1.000001', '0.399001']:
        url = URL + '&secid=' + mk
        async with ClientSession() as sess:
            try:
                async with sess.get(url) as res:
                    if res.status == 200:
                        data = await res.json()
                        y += calculate_difference(data['data']['trends'])
            except ClientConnectorError:
                logger.warning(f"[SayuStock]获取{mk}数据失败")
    return y


async def get_image_from_em(
    name: str = '0.899001',
    size: Optional[Tuple[int, int]] = None,
) -> Image.Image:
    url = f'{WEBPIC}?nid={name}&imageType=FFRST&type=ffr'

    file = get_file(name, 'png')
    if file.exists():
        # 检查文件的修改时间是否在一分钟以内
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(
                f"[SayuStock] image文件在{minutes}分钟内，直接返回文件数据。"
            )
            try:
                img = Image.open(file)
                if size:
                    return img.resize(size)
                return img
            except UnidentifiedImageError:
                logger.warning(
                    f"[SayuStock]{name}已存在文件读取失败, 尝试重新下载..."
                )

    async with ClientSession() as sess:
        try:
            logger.info(f'[SayuStock]开始下载: {name} | 地址: {url}')
            async with sess.get(url) as res:
                if res.status == 200:
                    content = await res.read()
                    logger.info(f'[SayuStock]下载成功: {name}')
                else:
                    logger.warning(f"[SayuStock]{name}下载失败")
                    return Image.new('RGBA', (256, 256))
        except ClientConnectorError:
            logger.warning(f"[SayuStock]{name}下载失败")
            return Image.new('RGBA', (256, 256))

    async with aiofiles.open(str(file), "wb") as f:
        await f.write(content)
        stream = BytesIO(content)
        if size:
            return Image.open(stream).resize(size)
        else:
            return Image.open(stream)
