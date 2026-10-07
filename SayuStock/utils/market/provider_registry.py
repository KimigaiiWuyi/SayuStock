"""权益行情供应商注册表：后台「行情API」四域源链配置驱动。

- 六个源链配置（`GsListStrConfig` 列表）：全局 `market_api_chain` + 五域
  （`_GROUP_CHAIN_CONFIG_KEYS`：盘口/分时 quote、K线 kline、板块/排行/菜单
  board、大盘统计/资金 market、东财独占 exclusive）。域链只作用于该域接口，
  留空回落全局链；全局链留空用内置默认链。每个域键的 `options` 只列该域
  真正实现了接口的源，点开控制台就能看出「有没有第二个源」。
- 源链只表达优先级，不是禁用表达：链内源按顺序先行，链外源按系统内禀
  次序（`_SYSTEM_ORDER`：东财→腾讯→新浪→同花顺）自动排到链尾兜底。
  因此任何配置下四个源都在链上 —— 东财独占接口（云图/北向/估值/财报/
  五日分时/概念板块）永远不会被配置饿死。
- 取数语义（尽可能交付）：按链逐一尝试，成功即返回；源返回 `unsupported`
  （不支持该接口）直接跳过；其余错误（网络/解析/空数据）顺延到下一个源；
  `not_found` 短路返回（标的解析层共用，换源无意义）。全部失败才报错，
  报「优先级最高且真正出错」的那个源的错误。
- 时间预算：每个源最多占 `SOURCE_TIMEOUT_S`；调用方可用 `chain_deadline()`
  再声明整链总预算，链内按「剩余预算 / 剩余源数」分片。没有分片时，
  一个挂起的源会把预算吃光，后面的源一个都轮不到。
"""

from __future__ import annotations

import time
import asyncio
from typing import Literal, Callable, Iterator, cast
from datetime import date
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Sequence

try:
    from gsuid_core.logger import logger
except ImportError:  # 最小依赖 CI（Indicator math job）只装 pandas/numpy/pytest
    import logging

    logger = logging.getLogger("SayuStock")

from .port import MarketDataPort
from .enums import RankBy, BoardKind, ValueKind, KlinePeriod
from .errors import MarketError, network_error, is_market_error
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
    "ths": "同花顺",
}

# 选单值/手写值 → 供应商 id（含常用简称）
_PROVIDER_ALIASES: dict[str, str] = {
    "东方财富": "eastmoney",
    "东财": "eastmoney",
    "eastmoney": "eastmoney",
    "新浪财经": "sina",
    "新浪": "sina",
    "sina": "sina",
    "腾讯财经": "tencent",
    "腾讯": "tencent",
    "tencent": "tencent",
    "同花顺": "ths",
    "ths": "ths",
}

CHAIN_CONFIG_KEY = "market_api_chain"
# 全局链留空时的内置默认链（同花顺由链尾兜底自动补入，不在此列）
DEFAULT_PRIORITY: tuple[str, ...] = ("eastmoney", "tencent", "sina")

# 域 → 链串配置键（域链留空回落全局链）
# exclusive 是「东财独占」组：组内四个接口别的源都没实现（见 doc 覆盖矩阵），
# 单列成键是为了让后台点开就能看见「只有东方财富一个可选」。
_GROUP_CHAIN_CONFIG_KEYS: dict[str, str] = {
    "quote": "market_api_chain_quote",
    "kline": "market_api_chain_kline",
    "board": "market_api_chain_board",
    "market": "market_api_chain_market",
    "exclusive": "market_api_chain_exclusive",
}

# 接口 → 功能域；未列出的接口（resolve 等）走全局链。
# 分域依据是「该接口还有没有第二个源」：有第二源的才值得让用户调优先级，
# 只有一个源的接口并进 exclusive，避免用户在必然失败的选择上浪费时间。
_IFACE_GROUPS: dict[str, str] = {
    "quote": "quote",
    "quotes": "quote",
    "intraday": "quote",
    "kline": "kline",
    "board": "board",
    "rank_list": "board",
    "sector_menu": "board",
    "breadth": "market",
    "market_turnover": "market",
    "hotmap": "exclusive",
    "northbound": "exclusive",
    "valuation_series": "exclusive",
    "financial_snapshot": "exclusive",
}

# 系统内禀次序：链外源兜底追加用
_SYSTEM_ORDER: tuple[str, ...] = ("eastmoney", "tencent", "sina", "ths")

# 单个源在链上最多占用多少秒。东财 stock_request 是全局 ClientTimeout(total=20)，
# 但一个"连上了却不回包"的源仍然能把整条链拖住，所以每源再封一层顶，
# 超时即顺延到下一个源。取值要**大于**各源自身 20s 的 HTTP 超时（否则会先取消
# 掉本该由源自己报错返回的请求），也要远大于实测健康调用的最慢值
# （quote 0.85s / kline 0.48s / board 0.99s / hotmap 0.47s / breadth 7.29s）。
SOURCE_TIMEOUT_S: float = 25.0

# 取数总预算。调用方（如模拟盘取价）用 chain_deadline() 声明后，链上每个源按
# 「剩余预算 / 剩余源数」分到时间片，保证慢源挂起时后面的源仍能轮到；
# 不声明预算的调用方（大盘概览、云图等）走 SOURCE_TIMEOUT_S 每源顶格。
_deadline: ContextVar[float | None] = ContextVar("sayustock_market_chain_deadline", default=None)


@contextmanager
def chain_deadline(seconds: float) -> Iterator[None]:
    """在 with 块内声明「这条取数链总共只有 seconds 秒」。

    没有它时，外层 ``asyncio.wait_for`` 取消的是整个 ``quote()``：
    第一个源挂起就把预算吃光，后面的源一个都轮不到。
    """
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def _source_budget(sources_left: int) -> float:
    """当前源可用的秒数；0 表示预算已耗尽，链应就地停止。"""
    deadline = _deadline.get()
    if deadline is None:
        return SOURCE_TIMEOUT_S
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(SOURCE_TIMEOUT_S, remaining / sources_left)


def normalize_provider_id(value: object) -> str | None:
    """选单值（中文展示名/简称/裸 id）→ 供应商 id；未知返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    return _PROVIDER_ALIASES.get(text)


def parse_priority_chain(raw: object) -> list[str]:
    """源链配置（字符串列表）→ 供应商 id 列表（保序去重，未知项忽略并告警）。"""
    if not isinstance(raw, (list, tuple)):
        return []
    ids: list[str] = []
    for part in raw:
        text = str(part or "").strip()
        if not text:
            continue
        pid = normalize_provider_id(text)
        if pid is None:
            logger.warning(f"[SayuStock][行情API] 源链中的「{text}」不是可用数据源，已忽略")
            continue
        if pid not in ids:
            ids.append(pid)
    return ids


def _build_eastmoney() -> MarketDataPort:
    from .adapters.eastmoney import EastMoneyMarketData

    return EastMoneyMarketData()


def _build_sina() -> MarketDataPort:
    from .adapters.sina import SinaMarketData

    return SinaMarketData()


def _build_tencent() -> MarketDataPort:
    from .adapters.tencent import TencentMarketData

    return TencentMarketData()


def _build_ths() -> MarketDataPort:
    from .adapters.ths import THSMarketData

    return THSMarketData()


_PROVIDER_FACTORIES: dict[str, Callable[[], MarketDataPort]] = {
    "eastmoney": _build_eastmoney,
    "sina": _build_sina,
    "tencent": _build_tencent,
    "ths": _build_ths,
}


def default_config_reader(key: str, fallback: object) -> object:
    """读后台「行情API」源链配置（字符串列表）；缺环境/空值回退 fallback。"""
    try:
        from ...stock_config.stock_config import STOCK_CONFIG

        cfg = STOCK_CONFIG.get_config(key)
        data = getattr(cfg, "data", None)
        if isinstance(data, list) and data:
            return data
        return fallback
    except Exception:  # noqa: BLE001 - 配置层不可用时保持默认装配
        return fallback


def build_priority_chain(reader: Callable[[str, object], object], group: str | None = None) -> list[str]:
    """配置 → 实际调用链：域链 → 全局链 → 内置默认链，链外源排链尾兜底。

    链只表达优先级：链内源按顺序先行；链外源按系统内禀次序
    （东财→腾讯→新浪→同花顺）自动追加到链尾。因此任何配置（空列表/单源/
    非法值）下四个源都在链上，东财独占接口永不失兜底。
    """
    raw: object = reader(_GROUP_CHAIN_CONFIG_KEYS[group], None) if group is not None else None
    chain = parse_priority_chain(raw)
    if not chain:
        chain = parse_priority_chain(reader(CHAIN_CONFIG_KEY, None))
    if not chain:
        chain = list(DEFAULT_PRIORITY)
    for pid in _SYSTEM_ORDER:
        if pid not in chain:
            chain.append(pid)
    return chain


def _stamp_provider(result: object, pid: str) -> object:
    """把命中源 id 写进结果模型，供渲染层展示真实数据来源（左下角标签）。"""
    from .display import stamp_provider

    return stamp_provider(result, pid)


class ConfigurableEquityMarket:
    """equity 槽位包装：按域链/全局链逐一尝试，尽可能交付。

    供应商实例按 id 缓存；配置每次调用时读取，网页控制台改完即热生效。
    """

    def __init__(self, config_reader: Callable[[str, object], object] | None = None) -> None:
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

    def _chain(self, group: str | None = None) -> list[tuple[str, MarketDataPort]]:
        chain: list[tuple[str, MarketDataPort]] = []
        for pid in build_priority_chain(self._reader, group):
            instance = self._instance(pid)
            if instance is not None:
                chain.append((pid, instance))
        return chain

    async def _dispatch(self, iface: str, method: str, *args: object, **kwargs: object) -> object:
        chain = self._chain(_IFACE_GROUPS.get(iface))
        if not chain:
            return MarketError(
                code="unsupported",
                message=f"行情API无可用供应商（接口 {iface}）",
                provider="registry",
            )
        first_real_error: MarketError | None = None
        first_error: MarketError | None = None
        for i, (pid, port) in enumerate(chain):
            budget = _source_budget(len(chain) - i)
            if budget <= 0:
                logger.warning(f"[SayuStock][行情API] {iface} 取数预算已耗尽，停止顺延")
                break
            try:
                result = await asyncio.wait_for(getattr(port, method)(*args, **kwargs), timeout=budget)
            except asyncio.TimeoutError:
                # 超时按 network 处理顺延：慢≠没有，后面的源还有机会
                result = network_error(f"单源超时（>{budget:.1f}s）", provider=pid)
                logger.warning(f"[SayuStock][行情API] {iface} 在 {PROVIDER_LABELS.get(pid, pid)} 超时，按失败顺延")
            except Exception as exc:  # noqa: BLE001 - 源内部异常转 network 错误顺延，链路尽可能交付
                result = network_error(f"{type(exc).__name__}: {exc}", provider=pid)
                logger.warning(
                    f"[SayuStock][行情API] {iface} 在 {PROVIDER_LABELS.get(pid, pid)} 抛出异常"
                    f"（{result.message}），按失败顺延"
                )
            if not is_market_error(result):
                return _stamp_provider(result, pid)
            assert isinstance(result, MarketError)
            if first_error is None:
                first_error = result
            if result.code == "not_found":
                # 标的解析层各源共用，换源无意义
                return result
            if result.code == "unsupported":
                # 该源没有此接口，属预期，静默跳过
                logger.debug(f"[SayuStock][行情API] {iface} 在 {PROVIDER_LABELS.get(pid, pid)} 不支持，跳过")
                continue
            if first_real_error is None:
                first_real_error = result
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            logger.warning(
                f"[SayuStock][行情API] {iface} 由 {PROVIDER_LABELS.get(pid, pid)} 失败"
                f"（{result.code}: {result.message}），顺延 "
                f"{PROVIDER_LABELS.get(nxt[0], nxt[0]) if nxt else '无下一源'}"
            )
        if first_real_error is not None:
            return first_real_error
        if first_error is not None:
            return first_error
        # 预算在动手之前就耗尽：必须回一个 MarketError，不能让 None 漏给调用方
        return MarketError(
            code="network",
            message=f"{iface} 未取得数据（取数预算已耗尽）",
            provider="registry",
        )

    # -- MarketDataPort -------------------------------------------------

    async def resolve(self, query: str) -> SymbolRef | None:
        chain = self._chain()
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
