"""东方财富 7x24。免登录，titleColor>0 视为要闻。"""

from __future__ import annotations

import time

from ..http import fetch_json
from ..errors import NewsError, empty_error, parse_error, is_news_error, upstream_error
from ..models import NewsFeed, NewsItem
from ..parse_util import as_list, opt_int, opt_str, as_mapping, parse_bjt_ms

_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
_HEADERS = {"Referer": "https://kuaixun.eastmoney.com/"}
_MAX_PAGES = 20


class EastmoneyNews:
    name = "eastmoney"

    async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
        items: list[NewsItem] = []
        seen: set[str] = set()
        cursor = ""
        now_ms = int(time.time() * 1000)
        pages = _MAX_PAGES if cover_ms else 1
        page_size = 50 if cover_ms else max(1, min(limit, 50))
        for _ in range(pages):
            payload = await fetch_json(
                _URL,
                params={
                    "client": "web",
                    "biz": "web_724",
                    "fastColumn": "102",
                    "sortEnd": cursor,
                    "pageSize": str(page_size),
                    "req_trace": str(now_ms),
                },
                headers=_HEADERS,
                provider=self.name,
            )
            if is_news_error(payload):
                return payload if not items else _feed(items)
            parsed = _parse_page(payload)
            if is_news_error(parsed):
                return parsed if not items else _feed(items)
            page, next_cursor = parsed
            added = _extend(items, seen, page)
            if not added or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            oldest = min(item.published_ms for item in items)
            if cover_ms and now_ms - oldest >= cover_ms:
                break
        if not items:
            return empty_error("东财快讯为空", provider=self.name)
        return _feed(items)


def _feed(items: list[NewsItem]) -> NewsFeed:
    return NewsFeed(source="eastmoney", items=tuple(items))


def _extend(items: list[NewsItem], seen: set[str], page: list[NewsItem]) -> int:
    added = 0
    for item in page:
        if item.id in seen:
            continue
        seen.add(item.id)
        items.append(item)
        added += 1
    return added


def _parse_page(payload: object) -> tuple[list[NewsItem], str] | NewsError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("东财响应不是对象", provider="eastmoney")
    code = opt_str(root, "code")
    if code != "1":
        return upstream_error(f"东财 code={code}", provider="eastmoney")
    data = as_mapping(root["data"]) if "data" in root else None
    if data is None:
        return parse_error("东财缺少 data", provider="eastmoney")
    rows = as_list(data["fastNewsList"]) if "fastNewsList" in data else None
    if rows is None:
        return parse_error("东财缺少 fastNewsList", provider="eastmoney")
    items: list[NewsItem] = []
    for raw in rows:
        row = as_mapping(raw)
        if row is None:
            continue
        item = _parse_row(row)
        if item is not None:
            items.append(item)
    next_cursor = opt_str(data, "sortEnd") or ""
    return items, next_cursor


def _parse_row(mapped: object) -> NewsItem | None:
    row = as_mapping(mapped)
    if row is None:
        return None
    news_id = opt_str(row, "code")
    show_time = opt_str(row, "showTime")
    if news_id is None or show_time is None:
        return None
    published_ms = parse_bjt_ms(show_time)
    if published_ms is None:
        return None
    summary = opt_str(row, "summary") or ""
    title = opt_str(row, "title") or ""
    text = summary or title
    if not text:
        return None
    color = opt_int(row, "titleColor")
    return NewsItem(
        source="eastmoney",
        id=news_id,
        text=text,
        published_ms=published_ms,
        important=color is not None and color > 0,
        title=title,
    )
