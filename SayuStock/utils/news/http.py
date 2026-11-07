"""新闻源共用的 JSON GET。只在不可信响应上把解析失败收成 NewsError。"""

from __future__ import annotations

import json
from typing import Mapping

from aiohttp import ClientError, ClientSession, ClientTimeout, ContentTypeError

from .errors import NewsError, parse_error, network_error
from ..constant import DESKTOP_UA


async def fetch_json(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    provider: str,
) -> Mapping[str, object] | NewsError:
    merged = {"User-Agent": DESKTOP_UA, **headers}
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as client:
            async with client.get(url, params=params, headers=merged) as resp:
                if resp.status != 200:
                    return network_error(f"HTTP {resp.status}", provider=provider)
                try:
                    raw = await resp.json()
                except (ContentTypeError, json.JSONDecodeError):
                    return parse_error("响应不是 JSON", provider=provider)
    except (ClientError, TimeoutError, OSError) as exc:
        return network_error(str(exc), provider=provider)
    if not isinstance(raw, dict):
        return parse_error("JSON 根节点不是对象", provider=provider)
    return raw
