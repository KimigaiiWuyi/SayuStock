"""宏观事件表的仓储 + 字段归一（纯函数，单测不需要 DB）。

LLM 写进来的字段全部先过 ``normalize_*``：slug 只留小写字母数字下划线，分类 /
方向 / 状态落到枚举，严重度夹紧到 1~5。写工具不信任模型的任何原样字符串。
"""

from __future__ import annotations

import re
from typing import List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

from sqlmodel import col
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.utils.database.base_models import with_session

from ..utils.database.macro_models import (
    MACRO_STATUSES,
    MACRO_CATEGORIES,
    MACRO_DIRECTIONS,
    MACRO_STATUS_OPEN,
    MACRO_SEVERE_LEVEL,
    MACRO_SEVERITY_MAX,
    MACRO_SEVERITY_MIN,
    MACRO_STATUS_SETTLED,
    DEFAULT_CHECK_INTERVAL_HOURS,
    SayuMacroEvent,
)

__all__ = [
    "MacroEventRepo",
    "MacroEventInput",
    "normalize_slug",
    "normalize_category",
    "normalize_direction",
    "normalize_status",
    "normalize_severity",
    "is_due_for_check",
    "is_severe_risk_off",
]

_SLUG_KEEP = re.compile(r"[^a-z0-9_]+")
_SLUG_MAX_LEN = 64
_TEXT_MAX_LEN = 1200
_SHORT_MAX_LEN = 200

# 模型常用的同义写法 → 枚举值。大小写不敏感。
_DIRECTION_ALIASES: dict[str, str] = {
    "risk_off": "risk_off",
    "riskoff": "risk_off",
    "risk-off": "risk_off",
    "避险": "risk_off",
    "利空": "risk_off",
    "负面": "risk_off",
    "bearish": "risk_off",
    "risk_on": "risk_on",
    "riskon": "risk_on",
    "risk-on": "risk_on",
    "利好": "risk_on",
    "正面": "risk_on",
    "bullish": "risk_on",
    "mixed": "mixed",
    "混合": "mixed",
    "分歧": "mixed",
    "neutral": "neutral",
    "中性": "neutral",
}

_STATUS_ALIASES: dict[str, str] = {
    "open": MACRO_STATUS_OPEN,
    "ongoing": MACRO_STATUS_OPEN,
    "进行中": MACRO_STATUS_OPEN,
    "active": MACRO_STATUS_OPEN,
    "settled": MACRO_STATUS_SETTLED,
    "closed": MACRO_STATUS_SETTLED,
    "done": MACRO_STATUS_SETTLED,
    "盖棺定论": MACRO_STATUS_SETTLED,
    "已结束": MACRO_STATUS_SETTLED,
    "结束": MACRO_STATUS_SETTLED,
}

_CATEGORY_ALIASES: dict[str, str] = {
    "关税": "trade",
    "贸易": "trade",
    "tariff": "trade",
    "sanction": "trade",
    "地缘": "geopolitics",
    "战争": "geopolitics",
    "war": "geopolitics",
    "央行": "monetary",
    "利率": "monetary",
    "美联储": "monetary",
    "fed": "monetary",
    "rate": "monetary",
    "原油": "commodity",
    "油价": "commodity",
    "黄金": "commodity",
    "oil": "commodity",
    "政策": "policy",
    "数据": "macro_data",
    "ppi": "macro_data",
    "cpi": "macro_data",
    "pmi": "macro_data",
}


def normalize_slug(raw: str) -> str:
    """只保留 ``[a-z0-9_]``：空格 / 连字符转下划线，首尾下划线剥掉，超长截断。

    中文会被整段丢掉（``中美关税战`` → ``""``），所以工具 docstring 要求模型传英文键；
    结果为空由调用方拒绝并提示示例。
    """
    text = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = _SLUG_KEEP.sub("", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:_SLUG_MAX_LEN]


def normalize_category(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in MACRO_CATEGORIES:
        return key
    for alias, value in _CATEGORY_ALIASES.items():
        if alias in key:
            return value
    return "other"


def normalize_direction(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in MACRO_DIRECTIONS:
        return key
    if key in _DIRECTION_ALIASES:
        return _DIRECTION_ALIASES[key]
    return "mixed"


def normalize_status(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in MACRO_STATUSES:
        return key
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key]
    return MACRO_STATUS_OPEN


def normalize_severity(raw: int | float | str) -> int:
    if isinstance(raw, bool):
        return 3
    if isinstance(raw, (int, float)):
        value = int(raw)
    elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        value = int(raw.strip())
    else:
        return 3
    return max(MACRO_SEVERITY_MIN, min(MACRO_SEVERITY_MAX, value))


def _clip(text: str, limit: int) -> str:
    cleaned = "".join(ch if ch.isprintable() or ch == "\n" else " " for ch in (text or "")).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."


def is_due_for_check(event: SayuMacroEvent, now: Optional[datetime] = None) -> bool:
    """进行中且距上次复核超过间隔（从未复核视为到期）；盖棺定论永不到期。"""
    if event.status != MACRO_STATUS_OPEN:
        return False
    if event.last_checked_at is None:
        return True
    moment = now or datetime.now()
    hours = event.check_interval_hours if event.check_interval_hours > 0 else DEFAULT_CHECK_INTERVAL_HOURS
    return moment - event.last_checked_at >= timedelta(hours=hours)


def is_severe_risk_off(event: SayuMacroEvent) -> bool:
    return event.status == MACRO_STATUS_OPEN and event.direction == "risk_off" and event.severity >= MACRO_SEVERE_LEVEL


DEFAULT_CATEGORY: str = "other"
DEFAULT_SEVERITY: int = 3
DEFAULT_DIRECTION: str = "mixed"


@dataclass(frozen=True, slots=True)
class MacroEventInput:
    """``upsert`` 的归一化入参；由 ``from_raw`` 从模型自由文本构造。

    ``category`` / ``severity`` / ``direction`` / ``status`` 为 ``None`` 表示「调用方没说」：
    更新已有事件时保留原值，新建时落默认值。否则低能力模型只想改个 result，
    会把 severity 4 的关税战默默改回 3。
    """

    slug: str
    title: str
    category: Optional[str]
    status: Optional[str]
    severity: Optional[int]
    direction: Optional[str]
    affected_sectors: str
    summary: str
    result: str
    stance: str
    source_urls: str
    check_interval_hours: Optional[int]
    created_by: str

    @classmethod
    def from_raw(
        cls,
        *,
        slug: str,
        title: str,
        category: str = "",
        status: str = "",
        severity: int | float | str | None = None,
        direction: str = "",
        affected_sectors: str = "",
        summary: str = "",
        result: str = "",
        stance: str = "",
        source_urls: str = "",
        check_interval_hours: int = 0,
        created_by: str = "ai",
    ) -> "MacroEventInput":
        hours: Optional[int] = check_interval_hours if 1 <= check_interval_hours <= 168 else None
        sev_raw = severity if severity is not None and str(severity).strip() != "" else None
        return cls(
            slug=normalize_slug(slug),
            title=_clip(title, _SHORT_MAX_LEN),
            category=normalize_category(category) if category.strip() else None,
            status=normalize_status(status) if status.strip() else None,
            severity=normalize_severity(sev_raw) if sev_raw is not None else None,
            direction=normalize_direction(direction) if direction.strip() else None,
            affected_sectors=_clip(affected_sectors, _SHORT_MAX_LEN),
            summary=_clip(summary, _TEXT_MAX_LEN),
            result=_clip(result, _TEXT_MAX_LEN),
            stance=_clip(stance, _TEXT_MAX_LEN),
            source_urls=_clip(source_urls, _TEXT_MAX_LEN),
            check_interval_hours=hours,
            created_by=_clip(created_by, 64) or "ai",
        )

    @property
    def status_or_open(self) -> str:
        return self.status or MACRO_STATUS_OPEN


class MacroEventRepo:
    @classmethod
    @with_session
    async def get_by_slug(cls, session: AsyncSession, slug: str) -> Optional[SayuMacroEvent]:
        stmt = select(SayuMacroEvent).where(col(SayuMacroEvent.slug) == slug)
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def list_by_status(
        cls,
        session: AsyncSession,
        status: Optional[str] = MACRO_STATUS_OPEN,
        limit: int = 50,
    ) -> List[SayuMacroEvent]:
        """按严重度降序、更新时间降序列出；``status=None`` 表示全部。"""
        stmt = select(SayuMacroEvent)
        if status is not None:
            stmt = stmt.where(col(SayuMacroEvent.status) == status)
        stmt = stmt.order_by(col(SayuMacroEvent.severity).desc(), col(SayuMacroEvent.updated_at).desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_open_severe(cls, session: AsyncSession) -> List[SayuMacroEvent]:
        """买入硬闸用：进行中 + risk_off + 严重度 ≥ 高危线。"""
        stmt = select(SayuMacroEvent).where(
            col(SayuMacroEvent.status) == MACRO_STATUS_OPEN,
            col(SayuMacroEvent.direction) == "risk_off",
            col(SayuMacroEvent.severity) >= MACRO_SEVERE_LEVEL,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def upsert(
        cls,
        session: AsyncSession,
        data: MacroEventInput,
        *,
        touch_checked: bool,
    ) -> SayuMacroEvent:
        """按 slug 幂等写入。

        ``touch_checked=True`` 表示本次写入来自一次真实联网复核，顺带刷
        ``last_checked_at``；只是登记 / 改备注时传 False，别把"我改了个标题"
        伪装成"我刚查过"。
        """
        now = datetime.now()
        stmt = select(SayuMacroEvent).where(col(SayuMacroEvent.slug) == data.slug)
        existing = (await session.execute(stmt)).scalars().first()
        if existing is None:
            status = data.status_or_open
            row = SayuMacroEvent(
                slug=data.slug,
                title=data.title,
                category=data.category or DEFAULT_CATEGORY,
                status=status,
                severity=data.severity if data.severity is not None else DEFAULT_SEVERITY,
                direction=data.direction or DEFAULT_DIRECTION,
                affected_sectors=data.affected_sectors,
                summary=data.summary,
                result=data.result,
                stance=data.stance,
                source_urls=data.source_urls,
                check_interval_hours=data.check_interval_hours or DEFAULT_CHECK_INTERVAL_HOURS,
                created_by=data.created_by,
                created_at=now,
                updated_at=now,
                last_checked_at=now if touch_checked else None,
                settled_at=now if status == MACRO_STATUS_SETTLED else None,
            )
            session.add(row)
            await session.flush()
            return row

        # 没说的字段一律保留原值；说了的才覆盖
        if data.title:
            existing.title = data.title
        if data.category is not None:
            existing.category = data.category
        if data.severity is not None:
            existing.severity = data.severity
        if data.direction is not None:
            existing.direction = data.direction
        if data.affected_sectors:
            existing.affected_sectors = data.affected_sectors
        if data.summary:
            existing.summary = data.summary
        if data.result:
            existing.result = data.result
        if data.stance:
            existing.stance = data.stance
        if data.source_urls:
            existing.source_urls = data.source_urls
        if data.check_interval_hours is not None:
            existing.check_interval_hours = data.check_interval_hours
        existing.updated_at = now
        if touch_checked:
            existing.last_checked_at = now
        if data.status is not None:
            if data.status == MACRO_STATUS_SETTLED and existing.status != MACRO_STATUS_SETTLED:
                existing.settled_at = now
            if data.status == MACRO_STATUS_OPEN and existing.status == MACRO_STATUS_SETTLED:
                # 重开：事态反复（停火又开打）时允许，清掉定论时间
                existing.settled_at = None
            existing.status = data.status
        session.add(existing)
        await session.flush()
        return existing

    @classmethod
    @with_session
    async def delete_by_slug(cls, session: AsyncSession, slug: str) -> bool:
        stmt = select(SayuMacroEvent).where(col(SayuMacroEvent.slug) == slug)
        existing = (await session.execute(stmt)).scalars().first()
        if existing is None:
            return False
        await session.delete(existing)
        await session.flush()
        return True
