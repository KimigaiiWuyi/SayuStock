"""行情层统一错误类型。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import dataclass

# TypeIs：3.13+ 在 typing；3.12 需 typing_extensions。注解仅在类型检查期使用，
# 且本文件有 from __future__ import annotations，运行时不必真的 import TypeIs。
# 这样 Indicator math 轻量 CI（只装 pandas/numpy/pytest）也能 import 本模块。
if TYPE_CHECKING:
    from typing_extensions import TypeIs


@dataclass(frozen=True, slots=True)
class MarketError:
    """供应商无关错误；业务侧用 is_market_error 收窄。"""

    code: str
    message: str
    provider: str


def is_market_error(value: object) -> TypeIs[MarketError]:
    """双向收窄：True → MarketError；False → 从联合类型中排除 MarketError。

    须用 ``TypeIs`` 而非 ``TypeGuard``：后者在 if-return 之后**不会**排除错误分支，
    basedpyright 会仍把变量标成 ``T | MarketError``。
    """
    return isinstance(value, MarketError)


def not_found(message: str, *, provider: str) -> MarketError:
    return MarketError(code="not_found", message=message, provider=provider)


def network_error(message: str, *, provider: str) -> MarketError:
    return MarketError(code="network", message=message, provider=provider)


def parse_error(message: str, *, provider: str) -> MarketError:
    return MarketError(code="parse", message=message, provider=provider)


def empty_error(message: str, *, provider: str) -> MarketError:
    return MarketError(code="empty", message=message, provider=provider)


def unsupported(message: str, *, provider: str) -> MarketError:
    return MarketError(code="unsupported", message=message, provider=provider)
