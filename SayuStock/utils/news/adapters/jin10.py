"""金十快讯。x-app-id 是网页端公开客户端号，不是用户密钥。important==1 为要闻。"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..http import fetch_json
from ..errors import NewsError, empty_error, parse_error, is_news_error, upstream_error
from ..models import NewsFeed, NewsItem
from ..parse_util import as_list, opt_int, opt_str, as_mapping, parse_bjt_ms

_URL = "https://flash-api.jin10.com/get_flash_list"
_HEADERS = {
    "Referer": "https://www.jin10.com/",
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
}
_MAX_PAGES = 20


class Jin10News:
    name = "jin10"

    async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
        items: list[NewsItem] = []
        seen: set[str] = set()
        max_time = ""
        now_ms = int(time.time() * 1000)
        pages = _MAX_PAGES if cover_ms else 1
        for _ in range(pages):
            params = {"channel": "-8200", "vip": "1"}
            if max_time:
                params["max_time"] = max_time
            payload = await fetch_json(_URL, params=params, headers=_HEADERS, provider=self.name)
            if is_news_error(payload):
                return payload if not items else _feed(items)
            parsed = _parse_page(payload)
            if is_news_error(parsed):
                return parsed if not items else _feed(items)
            added = 0
            for item in parsed:
                if item.id in seen:
                    continue
                seen.add(item.id)
                items.append(item)
                added += 1
            if not added:
                break
            oldest_time = _clock(min(item.published_ms for item in parsed))
            if not oldest_time or oldest_time == max_time:
                break
            max_time = oldest_time
            oldest = min(item.published_ms for item in items)
            if cover_ms and now_ms - oldest >= cover_ms:
                break
        if not items:
            return empty_error("金十快讯为空", provider=self.name)
        return _feed(items)


def _feed(items: list[NewsItem]) -> NewsFeed:
    return NewsFeed(source="jin10", items=tuple(items))


def _clock(published_ms: int) -> str:
    return datetime.fromtimestamp(published_ms / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _parse_page(payload: object) -> list[NewsItem] | NewsError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("金十响应不是对象", provider="jin10")
    status = opt_int(root, "status")
    if status != 200:
        return upstream_error(f"金十 status={status}", provider="jin10")
    rows = as_list(root["data"]) if "data" in root else None
    if rows is None:
        return parse_error("金十缺少 data", provider="jin10")
    items: list[NewsItem] = []
    for raw in rows:
        row = as_mapping(raw)
        if row is None:
            continue
        item = _parse_row(row)
        if item is not None:
            items.append(item)
    return items


def _parse_row(row: object) -> NewsItem | None:
    mapped = as_mapping(row)
    if mapped is None:
        return None
    news_id = opt_str(mapped, "id")
    clock = opt_str(mapped, "time")
    body = as_mapping(mapped["data"]) if "data" in mapped else None
    text = opt_str(body, "content") if body is not None else None
    if news_id is None or clock is None or not text:
        return None
    published_ms = parse_bjt_ms(clock)
    if published_ms is None:
        return None
    flag = opt_int(mapped, "important")
    title = opt_str(body, "title") if body is not None else None
    return NewsItem(
        source="jin10",
        id=news_id,
        text=text,
        published_ms=published_ms,
        important=flag == 1,
        title=title or "",
    )
