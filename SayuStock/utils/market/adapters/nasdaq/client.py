"""纳斯达克官方 IPO 日历 HTTP：api.nasdaq.com（易限流，带重试）。"""

from __future__ import annotations

import asyncio

from aiohttp import ClientError, ClientSession, ClientTimeout

from gsuid_core.logger import logger

from ...errors import MarketError, parse_error, network_error
from ....stock.utils import async_file_cache

PROVIDER = "nasdaq"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nasdaq.com/market-activity/ipos",
}
CALENDAR_URL = "https://api.nasdaq.com/api/ipo/calendar"
# 实测偶发空响应/阻断，重试 3 次（间隔递增）后仍失败才落缓存
_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.5


async def _request_once(month: str) -> object | MarketError:
    timeout = ClientTimeout(total=20)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(CALENDAR_URL, params={"date": month, "limit": "60"}) as res:
                if res.status != 200:
                    return network_error(f"纳斯达克 HTTP {res.status}", provider=PROVIDER)
                try:
                    payload: object = await res.json(content_type=None)
                except (ValueError, TypeError) as e:
                    return parse_error(f"纳斯达克 JSON 无效: {e}", provider=PROVIDER)
                return payload
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][纳斯达克] 请求失败: {e}")
        return network_error(str(e), provider=PROVIDER)


@async_file_cache(market="nasdaq-ipo", sector="{month}", suffix="json", minutes=30)
async def fetch_calendar_month(month: str) -> object | str:
    """一个月的 IPO 日历原始 payload；month 形如 ``2026-09``。

    重试在缓存函数内部：只有重试穷尽后的结果才会落盘。
    """
    result: object | MarketError = network_error("未执行", provider=PROVIDER)
    for attempt in range(_ATTEMPTS):
        result = await _request_once(month)
        if not isinstance(result, MarketError):
            return result
        if attempt < _ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_BASE_DELAY + attempt)
    assert isinstance(result, MarketError)
    return result.message
