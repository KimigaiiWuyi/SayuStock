"""模拟盘交易日历（数据驱动）。

判定链（自上而下，先命中先返回）：

1. **周末** → 非交易日
2. **权威休市表** → 非交易日。上证指数 ``1.000001`` 日 K 在扫描窗口内
   没有 bar 的工作日即休市日，由 ``refresh_daily_calendar`` 落盘到
   ``papertrade_trading_calendar.json`` 的 ``closed_days``
3. **分时自证** → 非交易日。交易时段开始满 :data:`_SESSION_GRACE` 分钟后，
   缓存里的上证分时最后一点仍停在更早的日子，说明交易所压根没开市
4. **内置表兜底** → 仅当上面都没有缓存可用时（首次启动 / 网络不可达）

第 4 条的 :data:`FALLBACK_CLOSED_DAYS` 是**离线兜底**，不是事实源：它由
``holidays`` 库按年生成并并上少量静态表，行情不可达时才用。旧实现是一张纯
手写表，漏一年就会在长假期间照常交易（2026 年国庆就是这么进脏数据的）。
联网后第 2/3 条会覆盖它，所以本文件不再往静态表里加新一年的假期。

同步 / 异步分工：``is_a_share_trading_day`` 等同步 API **只读缓存不发网络**
（``commands._is_market_open_now`` / ``next_decision_time`` 是同步调用点）；
联网刷新走 ``should_run_papertrade_async`` / ``is_trading_day_async`` /
``refresh_daily_calendar``，由 recurring gate 与启动钩子驱动。
"""

from __future__ import annotations

import json
from typing import Tuple, Optional, TypedDict
from datetime import date, time, datetime, timedelta

from gsuid_core.logger import logger

from ..utils.market import KlinePeriod, get_market, is_market_error
from ..utils.resource_path import DATA_PATH

_CALENDAR_CACHE_PATH = DATA_PATH / "papertrade_trading_calendar.json"

# 上证指数 secid。必须带市场前缀：裸 "000001" 会被解析成平安银行。
_INDEX_SECID = "1.000001"

_INTRADAY_TTL_SECONDS = 60
_DAILY_TTL_SECONDS = 6 * 3600
_SESSION_GRACE_MINUTES = 3

# 日 K 扫描窗口：覆盖账户可能的全部生命周期，一次请求拿回全部休市日。
_DAILY_LOOKBACK_DAYS = 730

# 完整性下限：实测 A 股工作日里约 92%~93% 是交易日（其余是法定休市），
# 留足余量取 60%。低于此值说明上游只返回了部分数据，此时"缺失"是数据问题
# 而不是休市，绝不能据此生成休市表——那会让自愈删掉大量正常成交。
_MIN_BAR_DENSITY = 0.60
# 休市占比上限：正常年份工作日休市占比约 7%~8%，超过 30% 判据不可信。
_MAX_CLOSED_RATIO = 0.30

_LOG = "[SayuStock][PaperTrade][Calendar]"

# 离线兜底：优先用 ``holidays`` 库按年生成，覆盖所有已收录年份；
# 拿不到（未安装 / 未来年份未收录）时退回下面这份静态表。
_STATIC_FALLBACK_DAYS: frozenset[str] = frozenset(
    {
        # 2025 元旦
        "2025-01-01",
        # 2025 春节
        "2025-01-28",
        "2025-01-29",
        "2025-01-30",
        "2025-01-31",
        "2025-02-03",
        "2025-02-04",
        "2025-02-05",
        "2025-02-06",
        "2025-02-07",
        # 2025 清明
        "2025-04-04",
        "2025-04-05",
        "2025-04-06",
        # 2025 劳动节
        "2025-05-01",
        "2025-05-02",
        "2025-05-05",
        # 2025 端午
        "2025-05-31",
        "2025-06-02",
        # 2025 中秋 + 国庆
        "2025-10-01",
        "2025-10-02",
        "2025-10-03",
        "2025-10-06",
        "2025-10-07",
        "2025-10-08",
        # 2026 元旦
        "2026-01-01",
        "2026-01-02",
        # 2026 春节（2/24 起照常开市，旧版误把 2/24-2/27 也标成休市）
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-02-19",
        "2026-02-20",
        "2026-02-23",
        # 2026 清明
        "2026-04-06",
        # 2026 劳动节
        "2026-05-01",
        "2026-05-04",
        "2026-05-05",
        # 2026 端午
        "2026-06-19",
        # 2026 中秋
        "2026-09-25",
        # 2026 国庆
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
    }
)


def _closed_from_holidays(years: list[int]) -> frozenset[str]:
    """用 ``holidays`` 库取中国法定假日，落成 ISO 日期集合。

    库里的 CN 假日含调休上班的**周末**（股市同样休市）与法定假日，两者
    都被周末判定 / 交易日判定覆盖，取并集是安全的交集近似。
    """
    try:
        import holidays as _holidays_mod
    except ImportError:
        return frozenset()
    try:
        cal = _holidays_mod.country_holidays("CN", years=years)
    except (ValueError, KeyError, NotImplementedError):
        return frozenset()
    return frozenset(d.isoformat() for d in cal)


def _fallback_closed_days() -> frozenset[str]:
    """离线兜底休市表：静态表 ∪ holidays 库（当年 ± 1 年）。

    这样即便行情完全不可达，兜底也不再局限于手工维护的那两年。
    """
    year = date.today().year
    return _STATIC_FALLBACK_DAYS | _closed_from_holidays([year - 1, year, year + 1])


FALLBACK_CLOSED_DAYS: frozenset[str] = _fallback_closed_days()


class CalendarCache(TypedDict):
    """``papertrade_trading_calendar.json`` 的落盘结构。"""

    intraday_last_date: str
    intraday_checked_at: float
    daily_last_date: str
    daily_checked_at: float
    daily_window_start: str
    daily_window_end: str
    closed_days: list[str]


# ============================================================
# 缓存读写
# ============================================================
def _as_str(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _as_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _coerce_cache(raw: object) -> Optional[CalendarCache]:
    """逐字段校验磁盘 JSON；任一字段缺失或类型不符就整体作废，不做部分补全。"""
    if not isinstance(raw, dict):
        return None
    i_last = _as_str(raw.get("intraday_last_date"))
    i_at = _as_float(raw.get("intraday_checked_at"))
    d_last = _as_str(raw.get("daily_last_date"))
    d_at = _as_float(raw.get("daily_checked_at"))
    d_start = _as_str(raw.get("daily_window_start"))
    d_end = _as_str(raw.get("daily_window_end"))
    closed_raw = raw.get("closed_days")
    if (
        i_last is None
        or i_at is None
        or d_last is None
        or d_at is None
        or d_start is None
        or d_end is None
        or not isinstance(closed_raw, list)
    ):
        return None
    closed: list[str] = []
    for item in closed_raw:
        text = _as_str(item)
        if text is None:
            return None
        closed.append(text)
    return CalendarCache(
        intraday_last_date=i_last,
        intraday_checked_at=i_at,
        daily_last_date=d_last,
        daily_checked_at=d_at,
        daily_window_start=d_start,
        daily_window_end=d_end,
        closed_days=closed,
    )


def load_calendar_cache() -> Optional[CalendarCache]:
    """读缓存；文件不存在 / JSON 损坏 / 字段不合规一律返回 None。"""
    if not _CALENDAR_CACHE_PATH.exists():
        return None
    try:
        raw: object = json.loads(_CALENDAR_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"{_LOG} 读缓存失败（按无缓存处理）: {e}")
        return None
    return _coerce_cache(raw)


def save_calendar_cache(cache: CalendarCache) -> None:
    """落盘缓存。写失败只告警——日历不可用时还有内置表兜底。"""
    try:
        _CALENDAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CALENDAR_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"{_LOG} 写缓存失败: {e}")


def _intraday_date_if_fresh(cache: Optional[CalendarCache]) -> Optional[str]:
    """分时自证只在缓存 TTL 内有效；陈旧的分时日期不能用来判今天休市。"""
    if cache is None:
        return None
    if datetime.now().timestamp() - cache["intraday_checked_at"] > _INTRADAY_TTL_SECONDS:
        return None
    return cache["intraday_last_date"]


def authoritative_closed_days() -> frozenset[str]:
    """权威休市日（只读缓存）。日 K 缓存过期就不采信，返回空集。"""
    return _authoritative_closed(load_calendar_cache())


async def resolve_closed_days(*, allow_refresh: bool = True) -> Tuple[Optional[frozenset[str]], str]:
    """拿到权威休市日集合，附来源说明（供日志 / 自愈报告用）。

    顺序：新鲜缓存 → 按需实时刷新 → 失败返回 ``None``。**不**回落到内置兜底表：
    自愈要删数据，宁可没有也不拿一张已知会过期的表当事实源。
    """
    cached = authoritative_closed_days()
    if cached:
        return cached, "上证日K(缓存)"
    if not allow_refresh:
        return None, "缓存过期且未允许刷新"
    refreshed = await refresh_daily_calendar()
    if refreshed:
        return refreshed, "上证日K(实时)"
    return None, "行情不可达"


def _authoritative_closed(cache: Optional[CalendarCache]) -> frozenset[str]:
    """权威休市日；日 K 缓存过期就不采信，退回空集让调用方走兜底。"""
    if cache is None:
        return frozenset()
    if datetime.now().timestamp() - cache["daily_checked_at"] > _DAILY_TTL_SECONDS:
        return frozenset()
    return frozenset(cache["closed_days"])


# ============================================================
# 联网刷新（异步）
# ============================================================
async def refresh_intraday() -> Optional[str]:
    """拉上证指数分时，落盘最后一个点的日期。返回该日期；不可用时返回 None。

    休市期间接口返回上一个交易日的完整分时，所以"最后一点的日期不是今天"
    就是今天没开市的确凿证据。
    """
    series = await get_market().intraday(_INDEX_SECID)
    if is_market_error(series):
        logger.warning(f"{_LOG} 上证分时不可达: {series.message}")
        return None
    if not series.points:
        logger.warning(f"{_LOG} 上证分时为空，无法自证今日是否开市")
        return None
    last = series.points[-1].ts.date().isoformat()
    cache = load_calendar_cache()
    if cache is None:
        cache = CalendarCache(
            intraday_last_date=last,
            intraday_checked_at=datetime.now().timestamp(),
            daily_last_date="",
            daily_checked_at=0.0,
            daily_window_start="",
            daily_window_end="",
            closed_days=[],
        )
    cache["intraday_last_date"] = last
    cache["intraday_checked_at"] = datetime.now().timestamp()
    save_calendar_cache(cache)
    return last


async def refresh_daily_calendar(lookback_days: int = _DAILY_LOOKBACK_DAYS) -> Optional[frozenset[str]]:
    """扫上证日 K，把窗口内"工作日但无 K 线"的日子落盘为权威休市表。

    这是自愈与判定的共同事实源：它直接反映交易所实际开过哪些天，
    不依赖任何人工维护的假期表。返回休市日集合；不可用时返回 None。

    **密度自检是这个函数的安全底线。** 它是"哪些日子没开市"的唯一判据，
    一旦上游只返回部分数据，没被返回的工作日会被**整片**误判成休市日，
    而自愈正是拿这张表去删流水——数据残缺会直接变成误删。所以这里除了
    "全空要拒绝"，还要拒绝**明显残缺**（见 :data:`_MIN_BAR_DENSITY`）。
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    series = await get_market().kline(_INDEX_SECID, KlinePeriod.D1, start=start, end=end)
    if is_market_error(series):
        logger.warning(f"{_LOG} 上证日 K 不可达: {series.message}")
        return None
    trading: set[str] = {bar.ts.date().isoformat() for bar in series.bars}
    if not trading:
        logger.warning(f"{_LOG} 上证日 K 为空，无法生成权威休市表")
        return None

    weekdays = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    if weekdays > 0:
        density = len(trading) / weekdays
        if density < _MIN_BAR_DENSITY:
            logger.error(
                f"{_LOG} 上证日 K 密度异常（{len(trading)}/{weekdays} = {density:.1%} < "
                f"{_MIN_BAR_DENSITY:.0%}），判定为数据残缺，**拒绝**生成休市表"
            )
            return None

    closed: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor.isoformat() not in trading:
            closed.append(cursor.isoformat())
        cursor += timedelta(days=1)

    # 停牌/无报价等极端情况理论上会推高休市占比；超过三成就说明判据本身
    # 不可信，宁可不产出（自愈会退到 holidays 兜底或整体放弃）
    if weekdays > 0 and len(closed) / weekdays > _MAX_CLOSED_RATIO:
        logger.error(
            f"{_LOG} 推导出的休市日占比 {len(closed)}/{weekdays} = "
            f"{len(closed) / weekdays:.1%} 超过 {_MAX_CLOSED_RATIO:.0%}，判定为判据异常，拒绝采用"
        )
        return None

    cache = load_calendar_cache()
    if cache is None:
        cache = CalendarCache(
            intraday_last_date="",
            intraday_checked_at=0.0,
            daily_last_date="",
            daily_checked_at=0.0,
            daily_window_start="",
            daily_window_end="",
            closed_days=[],
        )
    cache["daily_last_date"] = max(trading)
    cache["daily_checked_at"] = datetime.now().timestamp()
    cache["daily_window_start"] = start.isoformat()
    cache["daily_window_end"] = end.isoformat()
    cache["closed_days"] = closed
    save_calendar_cache(cache)
    logger.info(
        f"{_LOG} 权威休市表已刷新：{start}~{end} 内 {len(closed)} 个休市工作日，最后交易日 {cache['daily_last_date']}"
    )
    return frozenset(closed)


async def ensure_intraday_probe() -> None:
    """确保分时自证是新的。gate 每次触发都调它，TTL 内不发第二个请求。"""
    cache = load_calendar_cache()
    fresh = _intraday_date_if_fresh(cache)
    if fresh is not None:
        return
    await refresh_intraday()


# ============================================================
# 同步判定（只读缓存，绝不发网络）
# ============================================================
def _is_weekend(d: datetime) -> bool:
    return d.weekday() >= 5


def _session_grace_passed(d: datetime) -> bool:
    """交易时段是否已开满宽限时间（避免 09:30:00 首根分时未落地就误判休市）。"""
    t = d.time()
    if time(9, 30) <= t < time(9, 33):
        return False
    if time(13, 0) <= t < time(13, 3):
        return False
    return True


def is_a_share_trading_day(dt: Optional[datetime] = None) -> bool:
    """判断给定时间（默认现在）是否是 A 股交易日。纯同步、只读缓存。"""
    d = dt or datetime.now()
    if _is_weekend(d):
        return False

    today = d.date().isoformat()
    cache = load_calendar_cache()

    if today in _authoritative_closed(cache):
        return False

    probed = _intraday_date_if_fresh(cache)
    if probed is not None and probed < today and _session_grace_passed(d):
        if is_trading_time(d):
            return False

    return today not in FALLBACK_CLOSED_DAYS


def is_trading_time(dt: Optional[datetime] = None) -> bool:
    """判定当前是否在 A 股交易时段内（9:30-11:30 / 13:00-15:00）。"""
    d = dt or datetime.now()
    t = d.time()
    morning = time(9, 30) <= t <= time(11, 30)
    afternoon = time(13, 0) <= t <= time(15, 0)
    return morning or afternoon


def should_run_papertrade(dt: Optional[datetime] = None) -> bool:
    """综合判断：是否应该跑一次模拟盘决策。同步版只读缓存。"""
    return is_a_share_trading_day(dt) and is_trading_time(dt)


def next_decision_time(dt: Optional[datetime] = None) -> datetime:
    """返回下一个合理的决策触发时间（用于日志 / 重试 / 心跳规划）。"""
    now = dt or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if not is_a_share_trading_day(now):
        for offset in range(1, 15):
            candidate = today + timedelta(days=offset)
            if is_a_share_trading_day(candidate):
                return candidate.replace(hour=9, minute=30)
        return now + timedelta(hours=24)

    t = now.time()
    if time(9, 30) <= t <= time(11, 30):
        return now
    if time(11, 30) < t < time(13, 0):
        return now.replace(hour=13, minute=0, second=0, microsecond=0)
    if t >= time(15, 0):
        return (today + timedelta(days=1)).replace(hour=9, minute=30)
    return now.replace(hour=9, minute=30, second=0, microsecond=0)


def trading_day_summary(dt: Optional[datetime] = None) -> Tuple[bool, bool, str]:
    """汇总当前状态：(is_trading_day, is_trading_time, human_desc)"""
    now = dt or datetime.now()
    td = is_a_share_trading_day(now)
    tt = is_trading_time(now)
    if not td:
        return td, tt, f"{now.strftime('%Y-%m-%d %A')} 非交易日"
    if not tt:
        if now.time() < time(9, 30):
            return td, tt, f"{now.strftime('%Y-%m-%d %A')} 开盘前（9:30 开）"
        if time(11, 30) < now.time() < time(13, 0):
            return td, tt, "午间休市（13:00 复盘）"
        return td, tt, f"{now.strftime('%Y-%m-%d %A')} 已收盘"
    return td, tt, f"{now.strftime('%Y-%m-%d %A')} 交易时段"


# ============================================================
# 异步判定（recurring gate 用；会先自证再落同步判定）
# ============================================================
async def is_trading_day_async(dt: Optional[datetime] = None) -> bool:
    """联网自证后再判定交易日。无法自证时走缓存/兜底表，不抛异常。"""
    await ensure_intraday_probe()
    return is_a_share_trading_day(dt)


async def should_run_papertrade_async(dt: Optional[datetime] = None) -> bool:
    """recurring gate 入口：自证 + 交易日 + 交易时段。

    刻意不向上抛异常：框架的 gate 是 fail-open，一旦抛错就等于"放行交易"，
    那正是我们要拦的场景。探测失败时退回缓存/兜底表，由同步判定给结论。
    """
    d = dt or datetime.now()
    if not is_trading_time(d):
        return False
    await ensure_intraday_probe()
    return is_a_share_trading_day(d)
