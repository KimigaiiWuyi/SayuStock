"""按配置顺序尝试新闻源，前一个失败或为空才用下一个。"""

from __future__ import annotations

import time
import asyncio
from collections.abc import Sequence

from gsuid_core.logger import logger

from .port import NewsPort, NewsSource
from .errors import NewsError, empty_error, is_news_error
from .models import NewsFeed

_BACKOFF_S = 90.0


class FallbackNews:
    def __init__(self, sources: Sequence[NewsSource], order: Sequence[str] | None = None) -> None:
        self._sources = {source.name: source for source in sources}
        self._default = tuple(source.name for source in sources)
        self._order_override = None if order is None else tuple(order)
        self._backoff_until: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def latest(self, *, limit: int = 50, cover_ms: int = 0) -> NewsFeed | NewsError:
        async with self._lock:
            return await self._latest(limit=limit, cover_ms=cover_ms)

    async def _latest(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
        order = self._order_override if self._order_override is not None else _configured_order(self._default)
        last_error = empty_error("没有可用的新闻源", provider="news")
        skipped: list[str] = []
        now = time.monotonic()
        for name in order:
            if name not in self._sources:
                continue
            source = self._sources[name]
            until = self._backoff_until[name] if name in self._backoff_until else 0.0
            if now < until:
                skipped.append(name)
                last_error = empty_error(f"{name} 退避中", provider=name)
                continue
            result = await source.fetch(limit=limit, cover_ms=cover_ms)
            if is_news_error(result):
                self._backoff_until[name] = now + _BACKOFF_S
                skipped.append(name)
                last_error = result
                logger.warning(f"[SayuStock] 新闻源 {name} 失败({result.code}): {result.message}")
                continue
            if not result.items:
                skipped.append(name)
                last_error = empty_error(f"{name} 返回空列表", provider=name)
                continue
            if skipped:
                logger.info(f"[SayuStock] 新闻源使用 {name}（{'、'.join(skipped)} 不可用）")
            return result
        return last_error


def resolve_order(default: tuple[str, ...], raw: object) -> tuple[str, ...]:
    """配置顺序在前；没写到的已知源排在后面，省略不等于禁用。"""
    if not isinstance(raw, list):
        return default
    known = set(default)
    picked: list[str] = []
    seen: set[str] = set()
    for name in raw:
        if isinstance(name, str) and name in known and name not in seen:
            seen.add(name)
            picked.append(name)
    if not picked:
        return default
    rest = tuple(name for name in default if name not in seen)
    return tuple(picked) + rest


def _configured_order(default: tuple[str, ...]) -> tuple[str, ...]:
    from ...stock_config.stock_config import STOCK_CONFIG

    raw = STOCK_CONFIG.get_config("news_source_order").data
    return resolve_order(default, raw)


def build_default_news() -> NewsPort:
    from .adapters import SinaNews, Jin10News, EastmoneyNews, WallstreetcnNews

    return FallbackNews((EastmoneyNews(), WallstreetcnNews(), SinaNews(), Jin10News()))
