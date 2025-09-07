import json
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import aiofiles
from gsuid_core.logger import logger
from PIL import Image, UnidentifiedImageError
from aiohttp import (
    ClientSession,
    ClientConnectorError,
)

from ..constant import code_id_dict
from .utils import get_file, calculate_difference
from ...stock_config.stock_config import STOCK_CONFIG


async def get_hours_from_em() -> Tuple[float, float]:
    URL = 'https://push2his.eastmoney.com/api/qt/stock/trends2/get?fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ndays=2'  # noqa: E501
    y = 0
    ya = 0
    for mk in ['1.000001', '0.399001']:
        url = URL + '&secid=' + mk
        async with ClientSession() as sess:
            try:
                async with sess.get(url) as res:
                    if res.status == 200:
                        data = await res.json()
                        ya0, y0 = calculate_difference(data['data']['trends'])
                        y += y0
                        ya += ya0
            except ClientConnectorError:
                logger.warning(f"[SayuStock]获取{mk}数据失败")
    return ya, y


async def get_code_id(
    code: str, priority: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    """
    生成东方财富股票专用的行情ID
    code:可以是代码或简称或英文
    """
    if code.endswith('.h'):
        code = code.replace('.h', '')
        priority = 'h'
    elif code.endswith('.hk'):
        code = code.replace('.hk', '')
        priority = 'h'
    elif code.endswith('.us'):
        code = code.replace('.us', '')
        priority = 'us'
    elif code.endswith('.a'):
        code = code.replace('.a', '')
        priority = 'a'

    if priority is not None:
        priority = priority.lower()

    if '.' in code:
        return code, ''
    if code in code_id_dict.keys():
        return code_id_dict[code], code
    url = 'https://searchapi.eastmoney.com/api/suggest/get'
    params = (
        ('input', f'{code}'),
        ('type', '14'),
        ('token', 'D43BF722C8E33BDC906FB84D85E326E8'),
        ('count', '4'),
    )
    async with ClientSession() as sess:
        async with sess.get(url, params=params) as res:
            if res.status == 200:
                logger.debug(f"[SayuStock]开始获取{code}的ID")
                text = await res.text()
                logger.debug(text)
                data = json.loads(text)
                code_dict: List[Dict] = data['QuotationCodeTable']['Data']
                if code_dict:
                    # 排序：SecurityTypeName为"债券"的排到最后
                    code_dict.sort(
                        key=lambda x: x.get('SecurityTypeName') == '债券'
                    )
                    for i in code_dict:
                        if priority is None:
                            return i['QuoteID'], i['Name']
                        elif priority == 'h':
                            if i['SecurityTypeName'] in ['港股']:
                                return i['QuoteID'], i['Name']
                        elif priority == 'us':
                            if i['SecurityTypeName'] in ['美股', '粉单']:
                                return i['QuoteID'], i['Name']
                        elif priority == 'a':
                            if i['SecurityTypeName'] in [
                                '沪深A',
                                '沪A',
                                '深A',
                            ]:
                                return i['QuoteID'], i['Name']
                    else:
                        return code_dict[0]['QuoteID'], code_dict[0]['Name']
                else:
                    return None
    return None


async def get_image_from_em(
    name: str = '0.899001',
    size: Optional[Tuple[int, int]] = None,
) -> Image.Image:
    WEBPIC = 'https://webquotepic.eastmoney.com/GetPic.aspx'
    url = f'{WEBPIC}?nid={name}&imageType=FFRST&type=ffr'

    file = get_file(name, 'png')
    if file.exists():
        # 检查文件的修改时间是否在一分钟以内
        minutes = STOCK_CONFIG.get_config('mapcloud_refresh_minutes').data
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
            return Image.open(stream)
