"""NewsPort：对内推送 / 对外 AI 工具共用的快讯契约。"""

from __future__ import annotations

from typing import Protocol

from .errors import NewsError
from .models import NewsFeed


class NewsSource(Protocol):
    name: str

    async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError: ...


class NewsPort(Protocol):
    async def latest(self, *, limit: int = 50, cover_ms: int = 0) -> NewsFeed | NewsError: ...
