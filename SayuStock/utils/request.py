import json
from typing import Any, Dict, Tuple, Union, Literal, Optional, cast

from aiohttp import (
    FormData,
    TCPConnector,
    ClientSession,
    ClientTimeout,
    ContentTypeError,
)
from playwright.async_api import async_playwright

from gsuid_core.logger import logger

from .models import XueQiu7x24

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"  # noqa: E501


_HEADER: Dict[str, str] = {
    "User-Agent": UA,
    "Referer": "https://xueqiu.com/",
}

NEWS_API = "https://xueqiu.com/statuses/livenews/list.json"

NEWS: XueQiu7x24 = {
    "next_max_id": 0,
    "items": [],
    "next_id": 0,
}
XUEQIU_TOKEN = ""


async def get_token():
    global XUEQIU_TOKEN
    async with async_playwright() as p:
        # 启动浏览器（默认 Chromium）
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],  # 禁用自动化检测
        )

        # 创建上下文和页面
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        try:
            # 导航到目标页面
            await page.goto("https://xueqiu.com/", wait_until="networkidle", timeout=15000)
            # 获取所有 Cookie
            cookies = await context.cookies()
            logger.debug(f"[SayuStock] 获取Cookie: {cookies}")
            cl = [f"{cookie['name']}={cookie['value']}" for cookie in cookies]  # type: ignore # noqa: E501
            XUEQIU_TOKEN = ";".join(cl)
            _HEADER["Cookie"] = XUEQIU_TOKEN
            logger.debug(f"[SayuStock] 设置Cookie: {XUEQIU_TOKEN}")
            return XUEQIU_TOKEN
        finally:
            await browser.close()


async def clean_news():
    global NEWS
    NEWS = {
        "next_max_id": 0,
        "items": [],
        "next_id": 0,
    }


async def get_news_list(
    max_id: int = 0,
) -> Union[int, XueQiu7x24]:
    params = {
        "count": 15,
        "max_id": max_id,
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
            return_max_id = data["items"][0]["id"]

        if data["items"][0]["id"] <= max_id:
            break

        for item in data["items"]:
            if item["id"] >= max_id:
                NEWS["items"].append(item)

        NEWS["next_id"] = data["next_id"]
        NEWS["next_max_id"] = data["next_max_id"]
        _max_id = data["next_max_id"]

    return return_max_id, NEWS


async def stock_request(
    url: str,
    method: Literal["GET", "POST"] = "GET",
    header: Dict[str, str] = _HEADER,
    params: Optional[Dict[str, Any]] = None,
    _json: Optional[Dict[str, Any]] = None,
    data: Optional[FormData] = None,
) -> Union[Dict, int]:
    async with ClientSession(connector=TCPConnector(verify_ssl=True)) as client:
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
                    raw_data = {"code": -999, "data": _raw_data}
                logger.debug(raw_data)
                if "error_code" in raw_data and raw_data["error_code"] == "400016":
                    await get_token()
                    continue

                if resp.status != 200:
                    logger.error(f"[SayuStock] 访问 {url} 失败, 错误码: {resp.status}, 错误返回: {raw_data}")
                    return -999
                return raw_data
        else:
            return -400016
