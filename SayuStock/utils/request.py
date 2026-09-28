import json
import time
from typing import Any, Dict, Tuple, Union, Literal, Optional

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


async def get_token() -> object:
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
            cl = [f"{cookie['name']}={cookie['value']}" for cookie in cookies if "name" in cookie and "value" in cookie]
            XUEQIU_TOKEN = ";".join(cl)
            _HEADER["Cookie"] = XUEQIU_TOKEN
            logger.debug(f"[SayuStock] 设置Cookie: {XUEQIU_TOKEN}")
            return XUEQIU_TOKEN
        finally:
            await browser.close()


# 新闻缓存保留时长。小时/交易时段/每日汇总推送需要取到隔夜消息，
# 不能在 00:00 整表清空，只裁掉超过该时长的旧新闻，内存仍有界。
# 取 48h 而非 24h：漏推一次「每日 08:00」时，下一次汇总仍要能补齐前一天的量。
NEWS_RETENTION_MS = 48 * 60 * 60 * 1000

# 逐条实时只需最新几页；cover_ms 回填时按覆盖窗口决定翻多少页，页数上限兜底
_NEWS_LIVE_PAGES = 3
_NEWS_MAX_PAGES = 60


async def clean_news() -> None:
    """按 created_at 裁剪新闻缓存中超过保留时长的旧条目"""
    now_ms = int(time.time() * 1000)
    NEWS["items"] = [i for i in NEWS["items"] if now_ms - i["created_at"] <= NEWS_RETENTION_MS]


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
    # 显式按 TypedDict 结构取值：接口字段缺失时立即 KeyError，而非默默透传脏数据
    return XueQiu7x24(
        next_max_id=data["next_max_id"],
        items=data["items"],
        next_id=data["next_id"],
    )


async def get_news(
    max_id: int = 0,
    cover_ms: int = 0,
) -> Union[int, Tuple[int, XueQiu7x24]]:
    """取雪球7x24新闻并合并进全局 NEWS 缓存。

    cover_ms > 0 时持续翻页，直到缓存已覆盖该时长（汇总推送靠它回填隔夜/隔日区间，
    进程重启后缓存是空的，否则只能拿到最近 45 条）；为 0 时只取最新 _NEWS_LIVE_PAGES 页。
    """
    global NEWS
    _max_id = max_id
    return_max_id = max_id
    # 用 seen_ids 防止同一新闻被反复 append 进全局 NEWS
    seen_ids: set = {item["id"] for item in NEWS["items"]}
    now_ms = int(time.time() * 1000)

    cached_oldest = min((item["created_at"] for item in NEWS["items"]), default=now_ms)
    need_cover = bool(cover_ms) and now_ms - cached_oldest < cover_ms
    max_pages = _NEWS_MAX_PAGES if need_cover else _NEWS_LIVE_PAGES
    oldest_ms: Optional[int] = None

    for i in range(max_pages):
        data = await get_news_list(max_id=_max_id)
        if isinstance(data, int):
            return data

        if not data["items"]:
            break

        if i == 0:
            return_max_id = data["items"][0]["id"]

        if data["items"][0]["id"] <= max_id:
            break

        for item in data["items"]:
            if item["id"] > max_id and item["id"] not in seen_ids:
                NEWS["items"].append(item)
                seen_ids.add(item["id"])

        page_oldest = min(item["created_at"] for item in data["items"])
        oldest_ms = page_oldest if oldest_ms is None else min(oldest_ms, page_oldest)

        NEWS["next_id"] = data["next_id"]
        NEWS["next_max_id"] = data["next_max_id"]
        _max_id = data["next_max_id"]

        # 已经翻到比覆盖窗口更旧的条目就不必再翻
        if need_cover and oldest_ms is not None and now_ms - oldest_ms >= cover_ms:
            break

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
