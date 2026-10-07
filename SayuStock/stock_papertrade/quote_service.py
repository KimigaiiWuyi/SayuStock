"""持仓报价服务（TTL 内存缓存 + 行情端口轻量报价）。

2026-07-01 新增。背景：

  ``papertrade_position_list`` / ``papertrade_account_query`` 之前不返回现价，
  导致 LLM 拿到数据后无法计算持仓市值 / 浮盈 / 总资产。本模块给两个工具加一层
  "自动刷报价" 后端：

    - ``quote_service.get_quote(secid) -> Optional[float]``
        单只股票当前价；TTL 内存复用（成功 60s / 失败 5s）。
    - ``quote_service.get_quotes_batch(secids) -> dict[str, Optional[float]]``
        批量；先查缓存，缺失项并发取价。

API：

  - 取价走行情端口：``get_market().quote(secid)`` —— 按源链（默认
    东财→腾讯→新浪→同花顺，后台可配）逐一尝试，**东财限流时自动顺延**，
    不再整条链路失守。
  - 现价 / 昨收 / 涨跌幅 / 名称语义由端口 ``Quote`` 模型保证
    （等价于原先直接用 push2 的 f43/f60/f45/f57）。
  - 代价：端口 quote 复用 ``get_single_stock``（SINGLE_STOCK_FIELDS ~50 字段
    并合并当日分时），单次响应体积比原先手写的 6 字段请求大；换来的是
    多源容错与字段口径统一。

降级：
  - 全部源都失败 → 返回 ``None``；调用方按 ``last_quote_price → avg_cost → None`` 顺序兜底。
  - 老库 ``last_quote_price`` 列尚未迁移完（重启前）→ 该方法仍能跑，但写回 DB
    的 ``bulk_set_quote`` 会因列不存在抛 OperationalError；调用方需要 try/except 兜。

TTL 分两种（见 :data:`QUOTE_FAIL_TTL`）：成功条目 60s，失败条目 5s。
失败也要缓存——否则东财限流期间每秒重试会把它继续逼进 ``-400016``——但沿用
60s 会让一次限流把该票整整一分钟锁死，即便东财早已恢复也照样返回 None。
撮合层把 ``None`` 当作"行情不可达"直接拒单，所以这个 TTL 直接决定拒单持续多久。

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
QUOTE_CACHE_TTL: float = 60.0  # 成功取价的内存缓存秒数；超过即穿透去拉
# 单只股票的**取价总预算**（秒）。它同时是链上每个源的时间片来源：
# chain_deadline 让链上各源按「剩余预算 / 剩余源数」分摊（4 源时各约 3s），
# 所以东财挂起时腾讯/新浪仍轮得到。只包一层 wait_for 的话，第一个源挂起
# 就把预算吃光、后面的源一个都不会开始，而容灾恰恰是在那时才需要生效。
# 12s = 4 源 × 3s，远大于实测健康单源取价最慢值（0.85s）。
QUOTE_TIMEOUT_S: float = 12.0
# 失败缓存 TTL 必须**远小于**成功 TTL。失败也要缓存（否则限流期间每秒重试
# 会把东财继续逼进 -400016），但锁 60s 太长：一次限流会让该票整整一分钟
# 拿不到价，即便东财早已恢复也照样拒单。5s 足够挡住抖动，又不至于拖死整轮决策。
QUOTE_FAIL_TTL: float = 5.0


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
    last_close: Optional[float] = None  # f60 昨收价
    change_pct: Optional[float] = None  # f45 涨跌幅（%，如 9.99）

    @property
    def ttl(self) -> float:
        """本条目该活多久。失败条目用短 TTL，不让它拖住恢复后的取价。"""
        return QUOTE_FAIL_TTL if self.price is None else QUOTE_CACHE_TTL

    def is_fresh(self, now: Optional[float] = None) -> bool:
        ts = time.time() if now is None else now
        return (ts - self.fetched_at) < self.ttl


# ============================================================
# 主服务
# ============================================================
class QuoteService:
    """TTL in-memory quote cache + 行情端口取价（get_market().quote）。

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
        if cached is not None and cached.is_fresh(now):
            self._hits += 1
            return cached.price

        lock = await self._get_lock(secid)
        async with lock:
            # 双重检查：拿锁期间其它协程可能已经拉过
            cached = self._cache.get(secid)
            if cached is not None and cached.is_fresh():
                self._hits += 1
                return cached.price

            self._misses += 1
            price: Optional[float] = None
            last_close: Optional[float] = None
            change_pct: Optional[float] = None
            name: Optional[str] = None
            try:
                price, last_close, change_pct, name = await self._fetch_one(secid)
            except Exception as e:
                logger.debug(f"[PaperTrade][Quote] secid={secid} 拉报价失败: {e}")
                # 失败也写一条 None 缓存，避免下一秒立刻又重试；TTL 仍是 60s
                # （调大 TTL 也可，但这层 cache 是临时挡板，主要兜底在 DB 列）
            self._cache[secid] = QuoteCacheEntry(
                secid=secid,
                price=price,
                name=name,
                last_close=last_close,
                change_pct=change_pct,
                fetched_at=time.time(),
            )
            return price

    # ----------------------------------------------------------------
    # 公共 API：单条目（后向兼容 get_quote 拿现价）
    # ----------------------------------------------------------------
    async def get_quote_detail(self, secid: str) -> Optional[QuoteCacheEntry]:
        """拿完整缓存条目（含 last_close / change_pct）。优先走缓存；缺失穿透。"""
        if not secid:
            return None
        now = time.time()
        cached = self._cache.get(secid)
        if cached is not None and cached.is_fresh(now):
            return cached
        # 穿透一次 get_quote 让它把整条 cache entry 写齐
        await self.get_quote(secid)
        return self._cache.get(secid)

    # ----------------------------------------------------------------
    # 公共 API：批量（缓存优先；并发拉缺失项）
    # ----------------------------------------------------------------
    async def get_quotes_batch(self, secids: List[str]) -> Dict[str, Optional[float]]:
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
            if entry is not None and entry.is_fresh(now):
                result[secid] = entry.price
                self._hits += 1
            else:
                misses.append(secid)

        if misses:
            fetched = await asyncio.gather(*(self.get_quote(s) for s in misses), return_exceptions=True)
            for secid, item in zip(misses, fetched):
                if isinstance(item, BaseException):
                    logger.debug(f"[PaperTrade][Quote] secid={secid} failed: {item}")
                    result[secid] = None
                else:
                    result[secid] = item

        return {s: result.get(s) for s in secids}

    async def get_details_batch(self, secids: List[str]) -> Dict[str, Optional[QuoteCacheEntry]]:
        """批量取完整条目（含 change_pct / last_close），供候选池过滤涨停/过热用。

        先走 ``get_quotes_batch`` 把缓存喂满（并发 + per-secid 锁复用），再从缓存
        取整条目。只关心 change_pct 一类元数据时用它，避免调用方拿 price 后还得
        自己回查缓存。
        """
        if not secids:
            return {}
        await self.get_quotes_batch(secids)
        return {s: self._cache.get(s) for s in secids}

    # ----------------------------------------------------------------
    # 内部：单次 HTTP
    # ----------------------------------------------------------------
    async def _fetch_one(self, secid: str) -> tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
        """拉一次；返回 ``(price, last_close, change_pct, name)``。

        走 ``get_market().quote()``：现价/昨收/涨跌幅/名称语义由端口保证，
        东财限流时按优先级链顺延到腾讯/新浪，不再整条链路失守。

        **失败必须返回全 None**（不可放行任何价格）：调用方以 None 表示
        「拿不到实时价」，据此拒绝入库，勿改成兜底默认值。
        """
        from ..utils.market import get_market, chain_deadline
        from ..utils.market.errors import is_market_error

        try:
            # chain_deadline 把总预算摊到每个源（同步上下文管理器，只写 ContextVar）；
            # 外层 wait_for 只是硬保险，防的是链外还有别的耗时
            with chain_deadline(QUOTE_TIMEOUT_S):
                quote = await asyncio.wait_for(get_market().quote(secid), timeout=QUOTE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.debug(f"[PaperTrade][Quote] secid={secid} 超时 (>={QUOTE_TIMEOUT_S}s)")
            return (None, None, None, None)
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            logger.debug(f"[PaperTrade][Quote] secid={secid} HTTP 失败: {e}")
            return (None, None, None, None)

        if is_market_error(quote) or quote.price <= 0:
            return (None, None, None, None)
        return (quote.price, quote.prev_close, quote.change_pct, quote.symbol.name)

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
