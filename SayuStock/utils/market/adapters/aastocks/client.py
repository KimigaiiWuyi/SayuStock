"""AAStocks 港股 IPO 页面 HTTP：主页面（已上市表现）+ 即将上市页（招股日程）。

仅作东财港股列表的**增强源**（招股截止日/暗盘/上市价/超购倍数/首日表现），
失败时跳过增强，不影响底层数据。页面 UTF-8、服务端渲染。
"""

from __future__ import annotations

from aiohttp import ClientError, ClientSession, ClientTimeout

from gsuid_core.logger import logger

from ....stock.utils import async_file_cache

PROVIDER = "aastocks"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
MAINPAGE_URL = "http://www.aastocks.com/tc/stocks/market/ipo/mainpage.aspx"
UPCOMING_URL = "http://www.aastocks.com/tc/stocks/market/ipo/upcomingipo/"


async def _get_html(url: str) -> str | None:
    timeout = ClientTimeout(total=20)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(url) as res:
                if res.status != 200:
                    logger.warning(f"[SayuStock][AAStocks] HTTP {res.status}: {url}")
                    return None
                return await res.text(errors="replace")
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][AAStocks] 请求失败: {e}")
        return None


@async_file_cache(market="aastocks-ipo", sector="mainpage", suffix="html", minutes=60)
async def fetch_mainpage() -> str:
    """新股主页：招股中表 + 已上市表现表。空串表示抓取失败（同样落缓存防抖）。"""
    return await _get_html(MAINPAGE_URL) or ""


@async_file_cache(market="aastocks-ipo", sector="upcoming", suffix="html", minutes=60)
async def fetch_upcoming() -> str:
    """即将上市页：含招股截止日与暗盘日期。"""
    return await _get_html(UPCOMING_URL) or ""
