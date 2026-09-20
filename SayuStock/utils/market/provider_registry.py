"""权益行情供应商注册表：后台「行情API」配置驱动、逐接口可切换。

- `market_api_default`：全局默认源（未单独指定接口时使用）。
- `market_api_<接口>`：逐接口覆盖；选「跟随默认」或未知值时回落全局默认。
- 所选源返回 `unsupported`（能力缺失）时自动回落默认源，仍不支持再回落
  东方财富，保证既有功能不因切源而失效。网络/解析错误原样上抛。
"""

from __future__ import annotations

import asyncio
from typing import Literal, Callable, cast
from datetime import date
from collections.abc import Sequence

from gsuid_core.logger import logger

from .port import MarketDataPort
from .enums import RankBy, BoardKind, ValueKind, KlinePeriod
from .errors import MarketError, is_market_error
from .models import (
    Quote,
    SymbolRef,
    BreadthBar,
    KlineSeries,
    ValueSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)

# 供应商 id → 后台选单展示名
PROVIDER_LABELS: dict[str, str] = {
    "eastmoney": "东方财富",
    "sina": "新浪财经",
    "tencent": "腾讯财经",
}
_LABEL_TO_ID: dict[str, str] = {label: pid for pid, label in PROVIDER_LABELS.items()}

FALLBACK_PROVIDER_ID = "eastmoney"
DEFAULT_CONFIG_KEY = "market_api_default"
FOLLOW_DEFAULT = "跟随默认"

# Port 接口名 → 后台配置键（quotes 跟随 quote，resolve/榜单内层走默认源）
INTERFACE_CONFIG_KEYS: dict[str, str] = {
    "quote": "market_api_quote",
    "intraday": "market_api_intraday",
    "kline": "market_api_kline",
    "board": "market_api_board",
    "rank_list": "market_api_rank_list",
    "hotmap": "market_api_hotmap",
    "sector_menu": "market_api_sector_menu",
    "breadth": "market_api_breadth",
    "market_turnover": "market_api_market_turnover",
    "northbound": "market_api_northbound",
    "valuation_series": "market_api_valuation_series",
    "financial_snapshot": "market_api_financial_snapshot",
}


def normalize_provider_id(value: object) -> str | None:
    """选单值（中文展示名或裸 id）→ 供应商 id；未知返回 None。"""
    text = str(value or "").strip()
    if not text or text == FOLLOW_DEFAULT:
        return None
    if text in PROVIDER_LABELS:
        return text
    return _LABEL_TO_ID.get(text)


def _build_eastmoney() -> MarketDataPort:
    from .adapters.eastmoney import EastMoneyMarketData

    return EastMoneyMarketData()


def _build_sina() -> MarketDataPort:
    from .adapters.sina import SinaMarketData

    return SinaMarketData()


def _build_tencent() -> MarketDataPort:
    from .adapters.tencent import TencentMarketData

    return TencentMarketData()


_PROVIDER_FACTORIES: dict[str, Callable[[], MarketDataPort]] = {
    "eastmoney": _build_eastmoney,
    "sina": _build_sina,
    "tencent": _build_tencent,
}


def default_config_reader(key: str, fallback: str) -> str:
    """读后台「行情API」配置；缺 gsuid_core 环境（单测）时回退默认值。"""
    try:
        from ...stock_config.stock_config import STOCK_CONFIG

        cfg = STOCK_CONFIG.get_config(key)
        data = getattr(cfg, "data", None)
        return data if isinstance(data, str) and data.strip() else fallback
    except Exception:  # noqa: BLE001 - 配置层不可用时保持默认装配
        return fallback


class ConfigurableEquityMarket:
    """equity 槽位包装：逐接口分派到所选供应商，unsupported 自动回落。

    供应商实例按 id 缓存；配置每次调用时读取，网页控制台改完即热生效。
    """

    def __init__(self, config_reader: Callable[[str, str], str] | None = None) -> None:
        self._reader = config_reader or default_config_reader
        self._instances: dict[str, MarketDataPort] = {}

    # -- 装配 -----------------------------------------------------------

    def _instance(self, provider_id: str) -> MarketDataPort | None:
        factory = _PROVIDER_FACTORIES.get(provider_id)
        if factory is None:
            return None
        if provider_id not in self._instances:
            self._instances[provider_id] = factory()
        return self._instances[provider_id]

    def _chain(self, iface: str) -> list[tuple[str, MarketDataPort]]:
        """调用链：逐接口选择 → 全局默认 → 东方财富（去重、保序）。"""
        picked: list[str] = []
        key = INTERFACE_CONFIG_KEYS.get(iface)
        if key is not None:
            override = normalize_provider_id(self._reader(key, FOLLOW_DEFAULT))
            if override is not None:
                picked.append(override)
        default_id = normalize_provider_id(self._reader(DEFAULT_CONFIG_KEY, PROVIDER_LABELS[FALLBACK_PROVIDER_ID]))
        if default_id is not None:
            picked.append(default_id)
        picked.append(FALLBACK_PROVIDER_ID)
        chain: list[tuple[str, MarketDataPort]] = []
        seen: set[str] = set()
        for pid in picked:
            if pid in seen:
                continue
            seen.add(pid)
            instance = self._instance(pid)
            if instance is not None:
                chain.append((pid, instance))
        return chain

    async def _dispatch(self, iface: str, method: str, *args: object, **kwargs: object) -> object:
        chain = self._chain(iface)
        if not chain:
            return MarketError(
                code="unsupported",
                message=f"行情API无可用供应商（接口 {iface}）",
                provider="registry",
            )
        result: object = None
        for i, (pid, port) in enumerate(chain):
            result = await getattr(port, method)(*args, **kwargs)
            if not (is_market_error(result) and result.code == "unsupported"):
                return result
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            if nxt is not None:
                logger.warning(
                    f"[SayuStock][行情API] {iface} 在 {PROVIDER_LABELS.get(pid, pid)} 不支持"
                    f"（{result.message}），回落 {PROVIDER_LABELS.get(nxt[0], nxt[0])}"
                )
        return result

    # -- MarketDataPort -------------------------------------------------

    async def resolve(self, query: str) -> SymbolRef | None:
        chain = self._chain("resolve")
        if not chain:
            return None
        return await chain[0][1].resolve(query)

    async def quote(self, query: str) -> Quote | MarketError:
        result = await self._dispatch("quote", "quote", query)
        return cast("Quote | MarketError", result)

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return list(await asyncio.gather(*[self.quote(q) for q in queries]))

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        result = await self._dispatch("intraday", "intraday", query, ndays=ndays)
        return cast("IntradaySeries | MarketError", result)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        result = await self._dispatch("kline", "kline", query, period, start=start, end=end)
        return cast("KlineSeries | MarketError", result)

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        result = await self._dispatch("board", "board", kind, sector=sector, limit=limit, sort_asc=sort_asc)
        return cast("BoardSnapshot | MarketError", result)

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError:
        result = await self._dispatch("rank_list", "rank_list", rank_by, limit=limit, high_first=high_first)
        return cast("RankSnapshot | MarketError", result)

    async def hotmap(self) -> BoardSnapshot | MarketError:
        result = await self._dispatch("hotmap", "hotmap")
        return cast("BoardSnapshot | MarketError", result)

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        result = await self._dispatch("sector_menu", "sector_menu", kind)
        return cast("dict | MarketError", result)

    async def breadth(self) -> BreadthBar | MarketError:
        result = await self._dispatch("breadth", "breadth")
        return cast("BreadthBar | MarketError", result)

    async def market_turnover(self) -> MarketTurnover | MarketError:
        result = await self._dispatch("market_turnover", "market_turnover")
        return cast("MarketTurnover | MarketError", result)

    async def northbound(self) -> NorthboundFlow | MarketError:
        result = await self._dispatch("northbound", "northbound")
        return cast("NorthboundFlow | MarketError", result)

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError:
        result = await self._dispatch("valuation_series", "valuation_series", query, kind)
        return cast("ValueSeries | MarketError", result)

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError:
        result = await self._dispatch("financial_snapshot", "financial_snapshot", code)
        return cast("FinancialSnapshot | MarketError", result)
