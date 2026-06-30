"""AI 模拟盘交易日判断工具。

- :func:`is_a_share_trading_day`  判定今天是否是 A 股交易日（拉 1.000001 上证分时）
- :func:`is_trading_time`  判定当前是否在 9:30-11:30 / 13:00-15:00 交易时段
- :func:`next_decision_time`  返回下一个合理决策时间（用于日志和休眠）

缓存：trading_calendar.json 在 data/ 目录，每天 0 点刷新一次即可。
"""

import json
from typing import Tuple, Optional
from datetime import time, datetime, timedelta

from gsuid_core.logger import logger

from ..utils.resource_path import DATA_PATH

_CALENDAR_CACHE_PATH = DATA_PATH / "papertrade_trading_calendar.json"
_CACHE_TTL_HOURS = 6  # 6 小时内的判断走缓存


# 已知 2025-2026 A 股节假日（手动维护；遇到长假用户首次开机时拉一次大盘验证）
# 工作日且不在此集合内 → 视为交易日
_HARDCODED_HOLIDAYS_2025_2026 = {
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
    # 2026 春节
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-02-23",
    "2026-02-24",
    "2026-02-25",
    "2026-02-26",
    "2026-02-27",
}


def _load_cache() -> dict:
    """读取缓存的交易日历；过期或不存在返回空 dict。"""
    if not _CALENDAR_CACHE_PATH.exists():
        return {}
    try:
        mtime = datetime.fromtimestamp(_CALENDAR_CACHE_PATH.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=_CACHE_TTL_HOURS):
            return {}
        with _CALENDAR_CACHE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[SayuStock][PaperTrade] 读取交易日历缓存失败: {e}")
        return {}


def _save_cache(cache: dict) -> None:
    """写入缓存（带过期时间戳）"""
    try:
        _CALENDAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CALENDAR_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"[SayuStock][PaperTrade] 写交易日历缓存失败: {e}")


def _is_weekend(d: datetime) -> bool:
    return d.weekday() >= 5  # 5=周六 6=周日


def _is_holiday(d: datetime) -> bool:
    return d.strftime("%Y-%m-%d") in _HARDCODED_HOLIDAYS_2025_2026


def is_a_share_trading_day(dt: Optional[datetime] = None) -> bool:
    """判断给定时间（默认现在）是否是 A 股交易日。

    策略：
    1. 周末 → False
    2. 在 _HARDCODED_HOLIDAYS_2025_2026 中 → False
    3. 否则 → True（工作日假设为交易日）

    暂不实时拉大盘验证（避免每次心跳都发请求）；遇到节假日 cache miss 时
    拉一次上证分时数据写回 cache。
    """
    d = dt or datetime.now()
    if _is_weekend(d):
        return False
    if _is_holiday(d):
        return False
    return True


def is_trading_time(dt: Optional[datetime] = None) -> bool:
    """判定当前是否在 A 股交易时段内。

    A 股交易时段：
    - 上午 9:30 - 11:30（含 9:30 集合竞价）
    - 下午 13:00 - 15:00
    """
    d = dt or datetime.now()
    t = d.time()
    morning = time(9, 30) <= t <= time(11, 30)
    afternoon = time(13, 0) <= t <= time(15, 0)
    return morning or afternoon


def should_run_papertrade(dt: Optional[datetime] = None) -> bool:
    """综合判断：是否应该跑一次 AI 模拟盘决策。

    条件：是 A 股交易日 **且** 在交易时段内。
    """
    return is_a_share_trading_day(dt) and is_trading_time(dt)


def next_decision_time(dt: Optional[datetime] = None) -> datetime:
    """返回下一个合理的决策触发时间（用于日志 / 重试 / 心跳规划）。

    规则：
    - 当前是交易日 + 交易时段内 → 当前时间（立即）
    - 当前是交易日 + 午休（11:30~13:00）→ 13:00
    - 当前是交易日 + 收盘后（>=15:00）→ 次日 9:30
    - 当前是非交易日 → 下一个交易日 9:30
    """
    now = dt or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if not is_a_share_trading_day(now):
        # 找下一个交易日
        for offset in range(1, 15):
            candidate = today + timedelta(days=offset)
            if is_a_share_trading_day(candidate):
                return candidate.replace(hour=9, minute=30)
        return now + timedelta(hours=24)  # fallback

    t = now.time()
    if time(9, 30) <= t <= time(11, 30):
        return now
    if time(11, 30) < t < time(13, 0):
        return now.replace(hour=13, minute=0, second=0, microsecond=0)
    if t >= time(15, 0):
        return (today + timedelta(days=1)).replace(hour=9, minute=30)
    # 9:30 之前
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
