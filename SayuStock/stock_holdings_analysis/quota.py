"""持仓分析 · 每用户每自然日配额（原子占坑，失败可释放）。"""

from __future__ import annotations

import os
from pathlib import Path
from datetime import date

from ..utils.resource_path import DATA_PATH

_QUOTA_ROOT = DATA_PATH / "holdings_analysis_quota"
MAX_SYMBOLS = 8


def unlimited_user_ids() -> frozenset[str]:
    """每次调用都读配置，网页控制台改名单后热生效。"""
    from ..stock_config.stock_config import STOCK_CONFIG

    raw: list[str] = STOCK_CONFIG.get_config("holdings_analysis_unlimited_users").data
    return frozenset(item.strip() for item in raw if item.strip())


def is_unlimited_user(user_id: str) -> bool:
    return str(user_id).strip() in unlimited_user_ids()


def _safe_id(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (s or ""))[:128]


def quota_path(user_id: str, bot_id: str, day: date | None = None) -> Path:
    d = day or date.today()
    return _QUOTA_ROOT / d.isoformat() / f"{_safe_id(bot_id)}__{_safe_id(user_id)}.used"


def is_quota_available(user_id: str, bot_id: str, day: date | None = None) -> bool:
    """今日尚未占坑则 True。免限额用户始终 True。"""
    if is_unlimited_user(user_id):
        return True
    return not quota_path(user_id, bot_id, day).is_file()


def try_claim_quota(user_id: str, bot_id: str, day: date | None = None) -> bool:
    """原子占坑（O_EXCL）；成功 True，已占用 False。免限额用户不落文件。"""
    if is_unlimited_user(user_id):
        return True
    p = quota_path(user_id, bot_id, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(p), flags)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("1")
    return True


def release_quota(user_id: str, bot_id: str, day: date | None = None) -> None:
    """失败路径释放占坑，允许同日重试。"""
    p = quota_path(user_id, bot_id, day)
    if p.is_file():
        p.unlink()


def mark_quota_used(user_id: str, bot_id: str, day: date | None = None) -> None:
    """兼容旧调用：等价于成功路径保留 claim（若未 claim 则创建）。"""
    if is_unlimited_user(user_id):
        return
    p = quota_path(user_id, bot_id, day)
    if p.is_file():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1", encoding="utf-8")


def clear_quota_for_test(user_id: str, bot_id: str, day: date | None = None) -> None:
    """单测清理。"""
    p = quota_path(user_id, bot_id, day)
    if p.is_file():
        p.unlink()
