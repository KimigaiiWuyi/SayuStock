"""新浪财经 7x24 直播。该源没有要闻标记，important 恒为 False。"""

from __future__ import annotations

import time

from ..http import fetch_json
from ..errors import NewsError, empty_error, parse_error, is_news_error, upstream_error
from ..models import NewsFeed, NewsItem
from ..parse_util import as_list, opt_int, opt_str, as_mapping, parse_bjt_ms

_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
_HEADERS = {"Referer": "https://finance.sina.com.cn/7x24/"}
_MAX_PAGES = 20


class SinaNews:
    name = "sina"

    async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
        items: list[NewsItem] = []
        seen: set[str] = set()
        now_ms = int(time.time() * 1000)
        pages = _MAX_PAGES if cover_ms else 1
        page_size = 50 if cover_ms else max(1, min(limit, 50))
        for page_no in range(1, pages + 1):
            payload = await fetch_json(
                _URL,
                params={
                    "page": str(page_no),
                    "page_size": str(page_size),
                    "zhibo_id": "152",
                    "tag_id": "0",
                    "dire": "f",
                    "dpc": "1",
                },
                headers=_HEADERS,
                provider=self.name,
            )
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
            oldest = min(item.published_ms for item in items)
            if cover_ms and now_ms - oldest >= cover_ms:
                break
        if not items:
            return empty_error("新浪快讯为空", provider=self.name)
        return _feed(items)


def _feed(items: list[NewsItem]) -> NewsFeed:
    return NewsFeed(source="sina", items=tuple(items))


def _parse_page(payload: object) -> list[NewsItem] | NewsError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("新浪响应不是对象", provider="sina")
    result = as_mapping(root["result"]) if "result" in root else None
    if result is None:
        return parse_error("新浪缺少 result", provider="sina")
    status = as_mapping(result["status"]) if "status" in result else None
    if status is None or opt_int(status, "code") != 0:
        return upstream_error("新浪 status 非 0", provider="sina")
    data = as_mapping(result["data"]) if "data" in result else None
    feed = as_mapping(data["feed"]) if data is not None and "feed" in data else None
    rows = as_list(feed["list"]) if feed is not None and "list" in feed else None
    if rows is None:
        return parse_error("新浪缺少 feed.list", provider="sina")
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
    created = opt_str(mapped, "create_time")
    text = opt_str(mapped, "rich_text") or ""
    if news_id is None or created is None or not text:
        return None
    published_ms = parse_bjt_ms(created)
    if published_ms is None:
        return None
    return NewsItem(
        source="sina",
        id=news_id,
        text=text,
        published_ms=published_ms,
        important=False,
    )
