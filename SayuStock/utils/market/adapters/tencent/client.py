"""腾讯财经行情 HTTP：实时盘口 / K线 / 分时。"""

from __future__ import annotations

import re
from typing import Mapping

from aiohttp import ClientError, ClientSession, ClientTimeout

from gsuid_core.logger import logger

from ...errors import MarketError, parse_error, network_error
from ....stock.utils import async_file_cache

PROVIDER = "tencent"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Referer": "https://gu.qq.com/",
}
QUOTE_URL = "https://qt.gtimg.cn/q="
FQKLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
_QT_RE = re.compile(r'v_(?P<sym>[A-Za-z0-9]+)="(?P<line>[^"]*)"')


async def _get_text(url: str) -> str | MarketError:
    timeout = ClientTimeout(total=20)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(url) as res:
                if res.status != 200:
                    return network_error(f"腾讯 HTTP {res.status}", provider=PROVIDER)
                return await res.text(encoding="gb18030", errors="replace")
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][腾讯] 请求失败: {e}")
        return network_error(str(e), provider=PROVIDER)


async def _get_json(url: str, params: Mapping[str, str]) -> object | MarketError:
    timeout = ClientTimeout(total=20)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(url, params=dict(params)) as res:
                if res.status != 200:
                    return network_error(f"腾讯 HTTP {res.status}", provider=PROVIDER)
                try:
                    payload: object = await res.json(content_type=None)
                except (ValueError, TypeError) as e:
                    return parse_error(f"腾讯 JSON 无效: {e}", provider=PROVIDER)
                return payload
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][腾讯] 请求失败: {e}")
        return network_error(str(e), provider=PROVIDER)


async def fetch_qt_lines(symbols: list[str]) -> dict[str, str] | MarketError:
    """实时盘口原始 ~ 分隔行（GBK 解码后）；返回 {腾讯符号: 行}。"""
    if not symbols:
        return {}
    text = await _get_text(QUOTE_URL + ",".join(symbols))
    if isinstance(text, MarketError):
        return text
    out: dict[str, str] = {}
    for m in _QT_RE.finditer(text):
        out[m.group("sym")] = m.group("line")
    if not out:
        return parse_error("腾讯盘口响应无数据", provider=PROVIDER)
    return out


@async_file_cache(market="{symbol}", sector="tencent-kline-{unit}", suffix="json", sp="{datalen}", minutes=2)
async def fetch_fqkline(symbol: str, unit: str, datalen: int) -> object | str:
    """日/周/月 K（前复权）；unit ∈ day|week|month。"""
    params = {"param": f"{symbol},{unit},,,{datalen},qfq"}
    payload = await _get_json(FQKLINE_URL, params)
    if isinstance(payload, MarketError):
        return payload.message
    return payload


@async_file_cache(market="{symbol}", sector="tencent-mkline-{unit}", suffix="json", sp="{datalen}", minutes=2)
async def fetch_mkline(symbol: str, unit: str, datalen: int) -> object | str:
    """分钟 K；unit ∈ m1|m5|m15|m30|m60。m1 供五日分时拼接。"""
    params = {"param": f"{symbol},{unit},,,{datalen}"}
    payload = await _get_json(MKLINE_URL, params)
    if isinstance(payload, MarketError):
        return payload.message
    return payload


@async_file_cache(market="{symbol}", sector="tencent-minute", suffix="json", minutes=1)
async def fetch_minute(symbol: str) -> object | str:
    """当日分时；data.<sym>.data.data 为 "HHMM price 累计量(手) 累计额" 列表。"""
    payload = await _get_json(MINUTE_URL, {"code": symbol})
    if isinstance(payload, MarketError):
        return payload.message
    return payload
