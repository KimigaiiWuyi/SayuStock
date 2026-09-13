"""宏观事件 ai_tools（3 个）。

- ``macro_event_list``        只读：进行中 / 全部事件 + 每条是否 ``stale``（该复核了）
- ``macro_event_refresh_due`` 只读：只列「到期该联网复核」的 open 事件 + 建议检索词
- ``macro_event_upsert``      写：按 slug 幂等登记 / 更新进展 / 盖棺定论

写工具不做账户鉴权：事件表是全局共享的，不涉及资金；但所有字段经 ``repo`` 归一，
模型传什么进来都不会写出脏枚举。``created_by`` 记执行体画像，方便追溯是谁登记的。
"""

from __future__ import annotations

import json
from typing import TypedDict
from datetime import datetime

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.ai_core.planning.runtime import PlanRunContext, get_plan_context

from .repo import MacroEventRepo, MacroEventInput, is_due_for_check
from ..utils.database.macro_models import (
    MACRO_STATUSES,
    MACRO_CATEGORIES,
    MACRO_DIRECTIONS,
    MACRO_STATUS_OPEN,
    MACRO_SEVERE_LEVEL,
    MACRO_STATUS_SETTLED,
    SayuMacroEvent,
)

__all__ = [
    "macro_event_list",
    "macro_event_refresh_due",
    "macro_event_upsert",
    "severe_risk_off_titles",
    "format_macro_events_text",
]

MACRO_CAPABILITY_DOMAIN: str = "宏观事件"

# 金融群画像下自动装配（与 papertrade 只读账本工具同一套标签）
_MACRO_CTX_TAGS: list[str] = ["Stock", "Finance", "股票", "金融", "投资", "宏观", "模拟盘"]


class _EventView(TypedDict):
    slug: str
    title: str
    category: str
    status: str
    severity: int
    direction: str
    affected_sectors: str
    summary: str
    result: str
    stance: str
    stale: bool
    last_checked_at: str | None
    updated_at: str | None
    settled_at: str | None


class _DueView(TypedDict):
    slug: str
    title: str
    severity: int
    direction: str
    last_checked_at: str | None
    suggested_queries: list[str]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value is not None else None


def _to_view(row: SayuMacroEvent, now: datetime) -> _EventView:
    return {
        "slug": row.slug,
        "title": row.title,
        "category": row.category,
        "status": row.status,
        "severity": row.severity,
        "direction": row.direction,
        "affected_sectors": row.affected_sectors,
        "summary": row.summary,
        "result": row.result,
        "stance": row.stance,
        "stale": is_due_for_check(row, now),
        "last_checked_at": _iso(row.last_checked_at),
        "updated_at": _iso(row.updated_at),
        "settled_at": _iso(row.settled_at),
    }


def _current_profile() -> str:
    plan_ctx: PlanRunContext | None = get_plan_context()
    return plan_ctx.agent_profile if plan_ctx is not None else ""


def _suggested_queries(row: SayuMacroEvent, now: datetime) -> list[str]:
    """给低能力模型现成的检索词：标题 + 最新进展 + 时间窗，不让它自己拼。"""
    ym = now.strftime("%Y年%m月")
    return [
        f"{row.title} 最新进展 {ym}",
        f"{row.title} 对A股影响 {ym}",
    ]


def severe_risk_off_titles(rows: list[SayuMacroEvent]) -> str:
    """硬闸提示用：高危 risk_off 事件标题拼成一行。"""
    return "、".join(f"{r.title}(sev{r.severity})" for r in rows)


def format_macro_events_text(rows: list[SayuMacroEvent], now: datetime | None = None) -> str:
    """用户命令 / 播报用的纯文本视图。"""
    moment = now or datetime.now()
    if not rows:
        return "宏观事件表为空。AI 在心跳里发现关税 / 地缘 / 央行 / 油价类大事后会自动登记。"
    lines: list[str] = ["【宏观重大事件】"]
    for r in rows:
        flag = "🟠进行中" if r.status == MACRO_STATUS_OPEN else "⚪已定论"
        stale = "（待复核）" if is_due_for_check(r, moment) else ""
        checked = _iso(r.last_checked_at) or "未复核"
        lines.append(f"{flag} [{r.direction}/sev{r.severity}] {r.title}{stale}")
        if r.result:
            lines.append(f"  进展：{r.result[:160]}")
        if r.stance:
            lines.append(f"  含义：{r.stance[:120]}")
        lines.append(f"  最近复核：{checked}")
    return "\n".join(lines)


@ai_tools(
    covers=[
        "查询正在进行或已定论的宏观重大事件（关税战/地缘冲突/央行决议/油价冲击）及其最新进展",
        "模拟盘决策与持仓分析前必看的宏观事件表",
    ],
    category="common",
    capability_domain=MACRO_CAPABILITY_DOMAIN,
    context_tags=_MACRO_CTX_TAGS,
)
async def macro_event_list(
    ctx: RunContext[ToolContext],
    status: str = "open",
    limit: int = 20,
) -> str:
    """列出宏观重大事件表（全局共享，不分模拟盘）。

    决策 / 持仓分析 / 研究任务**每轮开头必调一次**，用它回答「现在有什么大事在进行」。
    返回 JSON 列表，按严重度降序；每条字段：
      slug / title / category / status(open=进行中, settled=盖棺定论) / severity(1~5) /
      direction(risk_off=利空风险偏好, risk_on=利好, mixed, neutral) / affected_sectors /
      summary(事件是什么) / result(最新进展或最终结论) / stance(对操作的含义) /
      stale(true=距上次联网复核已超间隔，本轮可顺手 web_search 一次并 upsert 更新)。

    Args:
        status: "open" 只看进行中（默认）；"settled" 只看已定论；"all" 全部。
        limit: 最多返回条数，默认 20。
    """
    key = (status or "open").strip().lower()
    wanted: str | None
    if key == "all":
        wanted = None
    elif key in MACRO_STATUSES:
        wanted = key
    else:
        wanted = MACRO_STATUS_OPEN
    rows = await MacroEventRepo.list_by_status(wanted, limit=max(1, min(limit, 100)))
    now = datetime.now()
    views: list[_EventView] = [_to_view(r, now) for r in rows]
    return json.dumps(views, ensure_ascii=False)


@ai_tools(
    covers=["列出到期需要联网复核的宏观事件并给出检索词"],
    category="common",
    capability_domain=MACRO_CAPABILITY_DOMAIN,
)
async def macro_event_refresh_due(
    ctx: RunContext[ToolContext],
    max_events: int = 5,
) -> str:
    """只列「进行中且到了复核时间」的宏观事件，附带现成的 web_search 检索词。

    宏观心跳代理的第一步。返回 JSON：
      {"due": [{slug, title, severity, direction, last_checked_at, suggested_queries}], "count": N}
    ``count`` 为 0 时表示本轮没有事件需要联网复核，直接进入「扫描新事件」步骤即可。
    已经 settled（盖棺定论）的事件永远不会出现在这里——不要再去查它们。

    Args:
        max_events: 本轮最多复核几件，默认 5（先复核严重度高的）。
    """
    rows = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=100)
    now = datetime.now()
    due: list[_DueView] = []
    for r in rows:
        if not is_due_for_check(r, now):
            continue
        due.append(
            {
                "slug": r.slug,
                "title": r.title,
                "severity": r.severity,
                "direction": r.direction,
                "last_checked_at": _iso(r.last_checked_at),
                "suggested_queries": _suggested_queries(r, now),
            }
        )
        if len(due) >= max(1, min(max_events, 20)):
            break
    return json.dumps({"due": due, "count": len(due)}, ensure_ascii=False)


@ai_tools(
    covers=["登记或更新一条宏观重大事件的最新进展、严重度、方向与是否盖棺定论"],
    category="common",
    capability_domain=MACRO_CAPABILITY_DOMAIN,
)
async def macro_event_upsert(
    ctx: RunContext[ToolContext],
    slug: str,
    title: str,
    result: str,
    status: str = "",
    category: str = "",
    severity: int = 0,
    direction: str = "",
    affected_sectors: str = "",
    summary: str = "",
    stance: str = "",
    source_urls: str = "",
    check_interval_hours: int = 0,
    checked_now: bool = True,
) -> str:
    """登记 / 更新一条宏观重大事件（按 slug 幂等：同一 slug 反复写只会更新那一条）。

    什么算「重大事件」：关税 / 贸易战、战争与航道封锁、央行决议与流动性拐点、油价急变、
    重大国内政策、PPI/CPI 等数据拐点。个股公告、板块日内涨跌**不算**，不要往这里写。

    **只改进展时只传 slug + title + result 即可**：status / category / severity / direction
    留空（或 0）表示「不改」，已有事件保留原值；新建事件时留空落默认
    （open / other / 3 / mixed）。

    Args:
        slug: 英文小写 + 下划线的唯一键，例 "us_china_tariff_2026"、"red_sea_shipping_2026"。
              中文会被丢弃导致为空而被拒绝；已存在的事件请沿用 macro_event_list 里的 slug。
        title: 中文标题，例 "中美关税战（2026）"。
        result: 最新进展 / 最终结论，3~5 句，带日期。每次联网复核后**必须**改写这里。
        status: "open"=进行中（还会继续联网复核）；"settled"=盖棺定论（此后不再查）；
                留空=不改。判定 settled 的标准：正式协议已签 / 停火已生效并执行 /
                决议已落地且市场不再为它定价。只是「暂时平静」不算 settled。
        category: trade / geopolitics / monetary / commodity / policy / macro_data / other；留空=不改。
        severity: 1~5，0=不改。5=全球性冲击（大国全面关税战、主要航道封锁）；4=影响多数板块；
                  3=影响若干板块；2=局部；1=仅需知道。≥4 且 direction=risk_off 会让
                  多因子盘的 buy 不能标「进攻」档。
        direction: 对 A 股风险偏好：risk_off（利空）/ risk_on（利好）/ mixed / neutral；留空=不改。
        affected_sectors: 受影响板块，逗号分隔，例 "出口链,消费电子,农业"。
        summary: 事件是什么（首次登记写，之后可留空不改）。
        stance: 对操作的一句话含义，例 "已互加到无意义税率，政策顶=情绪底，可试探反向"。
        source_urls: 本次复核依据的 URL，换行分隔。
        check_interval_hours: 多久复核一次（1~168 小时），0=不改（新建默认 6）。
        checked_now: 本次写入是否基于刚做过的联网检索（True 会刷新 last_checked_at；
                     只是改标题 / 补备注请传 False）。
    """
    data = MacroEventInput.from_raw(
        slug=slug,
        title=title,
        category=category,
        status=status,
        severity=severity if severity > 0 else None,
        direction=direction,
        affected_sectors=affected_sectors,
        summary=summary,
        result=result,
        stance=stance,
        source_urls=source_urls,
        check_interval_hours=check_interval_hours,
        created_by=_current_profile() or "persona",
    )
    if not data.slug:
        return (
            "⚠️ slug 不能为空：请用英文小写字母 / 数字 / 下划线，例如 us_china_tariff_2026；"
            "中文会被整段丢弃。已拒绝写入。"
        )
    if not data.title:
        return "⚠️ title 不能为空，已拒绝写入。"
    if not data.result and data.status == MACRO_STATUS_SETTLED:
        return "⚠️ 盖棺定论必须写 result（最终结论），已拒绝写入。"
    cat_key = category.strip().lower()
    if cat_key and cat_key not in MACRO_CATEGORIES and data.category == "other":
        note_cat = f"（category「{category}」不在枚举内，已归为 other）"
    else:
        note_cat = ""
    dir_key = direction.strip().lower()
    if dir_key and dir_key not in MACRO_DIRECTIONS and data.direction == "mixed":
        note_dir = f"（direction「{direction}」不在枚举内，已归为 mixed）"
    else:
        note_dir = ""

    row = await MacroEventRepo.upsert(data, touch_checked=checked_now)
    severe = row.status == MACRO_STATUS_OPEN and row.direction == "risk_off" and row.severity >= MACRO_SEVERE_LEVEL
    tail = "；该事件为高危 risk_off，多因子盘 buy 不得标进攻档" if severe else ""
    return (
        f"ok slug={row.slug} status={row.status} severity={row.severity} direction={row.direction}"
        f"{note_cat}{note_dir}{tail}"
    )
