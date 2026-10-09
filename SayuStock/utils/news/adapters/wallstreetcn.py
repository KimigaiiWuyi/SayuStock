"""华尔街见闻 7x24。score>=2 视为要闻。"""

from __future__ import annotations

import time

from ..http import fetch_json
from ..errors import NewsError, empty_error, parse_error, is_news_error, upstream_error
from ..models import NewsFeed, NewsItem
from ..parse_util import as_list, opt_int, opt_str, as_mapping

_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
_HEADERS = {"Referer": "https://wallstreetcn.com/live/global"}
_MAX_PAGES = 20


class WallstreetcnNews:
    name = "wallstreetcn"

    async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
        items: list[NewsItem] = []
        seen: set[str] = set()
        cursor = ""
        now_ms = int(time.time() * 1000)
        pages = _MAX_PAGES if cover_ms else 1
        page_size = 50 if cover_ms else max(1, min(limit, 50))
        for _ in range(pages):
            params = {
                "channel": "global-channel",
                "client": "pc",
                "limit": str(page_size),
            }
            if cursor:
                params["cursor"] = cursor
            payload = await fetch_json(_URL, params=params, headers=_HEADERS, provider=self.name)
            if is_news_error(payload):
                return payload if not items else _feed(items)
            parsed = _parse_page(payload)
            if is_news_error(parsed):
                return parsed if not items else _feed(items)
            page, next_cursor = parsed
            added = 0
            for item in page:
                if item.id in seen:
                    continue
                seen.add(item.id)
                items.append(item)
                added += 1
            if not added or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            oldest = min(item.published_ms for item in items)
            if cover_ms and now_ms - oldest >= cover_ms:
                break
        if not items:
            return empty_error("华尔街见闻快讯为空", provider=self.name)
        return _feed(items)


def _feed(items: list[NewsItem]) -> NewsFeed:
    return NewsFeed(source="wallstreetcn", items=tuple(items))


def _parse_page(payload: object) -> tuple[list[NewsItem], str] | NewsError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("见闻响应不是对象", provider="wallstreetcn")
    code = opt_int(root, "code")
    if code != 20000:
        return upstream_error(f"见闻 code={code}", provider="wallstreetcn")
    data = as_mapping(root["data"]) if "data" in root else None
    if data is None:
        return parse_error("见闻缺少 data", provider="wallstreetcn")
    rows = as_list(data["items"]) if "items" in data else None
    if rows is None:
        return parse_error("见闻缺少 items", provider="wallstreetcn")
    items: list[NewsItem] = []
    for raw in rows:
        row = as_mapping(raw)
        if row is None:
            continue
        item = _parse_row(row)
        if item is not None:
            items.append(item)
    next_cursor = opt_str(data, "next_cursor") or ""
    return items, next_cursor


def _parse_row(row: object) -> NewsItem | None:
    mapped = as_mapping(row)
    if mapped is None:
        return None
    news_id = opt_str(mapped, "id")
    published = opt_int(mapped, "display_time")
    text = opt_str(mapped, "content_text") or ""
    if news_id is None or published is None or not text:
        return None
    score = opt_int(mapped, "score")
    title = opt_str(mapped, "title") or ""
    return NewsItem(
        source="wallstreetcn",
        id=news_id,
        text=text,
        published_ms=published * 1000,
        important=score is not None and score >= 2,
        title=title,
    )
