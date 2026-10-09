"""新闻层统一错误。业务侧用 is_news_error 收窄。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from typing_extensions import TypeIs


@dataclass(frozen=True, slots=True)
class NewsError:
    code: str
    message: str
    provider: str


def is_news_error(value: object) -> TypeIs[NewsError]:
    return isinstance(value, NewsError)


def network_error(message: str, *, provider: str) -> NewsError:
    return NewsError(code="network", message=message, provider=provider)


def parse_error(message: str, *, provider: str) -> NewsError:
    return NewsError(code="parse", message=message, provider=provider)


def empty_error(message: str, *, provider: str) -> NewsError:
    return NewsError(code="empty", message=message, provider=provider)


def upstream_error(message: str, *, provider: str) -> NewsError:
    return NewsError(code="upstream", message=message, provider=provider)
