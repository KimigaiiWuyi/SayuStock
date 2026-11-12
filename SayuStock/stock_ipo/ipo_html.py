"""IPO 日历模板投影：用原有日历行保留日期、阶段和市场身份。"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from collections.abc import Sequence

from ..render.extras_ui import Tone, direction
from ..render.templates import render_template
from ..utils.render_data import IpoCalendarRow, IpoCalendarGroup, IpoCalendarRenderData
from ..utils.market.enums import IpoStage, IpoMarket

CSS_WIDTH = 1100
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MARKETS: dict[IpoMarket, str] = {IpoMarket.CN: "cn", IpoMarket.HK: "hk", IpoMarket.US: "us"}
_STAGES: dict[IpoStage, str] = {
    IpoStage.FILED: "已申报",
    IpoStage.APPLY: "申购",
    IpoStage.PENDING: "待上市",
    IpoStage.LISTED: "已上市",
}
_LEGEND = (
    ("apply", "申购"),
    ("ballot", "中签"),
    ("pay", "缴款"),
    ("grey", "暗盘"),
    ("pending", "待上市"),
    ("listed", "已上市（红涨绿跌）"),
    ("filed", "已申报"),
)


@dataclass(frozen=True, slots=True)
class _DateCell:
    date_text: str
    caption: str
    today: bool


@dataclass(frozen=True, slots=True)
class _Bar:
    phase: str
    x: str
    width: str


@dataclass(frozen=True, slots=True)
class _Timeline:
    bars: tuple[_Bar, ...]
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Row:
    display_name: str
    stage: str
    stage_text: str
    identity: str
    dates: str
    numbers: str
    first_day: str
    first_day_tone: Tone
    timeline: _Timeline


@dataclass(frozen=True, slots=True)
class _Group:
    market: str
    name: str
    rows: tuple[_Row, ...]
    failed: bool


def _md(value: date) -> str:
    return value.strftime("%m-%d")


def _in_window(actual: date, data: IpoCalendarRenderData) -> bool:
    return data.window_start <= actual <= data.window_end


def _add_bar(bars: list[_Bar], *, left: float, right: float, days: int, phase: str) -> None:
    if right < left:
        return
    bars.append(_Bar(phase, f"{left / days:.3%}", f"{(right - left + 1) / days:.3%}"))


def _timeline(row: IpoCalendarRow, data: IpoCalendarRenderData) -> _Timeline:
    bars: list[_Bar] = []
    before: list[str] = []
    after: list[str] = []
    days = data.span_days + 1

    def mark(phase: str, label: str, actual: date | None, pos: float | None) -> None:
        if actual is None or pos is None:
            return
        if _in_window(actual, data):
            _add_bar(bars, left=pos, right=pos, days=days, phase=phase)
        elif actual < data.window_start:
            before.append(f"{label}{_md(actual)}")
        else:
            after.append(f"{label}{_md(actual)}")

    if row.apply_start_pos is not None and row.listing_pos is not None:
        _add_bar(bars, left=row.apply_start_pos, right=row.listing_pos - 1, days=days, phase="bridge")
    apply_end = row.apply_end_date or row.apply_date
    if (
        apply_end is not None
        and _in_window(apply_end, data)
        and row.apply_start_pos is not None
        and row.apply_end_pos is not None
    ):
        _add_bar(bars, left=row.apply_start_pos, right=row.apply_end_pos - 1, days=days, phase="window")
    mark("apply", "申购至" if row.market == IpoMarket.HK else "申购", apply_end, row.apply_end_pos)
    same_ballot_and_pay = row.ballot_date is not None and row.ballot_date == row.pay_date
    mark("ballot", "中签缴款" if same_ballot_and_pay else "中签", row.ballot_date, row.ballot_pos)
    if not same_ballot_and_pay:
        mark("pay", "缴款", row.pay_date, row.pay_pos)
    mark("grey", "暗盘", row.grey_market_date, row.grey_pos)
    listing = "pending"
    if row.stage == IpoStage.LISTED:
        tone = direction(row.first_day_change)
        listing = f"listed-{tone}" if tone in ("up", "down") else "listed"
    mark(listing, "上市", row.listing_date, row.listing_pos)
    mark("filed", "申报", row.filed_date, row.filed_pos)
    return _Timeline(tuple(bars), tuple(before), tuple(after))


def _row_identity_line(row: IpoCalendarRow) -> str:
    if row.market == IpoMarket.US:
        return row.name if row.code else ""
    return " · ".join(item for item in (row.code, row.board) if item)


def _row_date_line(row: IpoCalendarRow) -> str:
    parts: list[str] = []
    if row.market == IpoMarket.US and row.board:
        parts.append(row.board)
    if row.stage == IpoStage.APPLY:
        if row.apply_date is not None:
            parts.append(f"{_md(row.apply_date)}申购")
        elif row.apply_end_date is not None:
            parts.append(f"申购至{_md(row.apply_end_date)}")
    if row.ballot_date is not None and row.ballot_date == row.pay_date:
        parts.append(f"中签缴款{_md(row.ballot_date)}")
    else:
        if row.ballot_date is not None:
            parts.append(f"中签公布{_md(row.ballot_date)}")
        if row.pay_date is not None:
            parts.append(f"缴款{_md(row.pay_date)}")
    if row.grey_market_date is not None:
        parts.append(f"暗盘{_md(row.grey_market_date)}")
    if row.listing_date is not None:
        parts.append(f"{_md(row.listing_date)}上市")
    elif row.stage == IpoStage.FILED and row.filed_date is not None:
        parts.append(f"{_md(row.filed_date)}申报")
    return " · ".join(parts)


def _row_numbers_line(row: IpoCalendarRow) -> str:
    unit = "美元" if row.currency == "USD" else ("港元" if row.currency == "HKD" else "元")
    parts: list[str] = []
    if row.issue_price_text:
        parts.append(f"招股价{row.issue_price_text}{unit}")
    elif row.issue_price is not None:
        parts.append(f"发行价{row.issue_price:g}{unit}")
    if row.oversubscription is not None:
        parts.append(f"超购{row.oversubscription:g}倍")
    if row.raise_yi is not None:
        parts.append(f"募资{row.raise_yi:.2f}亿{unit}")
    return " · ".join(parts)


def _row(row: IpoCalendarRow, data: IpoCalendarRenderData) -> _Row:
    first_day = row.first_day_change if row.stage == IpoStage.LISTED else None
    return _Row(
        display_name=row.display_name,
        stage=row.stage.value,
        stage_text=_STAGES[row.stage],
        identity=_row_identity_line(row),
        dates=_row_date_line(row),
        numbers=_row_numbers_line(row),
        first_day=f"首日{first_day:+.1f}%" if first_day is not None else "",
        first_day_tone=direction(first_day),
        timeline=_timeline(row, data),
    )


def _group(group: IpoCalendarGroup, data: IpoCalendarRenderData, failed: Sequence[str]) -> _Group:
    return _Group(
        _MARKETS[group.market], group.label, tuple(_row(row, data) for row in group.rows), group.label in failed
    )


def build_ipo_calendar_html(data: IpoCalendarRenderData, *, failed: Sequence[str] = ()) -> str:
    return render_template(
        "ipo_calendar.html",
        width=CSS_WIDTH,
        subtitle=f"{data.window_start:%Y-%m-%d} 至 {data.window_end:%Y-%m-%d} · 共 {data.total} 只",
        date_cells=tuple(
            _DateCell(_md(day), "今天" if day == data.anchor else _WEEKDAYS[day.weekday()], day == data.anchor)
            for day in data.days
        ),
        legend=_LEGEND,
        groups=tuple(_group(group, data, failed) for group in data.groups),
        footer="SayuStock · IPO 日历",
    )
