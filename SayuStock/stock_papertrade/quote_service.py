"""持仓报价服务（60s TTL 内存缓存 + 东财 push2 轻量报价）。

2026-07-01 新增。背景：

  ``papertrade_position_list`` / ``papertrade_account_query`` 之前不返回现价，
  导致 LLM 拿到数据后无法计算持仓市值 / 浮盈 / 总资产。本模块给两个工具加一层
  "自动刷报价" 后端：

    - ``quote_service.get_quote(secid) -> Optional[float]``
        单只股票当前价；60s 内存复用。
    - ``quote_service.get_quotes_batch(secids) -> dict[str, Optional[float]]``
        批量；先查缓存，缺失项并发去打 /api/qt/stock/get。

API：

  - 端点：``https://push2.eastmoney.com/api/qt/stock/get``
  - 字段：仅取 ``f43,f44,f45,f46,f60,f57`` 6 个（不复用 SINGLE_STOCK_FIELDS 那
    40 个，单价查询 < 1KB 体量）。``f43``=当前价，``f57``=名称。
  - 复用现有的 ``EASTMONEY_REQUESTER.stock_request`` 拿 push2/push2delay failover。

降级：
  - API 失败 → 返回 ``None``；调用方按 ``last_quote_price → avg_cost → None`` 顺序兜底。
  - 老库 ``last_quote_price`` 列尚未迁移完（重启前）→ 该方法仍能跑，但写回 DB
    的 ``bulk_set_quote`` 会因列不存在抛 OperationalError；调用方需要 try/except 兜。

并发：
  - ``_lock`` 保护同一 ``(secid, ts_window)`` 内并发触发的重复 API。一次会话内
    同一秒里 N 个并发 ``get_quote(secid)`` 只发一次 HTTP。

参考模式：``gsuid_core/ai_core/budget/manager.py:121-150``（BudgetManager 单
timestamp + 显式 ``invalidate()``）。
"""

from __future__ import annotations

import time
import asyncio
from typing import Dict, List, Optional
from dataclasses import field, dataclass

from gsuid_core.logger import logger

# ============================================================
# 常量
# ============================================================
QUOTE_CACHE_TTL: float = 60.0  # 内存缓存秒数；超过即穿透去拉
QUOTE_FIELDS: str = "f43,f44,f45,f46,f60,f57"  # 仅 6 字段，对应：价/涨跌额/涨跌幅/振幅/昨收/名称
QUOTE_ENDPOINT: str = "https://push2.eastmoney.com/api/qt/stock/get"
QUOTE_TIMEOUT_S: float = 8.0  # 单只 HTTP 超时


# ============================================================
# 数据结构
# ============================================================
@dataclass
class QuoteCacheEntry:
    """单只股票的缓存条目。"""

    secid: str
    price: Optional[float]
    fetched_at: float = field(default_factory=time.time)
    name: Optional[str] = None  # 仅诊断用，不暴露给业务


# ============================================================
# 主服务
# ============================================================
class QuoteService:
    """60s TTL in-memory quote cache + EastMoney push2 fetcher。

    单例 — 由 ``quote_service`` 模块级实例调用，无需自己 ``QuoteService()``。
    """

    _instance: Optional["QuoteService"] = None

    def __init__(self) -> None:
        self._cache: Dict[str, QuoteCacheEntry] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock: asyncio.Lock = asyncio.Lock()
        # 统计：监控 cache 命中 / 穿透比
        self._hits: int = 0
        self._misses: int = 0

    # ----------------------------------------------------------------
    # 单例
    # ----------------------------------------------------------------
    @classmethod
    def instance(cls) -> "QuoteService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----------------------------------------------------------------
    # 内部 helper：拿 secid 维度的锁
    # ----------------------------------------------------------------
    async def _get_lock(self, secid: str) -> asyncio.Lock:
        async with self._global_lock:
            if secid not in self._locks:
                self._locks[secid] = asyncio.Lock()
            return self._locks[secid]

    # ----------------------------------------------------------------
    # 公共 API：单只
    # ----------------------------------------------------------------
    async def get_quote(self, secid: str) -> Optional[float]:
        """拿一只股票的当前价；带 60s TTL 缓存 + per-key lock 防穿透。"""
        if not secid:
            return None
        now = time.time()
        cached = self._cache.get(secid)
        if cached is not None and (now - cached.fetched_at) < QUOTE_CACHE_TTL:
            self._hits += 1
            return cached.price

        lock = await self._get_lock(secid)
        async with lock:
            # 双重检查：拿锁期间其它协程可能已经拉过
            cached = self._cache.get(secid)
            if cached is not None and (time.time() - cached.fetched_at) < QUOTE_CACHE_TTL:
                self._hits += 1
                return cached.price

            self._misses += 1
            price: Optional[float] = None
            name: Optional[str] = None
            try:
                price, name = await self._fetch_one(secid)
            except Exception as e:
                logger.debug(f"[PaperTrade][Quote] secid={secid} 拉报价失败: {e}")
                # 失败也写一条 None 缓存，避免下一秒立刻又重试；TTL 仍是 60s
                # （调大 TTL 也可，但这层 cache 是临时挡板，主要兜底在 DB 列）
            self._cache[secid] = QuoteCacheEntry(
                secid=secid, price=price, name=name, fetched_at=time.time()
            )
            return price

    # ----------------------------------------------------------------
    # 公共 API：批量（缓存优先；并发拉缺失项）
    # ----------------------------------------------------------------
    async def get_quotes_batch(
        self, secids: List[str]
    ) -> Dict[str, Optional[float]]:
        """批量取价；先查缓存，把缺失的塞 ``gather`` 并发去拉。

        缺失项复用 ``get_quote``（而不是直接裸调 ``_fetch_one``），这样批量
        调用和单只调用共享同一把 per-secid 锁——避免 ``ai_tools.py`` 里前后
        调用 ``get_quote(secid)`` 和 ``get_quotes_batch([..., secid, ...])``
        时对同一只股票并发打两次东财接口。
        """
        result: Dict[str, Optional[float]] = {}
        if not secids:
            return result

        # 去重：同一批次里出现两次的 secid 只拉一次
        unique_secids: List[str] = list(dict.fromkeys(secids))

        # 1) 缓存命中
        now = time.time()
        misses: List[str] = []
        for secid in unique_secids:
            entry = self._cache.get(secid)
            if entry is not None and (now - entry.fetched_at) < QUOTE_CACHE_TTL:
                result[secid] = entry.price
                self._hits += 1
            else:
                misses.append(secid)

        if misses:
            fetched = await asyncio.gather(
                *(self.get_quote(s) for s in misses), return_exceptions=True
            )
            for secid, item in zip(misses, fetched):
                if isinstance(item, Exception):
                    logger.debug(f"[PaperTrade][Quote] secid={secid} failed: {item}")
                    result[secid] = None
                else:
                    result[secid] = item  # type: ignore[assignment]

        return {s: result.get(s) for s in secids}

    # ----------------------------------------------------------------
    # 内部：单次 HTTP
    # ----------------------------------------------------------------
    async def _fetch_one(self, secid: str) -> tuple[Optional[float], Optional[str]]:
        """拉一次；返回 ``(price, name)``，出错返回 ``(None, None)``。"""
        from ..utils.eastmoney import EASTMONEY_REQUESTER

        params = [
            ("fields", QUOTE_FIELDS),
            ("invt", "2"),
            ("fltt", "2"),
            ("secid", secid),
        ]
        try:
            resp = await asyncio.wait_for(
                EASTMONEY_REQUESTER.stock_request(QUOTE_ENDPOINT, "GET", params=params),
                timeout=QUOTE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.debug(f"[PaperTrade][Quote] secid={secid} 超时 (>={QUOTE_TIMEOUT_S}s)")
            return (None, None)
        except Exception as e:
            logger.debug(f"[PaperTrade][Quote] secid={secid} HTTP 失败: {e}")
            return (None, None)

        if isinstance(resp, int):  # -999 / -400016 等错误码
            return (None, None)
        if not isinstance(resp, dict):
            return (None, None)
        data = resp.get("data")
        if not isinstance(data, dict):
            return (None, None)
        raw_price = data.get("f43")
        if raw_price is None:
            return (None, None)
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            return (None, None)
        if price <= 0:
            return (None, None)
        name_raw = data.get("f57")
        name = str(name_raw) if name_raw else None
        return (price, name)

    # ----------------------------------------------------------------
    # 调试 / 维护
    # ----------------------------------------------------------------
    def invalidate(self, secid: Optional[str] = None) -> None:
        """清缓存。``secid=None`` 时全清；管理工具 / 测试用。"""
        if secid is None:
            self._cache.clear()
        else:
            self._cache.pop(secid, None)

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "cached_keys": len(self._cache)}


# ============================================================
# 模块级单例
# ============================================================
quote_service: QuoteService = QuoteService.instance()
