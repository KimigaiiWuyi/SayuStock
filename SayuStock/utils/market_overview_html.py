"""Presentation models for 大盘概览（还原原版布局，Jinja + Takumi 渲染）。"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from .market import BREADTH_DIRECTION, DisplayItem
from ..render.templates import render_template
from ..render.card_widgets import tone, _png_uri, mood_uri, row_spark, footer_uri, signed_pct

CSS_WIDTH = 1700
CANVAS_HEIGHT = 2885

_TEXTURE_DIR = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"

# 涨跌分布柱几何（div.png 内坐标系，画布 850×500）
_BREADTH_BAR_LEFT = 45
_BREADTH_BAR_WIDTH = 36
_BREADTH_BAR_RIGHT = 807

_BREADTH_DIRECTION_COLORS: dict[int, tuple[int, int, int]] = {
    1: (187, 26, 26),
    0: (150, 150, 150),
    -1: (23, 199, 30),
}
_BREADTH_BAR_COLORS: dict[str, tuple[int, int, int]] = {
    label: _BREADTH_DIRECTION_COLORS[direction] for label, direction in BREADTH_DIRECTION.items()
}


@dataclass(frozen=True, slots=True)
class BreadthColumn:
    name: str
    count: int
    height: int
    tone: str
    left: int = 0
    top: int = 413
    pixel_height: int = 0
    num_top: int = 385
    color: str = "rgb(150, 150, 150)"


@dataclass(frozen=True, slots=True)
class IndexTile:
    name: str
    pct: str
    price: str
    bg: str
    pct_color: str
    price_color: str
    spark: str


@dataclass(frozen=True, slots=True)
class BoardRank:
    name: str
    change: str
    tone: str
    member: str
    member_change: str
    top: int
    strip_bg: str
    lead_color: str


@dataclass(frozen=True, slots=True)
class BoardColumn:
    left: int
    rows: list[BoardRank]


def breadth_bar_left(index: int, total: int) -> int:
    """分布柱左缘 x：index 从 0 起（0 = 跌停侧），末柱右缘恒为 _BREADTH_BAR_RIGHT。"""
    if total < 2:
        return _BREADTH_BAR_LEFT
    span = _BREADTH_BAR_RIGHT - _BREADTH_BAR_WIDTH - _BREADTH_BAR_LEFT
    return round(_BREADTH_BAR_LEFT + index * span / (total - 1))


def card_gradient_bg(change_pct: float) -> str:
    max_diff = 4.0
    neutral = 26
    intensity = min(abs(change_pct), max_diff) / max_diff
    peak = int(neutral + intensity * (170 - neutral))
    if change_pct > 0:
        return f"rgba({peak}, {neutral}, {neutral}, 0.75)"
    if change_pct < 0:
        return f"rgba({neutral}, {peak}, {neutral}, 0.75)"
    return f"rgba({neutral}, {neutral}, {neutral}, 0.75)"


def breadth_columns(counts: dict[str, int]) -> list[BreadthColumn]:
    items = list(reversed(tuple(counts.items())))
    highest = max(counts.values(), default=0)
    total = len(items)
    columns: list[BreadthColumn] = []
    for index, (name, count) in enumerate(items):
        pct_height = max(1, round(100 * count / highest)) if highest > 0 and count > 0 else 0
        pixel_h = max(1, int(366 * count / highest)) if highest > 0 and count > 0 else 0
        top_y = 413 - pixel_h
        rgb = _BREADTH_BAR_COLORS[name]
        columns.append(
            BreadthColumn(
                name=name,
                count=count,
                height=pct_height,
                tone=tone(float(BREADTH_DIRECTION[name])),
                left=breadth_bar_left(index, total),
                top=top_y,
                pixel_height=pixel_h,
                num_top=top_y - 28,
                color=f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})",
            )
        )
    return columns


def _index_tiles(items: list[DisplayItem], sparks: dict[str, str]) -> list[IndexTile]:
    tiles: list[IndexTile] = []
    for item in items[:12]:
        diff = round(item.change_pct, 2)
        if diff > 0:
            pct_color, price_color = "#ff4d4f", "#ff7875"
        elif diff < 0:
            pct_color, price_color = "#00e676", "#24ce1e"
        else:
            pct_color, price_color = "#d1d5db", "#9ca3af"
        tiles.append(
            IndexTile(
                name=item.name,
                pct=f"+{diff:.2f}%" if diff >= 0 else f"{diff:.2f}%",
                price=f"{item.price}",
                bg=card_gradient_bg(diff),
                pct_color=pct_color,
                price_color=price_color,
                spark=row_spark(sparks, item.name, item.code),
            )
        )
    return tiles


def _ranks(items: list[DisplayItem]) -> list[BoardRank]:
    rows: list[BoardRank] = []
    total = 20
    for index, item in enumerate(items[:20]):
        declining = item.change_pct < 0
        alpha = round((total + 1 - index) / total, 3)
        if not declining:
            strip_bg = f"rgba(140, 18, 22, {alpha})"
            lead_color = "rgb(201, 26, 32)"
            member = item.lead_name or ""
            member_change = signed_pct(item.lead_change_pct) if item.lead_change_pct is not None else ""
        else:
            strip_bg = f"rgba(59, 140, 18, {alpha})"
            lead_color = "rgb(25, 199, 16)"
            member = item.fall_name or item.lead_name or ""
            raw_pct = item.fall_change_pct if item.fall_name else item.lead_change_pct
            member_change = signed_pct(raw_pct) if raw_pct is not None else ""
        rows.append(
            BoardRank(
                name=item.name,
                change=signed_pct(item.change_pct),
                tone=tone(item.change_pct),
                member=member,
                member_change=member_change,
                top=980 + 90 * index,
                strip_bg=strip_bg,
                lead_color=lead_color,
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
    stale_days = 0 if trade_date is None else (now.date() - trade_date.date()).days
    stale = stale_days > 0
    sz_diff = data_zs_items[0].change_pct if data_zs_items else 0.0
    time_color_bg = "rgba(186, 26, 27, 0.45)" if sz_diff >= 0 else "rgba(18, 199, 30, 0.45)"
    time_text_color = "#ba1a1b" if sz_diff >= 0 else "#12c71e"
    day_names = {1: "上日", 2: "前日", 3: "三日前"}
    days_label = day_names[stale_days] if stale_days in day_names else f"{stale_days}日前"
    vol_label = f"成交额({days_label}): {turnover}" if stale else f"成交额: {turnover}"
    diff_colors = {"up": "#ce221e", "down": "#24ce1e", "flat": "#a0a0a0"}
    diff_color = diff_colors[turnover_tone] if turnover_tone in diff_colors else "#a0a0a0"

    return render_template(
        "overview.html",
        width=CSS_WIDTH,
        height=CANVAS_HEIGHT,
        mood=mood_uri(title_num),
        bar1=_png_uri(_TEXTURE_DIR / "bar1.png"),
        bar2=_png_uri(_TEXTURE_DIR / "bar2.png"),
        bar3=_png_uri(_TEXTURE_DIR / "bar3.png"),
        bar4=_png_uri(_TEXTURE_DIR / "bar4.png"),
        div_bg=_png_uri(_TEXTURE_DIR / "div.png"),
        flow=em_flow_uri,
        weekday=now.strftime("星期" + "一二三四五六日"[now.weekday()]),
        time_main="休  市" if stale else now.strftime("%H:%M"),
        date_str=now.strftime("%Y.%m.%d"),
        time_box_bg="rgba(60, 60, 60, 0.85)" if stale else time_color_bg,
        weekday_color="#a0a0a0" if stale else "#ffffff",
        time_main_color="#ffc800" if stale else "#ffffff",
        date_color="#a0a0a0" if stale else "#ffffff",
        vol_label=vol_label,
        vol_color=time_text_color,
        turnover_delta=turnover_delta,
        diff_color=diff_color,
        index_cards=_index_tiles(data_zs_items, sparks),
        up_count=up_value,
        down_count=down_value,
        breadth=[bar for bar in breadth_columns(diff_bar) if bar.count > 0] if breadth_available else [],
        breadth_available=breadth_available,
        board_columns=[
            BoardColumn(0, _ranks(data_hy_fall)),
            BoardColumn(415, _ranks(data_hy_rise)),
            BoardColumn(850, _ranks(data_gn_fall)),
            BoardColumn(1265, _ranks(data_gn_rise)),
        ],
        source=source_line,
        brand=footer_uri(),
    )
