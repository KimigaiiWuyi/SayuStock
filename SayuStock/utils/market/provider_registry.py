"""权益行情供应商注册表：后台「行情API」每源优先级数字驱动。

- 每源一个 `market_api_priority_<id>` 整数配置（0-100，数字越大越先尝试；
  0=禁用该源）。数字相同时按各源系统内禀序号（`_PROVIDER_RANKS`，
  东财9/腾讯8/新浪7/同花顺6，大者先）裁决。
- 取数语义（尽可能交付）：按链逐一尝试，成功即返回；源返回 `unsupported`
  （不支持该接口）直接跳过；其余错误（网络/解析/空数据）顺延到下一个源；
  `not_found` 短路返回（标的解析层共用，换源无意义）。全部失败才报错，
  报「优先级最高且真正出错」的那个源的错误。
- 旧版 `market_api_priority` 链串配置在装配时一次性迁移为每源数字
  （`migrate_legacy_priority_config`）；东财被显式禁用后不再自动补链尾
  （云图/北向/估值/财报等东财独占接口随之为无兜底，属用户明示行为）。
"""

from __future__ import annotations

import re
import asyncio
from typing import Literal, Callable, cast
from datetime import date
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

# 选单值/手写值 → 供应商 id（含常用简称）；旧链串迁移仍走这套别名
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

FALLBACK_PROVIDER_ID = "eastmoney"
PRIORITY_CONFIG_PREFIX = "market_api_priority_"
LEGACY_PRIORITY_CONFIG_KEY = "market_api_priority"
DEFAULT_PRIORITY = "东方财富 → 腾讯财经 → 新浪财经"

# 各源系统内禀序号（平局裁决用）：数字大的先执行，调整平局顺序 = 直接改数值。
# 序号须 < 10，用户数字 × 10 才能严格主导
_PROVIDER_RANKS: dict[str, int] = {
    "eastmoney": 9,
    "tencent": 8,
    "sina": 7,
    "ths": 6,
}

# 每源出厂优先级数字（东财→腾讯→新浪→同花顺：40/30/20/10）
_DEFAULT_PRIORITY_NUMBERS: dict[str, int] = {
    "eastmoney": 40,
    "tencent": 30,
    "sina": 20,
    "ths": 10,
}

# 旧链串位置 → 迁移数字（旧体系只有东财/腾讯/新浪三源）
_LEGACY_POSITION_NUMBERS = (40, 30, 20)

# 链字符串分隔符：→ > ， , 及空白
_CHAIN_SPLIT = re.compile(r"[→>，,]")


def normalize_provider_id(value: object) -> str | None:
    """选单值（中文展示名/简称/裸 id）→ 供应商 id；未知返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    return _PROVIDER_ALIASES.get(text)


def parse_priority_chain(raw: object) -> list[str]:
    """优先级链配置 → 供应商 id 列表（保序去重，未知片段忽略）。"""
    text = str(raw or "")
    ids: list[str] = []
    for part in _CHAIN_SPLIT.split(text):
        pid = normalize_provider_id(part)
        if pid is not None and pid not in ids:
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


def default_config_reader(key: str, fallback: str) -> str:
    """读后台「行情API」配置；缺 gsuid_core 环境（单测）时回退默认值。"""
    try:
        from ...stock_config.stock_config import STOCK_CONFIG

        cfg = STOCK_CONFIG.get_config(key)
        data = getattr(cfg, "data", None)
        # 每源优先级是 int 配置、其余是 str，统一转 str 供链构建解析
        if isinstance(data, (str, int)) and str(data).strip():
            return str(data)
        return fallback
    except Exception:  # noqa: BLE001 - 配置层不可用时保持默认装配
        return fallback


def _read_priority_number(reader: Callable[[str, str], str], pid: str) -> int:
    """读单源优先级数字：非法回默认，越界夹到 0-100。"""
    raw = reader(f"{PRIORITY_CONFIG_PREFIX}{pid}", str(_DEFAULT_PRIORITY_NUMBERS[pid]))
    try:
        num = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_PRIORITY_NUMBERS[pid]
    return max(0, min(100, num))


def build_priority_chain(reader: Callable[[str, str], str]) -> list[str]:
    """配置 → 实际调用链：每源优先级数字（0-100，大者先），0=禁用。

    最终顺序 = 用户数字 × 10 + 各源系统内禀序号（东财9/腾讯8/新浪7/同花顺6）：
    用户数字严格主导，数字相同时内禀序号大的先执行。
    全部禁用时保底东财，避免行情整体瘫痪。
    """
    scored: list[tuple[int, str]] = []
    for pid, rank in _PROVIDER_RANKS.items():
        num = _read_priority_number(reader, pid)
        if num <= 0:
            continue
        scored.append((num * 10 + rank, pid))
    if not scored:
        return [FALLBACK_PROVIDER_ID]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [pid for _, pid in scored]


def legacy_chain_to_numbers(chain: Sequence[str]) -> dict[str, int]:
    """旧优先级链 → 每源数字（迁移基准）。

    链内按位置取 40/30/20；旧三源不在链内 = 0（禁用，旧语义里链外源不参与）；
    东财不在链内先按旧语义补到链尾。同花顺不在返回值里（旧体系不存在，保持出厂默认）。
    """
    effective = list(chain)
    if FALLBACK_PROVIDER_ID not in effective:
        effective.append(FALLBACK_PROVIDER_ID)
    out: dict[str, int] = {}
    for pos, pid in enumerate(effective):
        if pos < len(_LEGACY_POSITION_NUMBERS):
            out[pid] = _LEGACY_POSITION_NUMBERS[pos]
        else:
            out[pid] = _DEFAULT_PRIORITY_NUMBERS.get(pid, 10)
    for pid in ("eastmoney", "tencent", "sina"):
        if pid not in effective:
            out[pid] = 0
    return out


def migrate_legacy_priority_config() -> bool:
    """旧 `market_api_priority` 链串 → 每源优先级数字；一次性、幂等，装配时调用。

    仅当新数字键全部仍是出厂默认时迁移（用户已改过新键则尊重现状）。
    """
    try:
        from ...stock_config.stock_config import STOCK_CONFIG
    except Exception:  # noqa: BLE001 - 无 gsuid_core 环境（最小依赖 CI）跳过
        return False
    try:
        legacy = STOCK_CONFIG.get_config(LEGACY_PRIORITY_CONFIG_KEY)
        legacy_data = getattr(legacy, "data", None)
        # 旧键已不在 CONFIG_DEFAULT：仅存量 config.json 残留时有值
        if not isinstance(legacy_data, str) or not legacy_data.strip():
            return False
        chain = parse_priority_chain(legacy_data)
        if not chain:
            return False
        numbers = legacy_chain_to_numbers(chain)
        changed: dict[str, int] = {}
        for pid, num in numbers.items():
            key = f"{PRIORITY_CONFIG_PREFIX}{pid}"
            current = getattr(STOCK_CONFIG.get_config(key), "data", None)
            if not isinstance(current, int):
                continue
            if current != _DEFAULT_PRIORITY_NUMBERS[pid]:
                return False  # 用户已使用新键，放弃迁移
            if current != num:
                changed[key] = num
        for key, num in changed.items():
            STOCK_CONFIG.set_config(key, num)
        if changed:
            logger.info(f"[SayuStock][行情API] 旧优先级链「{legacy_data}」已迁移为每源数字 {changed}")
        return bool(changed)
    except Exception as exc:  # noqa: BLE001 - 迁移失败不影响默认装配
        logger.warning(f"[SayuStock][行情API] 旧优先级配置迁移失败: {exc}")
        return False


def _stamp_provider(result: object, pid: str) -> object:
    """把命中源 id 写进结果模型，供渲染层展示真实数据来源（左下角标签）。"""
    from .display import stamp_provider

    return stamp_provider(result, pid)


class ConfigurableEquityMarket:
    """equity 槽位包装：按全局优先级链逐一尝试，尽可能交付。

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

    def _chain(self) -> list[tuple[str, MarketDataPort]]:
        chain: list[tuple[str, MarketDataPort]] = []
        for pid in build_priority_chain(self._reader):
            instance = self._instance(pid)
            if instance is not None:
                chain.append((pid, instance))
        return chain

    async def _dispatch(self, iface: str, method: str, *args: object, **kwargs: object) -> object:
        chain = self._chain()
        if not chain:
            return MarketError(
                code="unsupported",
                message=f"行情API无可用供应商（接口 {iface}）",
                provider="registry",
            )
        first_real_error: MarketError | None = None
        first_error: MarketError | None = None
        for i, (pid, port) in enumerate(chain):
            try:
                result = await getattr(port, method)(*args, **kwargs)
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
        return first_real_error or first_error

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
