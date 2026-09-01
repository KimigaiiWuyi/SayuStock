"""天天基金搜索与历史净值 HTTP。"""

from __future__ import annotations

from typing import Mapping

from aiohttp import ClientError, ClientSession, ClientTimeout

from gsuid_core.logger import logger

from ...errors import MarketError, empty_error, parse_error, network_error
from ....stock.utils import async_file_cache

PROVIDER = "tiantian"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json,text/javascript,*/*;q=0.8",
    "Referer": "https://fund.eastmoney.com/",
}
SEARCH_URL = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
NAV_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList"
_DEVICE_ID = "1234567.py.service"
_PAGE_SIZE = 400
_MAX_PAGES = 8


async def _get_json(url: str, params: Mapping[str, str]) -> object | MarketError:
    timeout = ClientTimeout(total=20)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(url, params=dict(params)) as res:
                if res.status != 200:
                    return network_error(f"天天基金 HTTP {res.status}", provider=PROVIDER)
                try:
                    payload: object = await res.json(content_type=None)
                except (ValueError, TypeError) as e:
                    return parse_error(f"天天基金 JSON 无效: {e}", provider=PROVIDER)
                return payload
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][天天基金] 请求失败: {e}")
        return network_error(str(e), provider=PROVIDER)


@async_file_cache(market="{query}", sector="tiantian-search", suffix="json", minutes=1440)
async def fetch_fund_search(query: str) -> dict[str, object] | str:
    params = {
        "callback": "",
        "m": "1",
        "key": query.strip(),
    }
    payload = await _get_json(SEARCH_URL, params)
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, dict):
        return "天天基金搜索响应非对象"
    return payload


@async_file_cache(
    market="{fund_code}",
    sector="tiantian-nav-{page_index}-{page_size}",
    suffix="json",
    minutes=1440,
)
async def fetch_nav_page(fund_code: str, page_index: int, page_size: int) -> dict[str, object] | str:
    params = {
        "FCODE": fund_code,
        "pageIndex": str(page_index),
        "pageSize": str(page_size),
        "deviceid": _DEVICE_ID,
        "version": "6.2.4",
        "plat": "Android",
        "product": "EFund",
    }
    payload = await _get_json(NAV_URL, params)
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, dict):
        return "天天基金净值响应非对象"
    return payload


async def fetch_nav_pages(fund_code: str, *, min_rows: int) -> list[object] | MarketError:
    """按页拉取净值，最新在前；凑够 min_rows 或没有下一页为止。"""
    rows: list[object] = []
    total: int | None = None
    pages = max(1, min(_MAX_PAGES, (max(min_rows, 1) + _PAGE_SIZE - 1) // _PAGE_SIZE))
    for page_index in range(1, pages + 1):
        page = await fetch_nav_page(fund_code, page_index, _PAGE_SIZE)
        if isinstance(page, str):
            if rows:
                return rows
            return network_error(page, provider=PROVIDER)
        if "Datas" not in page:
            if rows:
                return rows
            return empty_error("天天基金净值缺少 Datas", provider=PROVIDER)
        chunk = page["Datas"]
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if "TotalCount" in page and isinstance(page["TotalCount"], int):
            total = page["TotalCount"]
        if total is not None and len(rows) >= total:
            break
        if len(rows) >= min_rows:
            break
        if len(chunk) < _PAGE_SIZE:
            break
    if not rows:
        return empty_error("天天基金净值列表为空", provider=PROVIDER)
    return rows


async def fetch_latest_nav_rows(fund_code: str, *, limit: int = 2) -> list[object] | MarketError:
    size = max(2, limit)
    page = await fetch_nav_page(fund_code, 1, size)
    if isinstance(page, str):
        return network_error(page, provider=PROVIDER)
    if "Datas" not in page:
        return empty_error("天天基金净值缺少 Datas", provider=PROVIDER)
    chunk = page["Datas"]
    if not isinstance(chunk, list) or not chunk:
        return empty_error("天天基金净值列表为空", provider=PROVIDER)
    return list(chunk[:size])
