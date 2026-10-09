"""绘图/列表用语义展示项（业务禁止再读 f*）。"""

from __future__ import annotations

from typing import TypeVar, cast
from dataclasses import replace, dataclass

from .models import (
    Quote,
    BoardRow,
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

# 数据源 id → 展示名（图表左下角「数据来源」标签用）
PROVIDER_DISPLAY: dict[str, str] = {
    "eastmoney": "东方财富",
    "tencent": "腾讯财经",
    "sina": "新浪财经",
    "ths": "同花顺",
    "tiantian": "天天基金",
    "okx": "OKX",
    "vix": "OPTBBS",
}

# 可盖章（写入 provider 字段）的结果模型
_STAMPABLE = (
    Quote,
    KlineSeries,
    IntradaySeries,
    BoardSnapshot,
    RankSnapshot,
    ValueSeries,
    MarketTurnover,
    BreadthBar,
    NorthboundFlow,
    FinancialSnapshot,
)

_T = TypeVar("_T")


def stamp_provider(result: _T, pid: str) -> _T:
    """把命中数据源 id 写进结果模型（已有值不覆盖），供渲染层展示真实来源。

    分时的来源在序列上。嵌套 quote 单独再盖一次，否则只读 quote 的图会丢来源。
    """
    if isinstance(result, IntradaySeries):
        quote = result.quote
        stamped_quote = quote
        if quote is not None and quote.provider is None:
            stamped_quote = replace(quote, provider=pid)
        series_pid = result.provider if result.provider is not None else pid
        if stamped_quote is not quote or series_pid != result.provider:
            return cast("_T", replace(result, provider=series_pid, quote=stamped_quote))
        return result
    if isinstance(result, _STAMPABLE) and result.provider is None:
        return cast("_T", replace(result, provider=pid))
    return result


def source_label(*providers: str | None) -> str:
    """数据源 id 序列 → 展示标签；多源去重拼接；未知 id 原样展示。

    无任何来源信息时回退「东方财富」（历史口径）。
    """
    labels: list[str] = []
    for pid in providers:
        if not pid:
            continue
        label = PROVIDER_DISPLAY.get(pid, pid)
        if label not in labels:
            labels.append(label)
    if not labels:
        return PROVIDER_DISPLAY["eastmoney"]
    return "、".join(labels)


def source_footer(*providers: str | None, note: str = "") -> str:
    """图角一行。note 是图种，例如「技术分析」。

    把本次用到的每个结果的 provider（sourceBy）都传进来，多源去重。
    """
    extra = f" {note}" if note else ""
    return f"数据来源：{source_label(*providers)} | SayuStock{extra}"


@dataclass(frozen=True, slots=True)
class DisplayItem:
    """大盘块/列表行通用展示。"""

    name: str
    price: float
    change_pct: float
    amount: float | None = None
    industry: str | None = None
    lead_name: str | None = None
    lead_change_pct: float | None = None
    fall_name: str | None = None
    fall_change_pct: float | None = None
    code: str = ""
    # sourceBy：这一格行情来自哪个源。
    provider: str | None = None


def from_board_row(row: BoardRow) -> DisplayItem:
    return DisplayItem(
        name=row.name,
        price=float(row.price) if row.price is not None else 0.0,
        change_pct=float(row.change_pct) if row.change_pct is not None else 0.0,
        amount=row.amount,
        industry=row.industry,
        lead_name=row.lead_name,
        lead_change_pct=row.lead_change_pct,
        fall_name=row.fall_name,
        fall_change_pct=row.fall_change_pct,
        code=row.code,
    )


def from_quote(q: Quote) -> DisplayItem:
    return DisplayItem(
        name=q.symbol.name.split(" (")[0] if " (" in q.symbol.name else q.symbol.name,
        price=float(q.price),
        change_pct=float(q.change_pct) if q.change_pct is not None else 0.0,
        amount=q.amount,
        industry=q.industry,
        code=q.symbol.code,
        provider=q.provider,
    )


def board_rows_to_items(rows: tuple[BoardRow, ...] | list[BoardRow]) -> list[DisplayItem]:
    return [from_board_row(r) for r in rows]


def pick_display_items(
    items: list[DisplayItem] | dict[str, DisplayItem],
    keys: dict[str, str] | list[str],
) -> list[DisplayItem]:
    """按配置键顺序挑选格子；每条数据只用一次，避免别名键重复贴同一标的。"""
    key_list = list(keys.keys()) if isinstance(keys, dict) else list(keys)
    pool = list(items.values()) if isinstance(items, dict) else list(items)
    picked: list[DisplayItem] = []
    used: set[int] = set()
    for key in key_list:
        for i, item in enumerate(pool):
            if i in used:
                continue
            name = item.name
            if name != key and key not in name and name not in key:
                continue
            picked.append(item)
            used.add(i)
            break
    return picked
