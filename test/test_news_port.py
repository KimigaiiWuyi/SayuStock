"""快讯统一口：解析、换源、水位线。全部离线，不打外网，不读 STOCK_CONFIG。"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

from SayuStock.utils.news import (
    NewsFeed,
    NewsItem,
    NewsError,
    id_newer,
    is_news_error,
    should_rebase,
    split_watermark,
)
from SayuStock.utils.news.errors import network_error
from SayuStock.utils.news.adapters import sina, jin10, eastmoney, wallstreetcn
from SayuStock.utils.news.fallback import FallbackNews, resolve_order

_DEFAULT = ("eastmoney", "wallstreetcn", "sina", "jin10")


def _feed(source: str, news_id: str = "1") -> NewsFeed:
    return NewsFeed(
        source=source,
        items=(
            NewsItem(
                source=source,
                id=news_id,
                text="正文",
                published_ms=1_700_000_000_000,
                important=True,
            ),
        ),
    )


class _Scripted:
    def __init__(self, name: str, result: NewsFeed | NewsError) -> None:
        self.name = name
        self._result = result
        self.calls = 0

    async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
        self.calls += 1
        return self._result


def test_split_and_rebase_legacy_xueqiu_id() -> None:
    assert split_watermark("123456") == ("", "123456")
    assert split_watermark("eastmoney:99") == ("eastmoney", "99")
    assert split_watermark("wallstreetcn:a:b") == ("wallstreetcn", "a:b")
    assert should_rebase("123456", "eastmoney") is True
    assert should_rebase(None, "eastmoney") is True
    assert should_rebase("eastmoney:1", "eastmoney") is False
    assert should_rebase("sina:1", "eastmoney") is True


def test_id_newer_compares_digits_numerically() -> None:
    assert id_newer("10", "9") is True
    assert id_newer("9", "10") is False


def test_advance_mark_does_not_move_backward() -> None:
    from SayuStock.stock_news import _advance_mark

    assert _advance_mark("eastmoney:10", "eastmoney:3") == "eastmoney:10"
    assert _advance_mark("eastmoney:3", "eastmoney:10") == "eastmoney:10"


def test_resolve_order_keeps_unlisted_sources_as_tail() -> None:
    assert resolve_order(_DEFAULT, ["sina"]) == ("sina", "eastmoney", "wallstreetcn", "jin10")
    assert resolve_order(_DEFAULT, ["wallstreetcn", "eastmoney", "wallstreetcn"]) == (
        "wallstreetcn",
        "eastmoney",
        "sina",
        "jin10",
    )
    assert resolve_order(_DEFAULT, ["nope"]) == _DEFAULT
    assert resolve_order(_DEFAULT, "eastmoney") == _DEFAULT


def test_eastmoney_title_color_and_int_code() -> None:
    parsed = eastmoney._parse_page(
        {
            "code": 1,
            "data": {
                "sortEnd": 1791526794079682,
                "fastNewsList": [
                    {
                        "code": 202610090001,
                        "showTime": "2026-10-09 14:12:53",
                        "summary": "要闻",
                        "title": "标题",
                        "titleColor": 3,
                    },
                    {
                        "code": "202610090002",
                        "showTime": "2026-10-09 14:10:00",
                        "summary": "",
                        "title": "只有标题",
                        "titleColor": 0,
                    },
                    {"code": "bad"},
                ],
            },
        }
    )
    assert not is_news_error(parsed)
    assert isinstance(parsed, tuple)
    items, cursor = parsed
    assert cursor == "1791526794079682"
    assert [item.id for item in items] == ["202610090001", "202610090002"]
    assert items[0].important is True
    assert items[1].important is False
    assert items[1].text == "只有标题"


def test_eastmoney_upstream_code() -> None:
    parsed = eastmoney._parse_page({"code": "0", "data": {}})
    assert is_news_error(parsed)
    assert parsed.code == "upstream"


def test_wallstreetcn_score() -> None:
    parsed = wallstreetcn._parse_page(
        {
            "code": 20000,
            "data": {
                "next_cursor": 1791514860,
                "items": [
                    {"id": 11, "display_time": 1_700_000_000, "content_text": "普通", "score": 1},
                    {"id": "12", "display_time": 1_700_000_060, "content_text": "要闻", "score": 2, "title": "t"},
                ],
            },
        }
    )
    assert isinstance(parsed, tuple)
    items, cursor = parsed
    assert cursor == "1791514860"
    assert items[0].important is False
    assert items[1].important is True
    assert items[0].published_ms == 1_700_000_000_000


def test_sina_has_no_importance_flag() -> None:
    parsed = sina._parse_page(
        {
            "result": {
                "status": {"code": 0},
                "data": {
                    "feed": {"list": [{"id": 5134333, "rich_text": "快讯", "create_time": "2026-10-09 14:13:23"}]}
                },
            }
        }
    )
    assert isinstance(parsed, list)
    assert parsed[0].important is False
    assert parsed[0].id == "5134333"


def test_jin10_important_is_top_level() -> None:
    parsed = jin10._parse_page(
        {
            "status": 200,
            "data": [
                {
                    "id": "20261009142026440800",
                    "time": "2026-10-09 14:20:26",
                    "important": 1,
                    "data": {"content": "要闻正文", "title": "标题", "important": 0},
                },
                {
                    "id": "2",
                    "time": "2026-10-09 14:00:00",
                    "important": 0,
                    "data": {"content": "普通"},
                },
            ],
        }
    )
    assert isinstance(parsed, list)
    assert parsed[0].important is True
    assert parsed[0].title == "标题"
    assert parsed[1].important is False


def test_fallback_skips_error_then_uses_next() -> None:
    bad = _Scripted("eastmoney", network_error("down", provider="eastmoney"))
    good = _Scripted("wallstreetcn", _feed("wallstreetcn"))
    port = FallbackNews((bad, good), order=("eastmoney", "wallstreetcn"))

    first = asyncio.run(port.latest())
    second = asyncio.run(port.latest())

    assert isinstance(first, NewsFeed)
    assert first.source == "wallstreetcn"
    assert isinstance(second, NewsFeed)
    assert bad.calls == 1
    assert good.calls == 2


def test_fallback_empty_does_not_backoff() -> None:
    empty = _Scripted("eastmoney", NewsFeed(source="eastmoney", items=()))
    good = _Scripted("sina", _feed("sina"))
    port = FallbackNews((empty, good), order=("eastmoney", "sina"))

    asyncio.run(port.latest())
    asyncio.run(port.latest())
    assert empty.calls == 2
    assert good.calls == 2


def test_fallback_all_fail_returns_last_error() -> None:
    bad = _Scripted("jin10", network_error("down", provider="jin10"))
    port = FallbackNews((bad,), order=("jin10",))
    result = asyncio.run(port.latest())
    assert is_news_error(result)
    assert result.provider == "jin10"


def test_eastmoney_keeps_first_page_when_next_fails(monkeypatch: MonkeyPatch) -> None:
    show = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    pages = {"n": 0}

    async def _fake(
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        provider: str,
    ) -> dict[str, object] | NewsError:
        pages["n"] += 1
        if pages["n"] == 1:
            return {
                "code": "1",
                "data": {
                    "sortEnd": "10",
                    "fastNewsList": [
                        {
                            "code": "11",
                            "showTime": show,
                            "summary": "第一条",
                            "title": "",
                            "titleColor": 1,
                        }
                    ],
                },
            }
        return network_error("HTTP 500", provider=provider)

    monkeypatch.setattr(eastmoney, "fetch_json", _fake)
    result = asyncio.run(eastmoney.EastmoneyNews().fetch(limit=10, cover_ms=48 * 60 * 60 * 1000))
    assert isinstance(result, NewsFeed)
    assert [item.id for item in result.items] == ["11"]
    assert pages["n"] == 2
