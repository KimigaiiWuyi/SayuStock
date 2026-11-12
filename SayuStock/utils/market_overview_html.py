"""Presentation models for 大盘概览."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from .market import BREADTH_DIRECTION, DisplayItem
from ..render.templates import render_template
from ..render.card_widgets import Card, tone, mood_uri, row_spark, footer_uri, price_text, signed_pct

CSS_WIDTH = 1200


@dataclass(frozen=True, slots=True)
class BreadthColumn:
    name: str
    count: int
    height: int
    tone: str


@dataclass(frozen=True, slots=True)
class BoardRank:
    name: str
    change: str
    tone: str
    member: str
    member_change: str


def breadth_columns(counts: dict[str, int]) -> list[BreadthColumn]:
    highest = max(counts.values(), default=0)
    return [
        BreadthColumn(
            name,
            count,
            max(1, round(100 * count / highest)) if highest > 0 and count > 0 else 0,
            tone(float(BREADTH_DIRECTION[name])),
        )
        for name, count in reversed(tuple(counts.items()))
    ]


def _ranks(items: list[DisplayItem]) -> list[BoardRank]:
    rows: list[BoardRank] = []
    for item in items[:20]:
        declining = item.change_pct < 0
        member = (item.fall_name or item.lead_name) if declining else item.lead_name
        member_change = item.fall_change_pct if declining and item.fall_name else item.lead_change_pct
        rows.append(
            BoardRank(
                item.name,
                signed_pct(item.change_pct),
                tone(item.change_pct),
                member or "",
                signed_pct(member_change) if member_change is not None else "",
            )
        )
    return rows


def build_market_overview_html(
    *,
    data_zs_items: list[DisplayItem],
    sparks: dict[str, str],
    diff_bar: dict[str, int],
    up_value: int,
    down_value: int,
    breadth_available: bool,
    em_flow_uri: str,
    now: datetime,
    trade_date: datetime | None,
    turnover: str,
    turnover_delta: str,
    turnover_tone: str,
    data_hy_fall: list[DisplayItem],
    data_hy_rise: list[DisplayItem],
    data_gn_fall: list[DisplayItem],
    data_gn_rise: list[DisplayItem],
    source_line: str,
    title_num: str,
) -> str:
    stale = trade_date is not None and trade_date.date() < now.date()
    return render_template(
        "overview.html",
        width=CSS_WIDTH,
        mood=mood_uri(title_num),
        date=now.strftime("%Y-%m-%d"),
        status="休市" if stale else now.strftime("%H:%M"),
        trade_date=trade_date.strftime("%Y-%m-%d") if stale and trade_date is not None else "",
        turnover=turnover,
        turnover_delta=turnover_delta,
        turnover_tone=turnover_tone,
        flow=em_flow_uri,
        up_count=up_value,
        down_count=down_value,
        flat_count=diff_bar["平"] if "平" in diff_bar else 0,
        breadth=breadth_columns(diff_bar) if breadth_available else [],
        index_cards=[
            Card(
                item.name,
                price_text(item.price),
                signed_pct(item.change_pct),
                tone(item.change_pct),
                row_spark(sparks, item.name, item.code),
            )
            for item in data_zs_items
        ],
        boards=[
            ("行业领跌", _ranks(data_hy_fall)),
            ("行业领涨", _ranks(data_hy_rise)),
            ("概念领跌", _ranks(data_gn_fall)),
            ("概念领涨", _ranks(data_gn_rise)),
        ],
        source=source_line,
        brand=footer_uri(),
    )
