import json
from io import BytesIO
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple, Union, Literal, Optional, cast

import aiofiles
from gsuid_core.logger import logger
from PIL import Image, UnidentifiedImageError
from playwright.async_api import async_playwright
from aiohttp import (
    FormData,
    TCPConnector,
    ClientSession,
    ClientTimeout,
    ContentTypeError,
    ClientConnectorError,
)

from .models import XueQiu7x24
from .constant import code_id_dict
from .utils import get_file, calculate_difference
from ..stock_config.stock_config import STOCK_CONFIG

minutes: int = STOCK_CONFIG.get_config('mapcloud_refresh_minutes').data
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'  # noqa: E501

WEBPIC = 'https://webquotepic.eastmoney.com/GetPic.aspx'

_HEADER: Dict[str, str] = {
    'User-Agent': UA,
    'Referer': 'https://xueqiu.com/',
}

NEWS_API = 'https://xueqiu.com/statuses/livenews/list.json'

NEWS: XueQiu7x24 = {
    'next_max_id': 0,
    'items': [],
    'next_id': 0,
}
XUEQIU_TOKEN = ''


async def get_token():
    global XUEQIU_TOKEN
    async with async_playwright() as p:
        # 启动浏览器（默认 Chromium）
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled"
            ],  # 禁用自动化检测
        )

        # 创建上下文和页面
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        try:
            # 导航到目标页面
            await page.goto(
                "https://xueqiu.com/", wait_until="networkidle", timeout=15000
            )
            # 获取所有 Cookie
            cookies = await context.cookies()
            logger.debug(f'[SayuStock] 获取Cookie: {cookies}')
            cl = [f"{cookie['name']}={cookie['value']}" for cookie in cookies]  # type: ignore # noqa: E501
            XUEQIU_TOKEN = ';'.join(cl)
            _HEADER['Cookie'] = XUEQIU_TOKEN
            logger.debug(f'[SayuStock] 设置Cookie: {XUEQIU_TOKEN}')
            return XUEQIU_TOKEN
        finally:
            await browser.close()


'''
async def get_token() -> str:
    global XUEQIU_TOKEN
    url = "https://xueqiu.com/?md5__1038=QqGxcDnDyiitnD05o4%2Br%3DD9lRKTMqD5dx"

    async with ClientSession() as session:
        async with session.get(url) as resp:
            cookies = resp.headers.getall('set-cookie', [])
            token = "; ".join(cookies)
            XUEQIU_TOKEN = token
            logger.debug(f'[SayuStock] 获取Token: {XUEQIU_TOKEN}')
            return token
'''


async def clean_news():
    global NEWS
    NEWS = {
        'next_max_id': 0,
        'items': [],
        'next_id': 0,
    }


async def get_news_list(
    max_id: int = 0,
) -> Union[int, XueQiu7x24]:
    params = {
        'count': 15,
        'max_id': max_id,
    }
    data = await stock_request(NEWS_API, params=params)
    if isinstance(data, int):
        return data
    data = cast(XueQiu7x24, data)
    return data


async def get_news(
    max_id: int = 0,
) -> Union[int, Tuple[int, XueQiu7x24]]:
    global NEWS
    _max_id = max_id
    return_max_id = max_id

    for i in range(3):
        data = await get_news_list(max_id=_max_id)
        if isinstance(data, int):
            return data
        data = cast(XueQiu7x24, data)

        if i == 0:
            return_max_id = data['items'][0]['id']

        if data['items'][0]['id'] <= max_id:
            break

        for item in data['items']:
            if item['id'] >= max_id:
                NEWS['items'].append(item)

        NEWS['next_id'] = data['next_id']
        NEWS['next_max_id'] = data['next_max_id']
        _max_id = data['next_max_id']

    return return_max_id, NEWS


async def stock_request(
    url: str,
    method: Literal['GET', 'POST'] = 'GET',
    header: Dict[str, str] = _HEADER,
    params: Optional[Dict[str, Any]] = None,
    _json: Optional[Dict[str, Any]] = None,
    data: Optional[FormData] = None,
) -> Union[Dict, int]:
    async with ClientSession(
        connector=TCPConnector(verify_ssl=True)
    ) as client:
        for _ in range(2):
            async with client.request(
                method,
                url=url,
                headers=header,
                params=params,
                json=_json,
                data=data,
                timeout=ClientTimeout(total=300),
            ) as resp:
                try:
                    raw_data = await resp.json()
                except (ContentTypeError, json.decoder.JSONDecodeError):
                    _raw_data = await resp.text()
                    raw_data = {'code': -999, 'data': _raw_data}
                logger.debug(raw_data)
                if (
                    'error_code' in raw_data
                    and raw_data['error_code'] == '400016'
                ):
                    await get_token()
                    continue

                if resp.status != 200:
                    logger.error(
                        f'[SayuStock] 访问 {url} 失败, 错误码: {resp.status}'
                        f', 错误返回: {raw_data}'
                    )
                    return -999
                return raw_data
        else:
            return -400016


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


async def get_code_id(code: str) -> Optional[Tuple[str, str]]:
    """
    生成东方财富股票专用的行情ID
    code:可以是代码或简称或英文
    """
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
                    return code_dict[0]['QuoteID'], code_dict[0]['Name']
                else:
                    return None
    return None


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
