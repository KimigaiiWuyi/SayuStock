"""IPO 日历 HTML（pytakumi / render_html_to_bytes），视觉对齐全天候时间轴。"""

from __future__ import annotations

import html as html_lib
from base64 import b64encode
from pathlib import Path
from datetime import date
from functools import lru_cache

from ..utils.render_data import IpoCalendarRow, IpoCalendarRenderData
from ..utils.market.enums import IpoStage, IpoMarket

CSS_WIDTH = 1000
_HEAD_H = 150
_AXIS_H = 58
_SEC_HEAD_H = 46
_ROW_H = 104
_EMPTY_H = 44
_FOOT_H = 40
_FOOT_GAP = 16

_FOOTER_PATH = Path(__file__).resolve().parent.parent / "utils" / "texture2d" / "footer.png"

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 阶段色：申购蓝 / 中签紫 / 缴款橙 / 暗盘青 / 待上市琥珀 / 已申报灰；
# 已上市按首日表现红涨绿跌
_STAGE_APPLY = "#38bdf8"
_STAGE_BALLOT = "#a78bfa"
_STAGE_PAY = "#fb923c"
_STAGE_GREY_MKT = "#2dd4bf"
_STAGE_PENDING = "#fbbf24"
_STAGE_FILED = "#94a3b8"
_STAGE_LISTED_FLAT = "#34d399"
_UP = "#ef4444"
_DOWN = "#24ce1e"
_APPLY_WINDOW_BG = "rgba(56,189,248,0.30)"
_BRIDGE_BG = "rgba(56,189,248,0.14)"
_TODAY = "#fde68a"


def _e(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


@lru_cache(maxsize=1)
def _footer_uri() -> str:
    raw = _FOOTER_PATH.read_bytes()
    return f"data:image/png;base64,{b64encode(raw).decode('ascii')}"


def _md(value: date | None) -> str:
    return f"{value.month:02d}-{value.day:02d}" if value is not None else "--"


def _pct(pos: float, span: int) -> float:
    """窗口内天数索引 → 轨道百分比（span+1 个日格）。"""
    return pos / (span + 1) * 100.0


def ipo_calendar_canvas_size(data: IpoCalendarRenderData) -> tuple[int, int]:
    height = _HEAD_H + _AXIS_H
    for group in data.groups:
        rows_h = _ROW_H * len(group.rows) if group.rows else _EMPTY_H
        height += _SEC_HEAD_H + rows_h + 10
    height += _FOOT_GAP + _FOOT_H + _FOOT_GAP
    return CSS_WIDTH, height


def _in_window(actual: date | None, data: IpoCalendarRenderData) -> bool:
    return actual is not None and data.window_start <= actual <= data.window_end


def _bar_html(left_pos: float | None, right_pos: float | None, span: int, cls: str, style: str) -> str:
    """[left, right+1) 的一条横条；None 或零宽不画。"""
    if left_pos is None or right_pos is None or right_pos < left_pos:
        return ""
    left = _pct(left_pos, span)
    width = max(0.6, _pct(right_pos + 1, span) - left)
    return f'<div class="{cls}" style="left:{left:.2f}%;width:{width:.2f}%;{style}"></div>'


def _tick_html(
    pos: float | None,
    actual: date | None,
    data: IpoCalendarRenderData,
    color: str,
    *,
    heavy: bool = False,
) -> str:
    """一天宽的阶段横条；日期越出窗口时改为贴边箭头 + 日期标签。"""
    if pos is None or actual is None:
        return ""
    if not _in_window(actual, data):
        if actual < data.window_start:
            return (
                f'<div class="edge-note left" style="color:{color}">{_md(actual)}</div>'
                f'<div class="chev chev-l" style="border-right-color:{color}"></div>'
            )
        return (
            f'<div class="edge-note right" style="color:{color}">{_md(actual)}</div>'
            f'<div class="chev chev-r" style="border-left-color:{color}"></div>'
        )
    left = _pct(pos, data.span_days)
    width = 100.0 / (data.span_days + 1)
    cls = "tick heavy" if heavy else "tick"
    return f'<div class="{cls}" style="left:{left:.2f}%;width:{width:.2f}%;background:{color}"></div>'


def _listing_color(row: IpoCalendarRow) -> str:
    if row.stage == IpoStage.LISTED:
        if row.first_day_change is None:
            return _STAGE_LISTED_FLAT
        return _UP if row.first_day_change >= 0 else _DOWN
    return _STAGE_PENDING


def _apply_html(row: IpoCalendarRow, data: IpoCalendarRenderData) -> str:
    """申购窗口：浅色多日段 + 截止日/申购日实心条。

    A股单日申购 → 一天实心蓝；港股招股期 → 截止日前 3 天浅色段 + 截止日实心。
    截止/申购日越出窗口时不画条（位置会被夹到边缘误导读图），改由
    ``_edge_summary_html`` 在左缘给彩色摘要。
    """
    if row.apply_start_pos is None or row.apply_end_pos is None:
        return ""
    apply_end_actual = row.apply_end_date or row.apply_date
    if apply_end_actual is None or not _in_window(apply_end_actual, data):
        return ""
    window_bar = _bar_html(
        row.apply_start_pos, row.apply_end_pos - 1, data.span_days, "apply-window", f"background:{_APPLY_WINDOW_BG};"
    )
    deadline = _bar_html(
        row.apply_end_pos, row.apply_end_pos, data.span_days, "tick heavy", f"background:{_STAGE_APPLY};"
    )
    return window_bar + deadline


def _edge_summary_html(row: IpoCalendarRow, data: IpoCalendarRenderData) -> str:
    """发生在窗口开始之前的申购/中签/缴款/暗盘 → 左缘紧凑彩色摘要 + 箭头。"""
    segs: list[str] = []

    def _add(label: str, value: date | None, color: str) -> None:
        if value is not None and value < data.window_start:
            segs.append(f'<span style="color:{color}">{label}{_md(value)}</span>')

    if row.market == IpoMarket.HK:
        _add("申购至", row.apply_end_date, _STAGE_APPLY)
    else:
        _add("申购", row.apply_date, _STAGE_APPLY)
    if row.ballot_date is not None and row.ballot_date == row.pay_date:
        _add("中签缴款", row.ballot_date, _STAGE_BALLOT)
    else:
        _add("中签", row.ballot_date, _STAGE_BALLOT)
        _add("缴款", row.pay_date, _STAGE_PAY)
    _add("暗盘", row.grey_market_date, _STAGE_GREY_MKT)
    if not segs:
        return ""
    return (
        f'<div class="edge-note left">{"".join(segs)}</div>'
        '<div class="chev chev-l" style="border-right-color:#7f8ea8"></div>'
    )


def _milestone_html(row: IpoCalendarRow, data: IpoCalendarRenderData) -> str:
    """中签公布（紫）/ 缴款（橙）/ 暗盘（青）一天小条；窗口外不画。"""
    out: list[str] = []
    ballot = (
        _bar_html(row.ballot_pos, row.ballot_pos, data.span_days, "tick mid", f"background:{_STAGE_BALLOT};")
        if _in_window(row.ballot_date, data)
        else ""
    )
    # 中签与缴款同日只画一条（避免重叠）
    if row.pay_date is not None and row.pay_date != row.ballot_date:
        pay = (
            _bar_html(row.pay_pos, row.pay_pos, data.span_days, "tick mid", f"background:{_STAGE_PAY};")
            if _in_window(row.pay_date, data)
            else ""
        )
    else:
        pay = ""
    grey = (
        _bar_html(row.grey_pos, row.grey_pos, data.span_days, "tick mid", f"background:{_STAGE_GREY_MKT};")
        if _in_window(row.grey_market_date, data)
        else ""
    )
    out.extend(x for x in (ballot, pay, grey) if x)
    return "".join(out)


def _bridge_html(row: IpoCalendarRow, data: IpoCalendarRenderData) -> str:
    """A股申购 → 上市 的浅色桥段。"""
    if row.apply_start_pos is None or row.listing_pos is None:
        return ""
    return _bar_html(row.apply_start_pos, row.listing_pos - 1, data.span_days, "bridge", "")


def _price_text(row: IpoCalendarRow) -> str:
    unit = "美元" if row.currency == "USD" else ("港元" if row.currency == "HKD" else "元")
    if row.issue_price_text:
        return f"招股价{row.issue_price_text}{unit}"
    if row.issue_price is not None:
        return f"发行价{row.issue_price:g}{unit}"
    return ""


def _row_identity_line(row: IpoCalendarRow) -> str:
    """副标题第 1 行：身份（美股=公司全名独占一行，交易所挪去日期行；其余=代码）。"""
    if row.market == IpoMarket.US:
        # 主名是 ticker 时这里放公司全名；无代码时主名已是全名，本行留空避免重复
        return _e(row.name) if row.code else ""
    parts: list[str] = [_e(row.code)]
    if row.board:
        parts.append(_e(row.board))
    return " · ".join(parts)


def _row_date_line(row: IpoCalendarRow) -> str:
    """副标题第 2 行：申购/中签/缴款/暗盘/上市 日期里程碑（美股前缀交易所）。"""
    parts: list[str] = []
    if row.market == IpoMarket.US and row.board:
        parts.append(_e(row.board))
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
    """副标题第 3 行：发行价/首日表现/超购/募资 等数字信息。"""
    parts: list[str] = []
    price = _price_text(row)
    if price:
        parts.append(_e(price))
    if row.stage == IpoStage.LISTED and row.first_day_change is not None:
        color = _UP if row.first_day_change >= 0 else _DOWN
        parts.append(f'<span style="color:{color};font-weight:630">首日{row.first_day_change:+.1f}%</span>')
    if row.oversubscription is not None:
        parts.append(f"超购{row.oversubscription:g}倍")
    if row.raise_yi is not None:
        unit = "亿美元" if row.currency == "USD" else "亿元"
        parts.append(f"募资{row.raise_yi:.2f}{unit}")
    return " · ".join(parts)


def _row_html(row: IpoCalendarRow, data: IpoCalendarRenderData, market_label: str, market_color: str) -> str:
    track = (
        _grid_html(data)
        + _bridge_html(row, data)
        + _apply_html(row, data)
        + _milestone_html(row, data)
        + _edge_summary_html(row, data)
        + _tick_html(row.listing_pos, row.listing_date, data, _listing_color(row), heavy=True)
        + _tick_html(row.filed_pos, row.filed_date, data, _STAGE_FILED)
    )
    return (
        '<div class="row">'
        '<div class="info">'
        '<div class="l1">'
        f'<span class="chip" style="background:{market_color}">{_e(market_label)}</span>'
        f'<span class="nm" title="{_e(row.name)}">{_e(row.display_name)}</span>'
        "</div>"
        f'<div class="l2">{_row_identity_line(row)}</div>'
        f'<div class="l3">{_row_date_line(row)}</div>'
        f'<div class="l4">{_row_numbers_line(row)}</div>'
        "</div>"
        f'<div class="track">{track}</div>'
        "</div>"
    )


def _grid_html(data: IpoCalendarRenderData) -> str:
    """10 条日格竖线 + 今天竖线（today 线在最后，压在横条上方）。"""
    lines = [
        f'<div class="vline" style="left:{_pct(float(i), data.span_days):.2f}%"></div>'
        for i in range(data.span_days + 1)
    ]
    today_left = _pct(float((data.anchor - data.window_start).days), data.span_days)
    lines.append(f'<div class="today-line" style="left:{today_left:.2f}%"></div>')
    return "".join(lines)


def _axis_html(data: IpoCalendarRenderData) -> str:
    cells: list[str] = []
    for i, day in enumerate(data.days):
        left = (i + 0.5) / (data.span_days + 1) * 100
        if day == data.anchor:
            cells.append(
                f'<div class="day today" style="left:{left:.2f}%"><b>{day.month:02d}-{day.day:02d}</b><i>今天</i></div>'
            )
        else:
            cells.append(
                f'<div class="day" style="left:{left:.2f}%">'
                f"<b>{day.month:02d}-{day.day:02d}</b><i>{_WEEKDAYS[day.weekday()]}</i></div>"
            )
    return f'<div class="axis"><div class="axis-space"></div><div class="axis-days">{"".join(cells)}</div></div>'


def _section_html(group, data: IpoCalendarRenderData) -> str:
    if group.rows:
        rows = "".join(_row_html(r, data, group.label, group.color) for r in group.rows)
    else:
        rows = (
            '<div class="row empty"><div class="info"><div class="l2">窗口内无发行/上市安排</div></div>'
            '<div class="track"></div></div>'
        )
    return (
        '<div class="sec">'
        '<div class="sec-head">'
        f'<span class="dot" style="background:{group.color}"></span>'
        f'<span class="sec-name">{_e(group.label)}</span>'
        f'<span class="sec-count">{len(group.rows)} 只</span>'
        '<div class="sec-line"></div>'
        "</div>"
        f'<div class="rows">{rows}</div>'
        "</div>"
    )


def build_ipo_calendar_html(data: IpoCalendarRenderData) -> str:
    """标题 + 日期轴 + 分市场甘特行，全部 HTML；高度随行数变。"""
    _, height = ipo_calendar_canvas_size(data)
    axis = _axis_html(data)
    sections = "".join(_section_html(g, data) for g in data.groups)
    legend = (
        '<div class="legend">'
        f'<span><i style="background:{_STAGE_APPLY}"></i>申购</span>'
        f'<span><i class="half" style="background:{_STAGE_BALLOT}"></i>中签</span>'
        f'<span><i class="half" style="background:{_STAGE_PAY}"></i>缴款</span>'
        f'<span><i class="half" style="background:{_STAGE_GREY_MKT}"></i>暗盘</span>'
        f'<span><i style="background:{_STAGE_PENDING}"></i>待上市</span>'
        f'<span><i style="background:{_STAGE_LISTED_FLAT}"></i>已上市</span>'
        f'<span><i style="background:{_STAGE_FILED}"></i>已申报</span>'
        '<span class="edge-key"><em></em>箭头=窗口外日期</span>'
        f'<span class="today-key"><b></b>今天</span>'
        "</div>"
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  width: {CSS_WIDTH}px;
  height: {height}px;
  background: #07091b;
  font-family: "MiSans", "Twemoji Mozilla", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: #e9eef7;
}}
.page {{
  width: {CSS_WIDTH}px;
  height: {height}px;
  background: #07091b;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0 40px;
}}
.head {{ flex: none; height: {_HEAD_H}px; padding-top: 34px; }}
.title {{ font-size: 40px; font-weight: 800; letter-spacing: 4px; }}
.subtitle {{ margin-top: 10px; font-size: 16px; color: #8b95a8; }}
.legend {{ margin-top: 12px; display: flex; gap: 16px; font-size: 12.5px; color: #9aa7bd; }}
.legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
.legend i {{ width: 18px; height: 9px; border-radius: 4px; display: inline-block; }}
.legend i.half {{ width: 8px; height: 13px; border-radius: 3px; }}
.legend .edge-key em {{
  width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent;
  border-left: 8px solid #7f8ea8; display: inline-block;
}}
.legend .today-key b {{
  width: 3px; height: 14px; background: {_TODAY}; border-radius: 2px; display: inline-block;
}}
.axis {{ flex: none; height: {_AXIS_H}px; display: flex; }}
.axis-space {{ width: 340px; flex: none; }}
.axis-days {{ flex: 1; position: relative; }}
.axis-days .day {{
  position: absolute; top: 8px; transform: translateX(-50%);
  text-align: center; white-space: nowrap;
}}
.axis-days .day b {{ display: block; font-size: 15px; font-weight: 630; color: #cbd5e1; }}
.axis-days .day i {{ display: block; font-style: normal; font-size: 11px; color: #66738c; margin-top: 3px; }}
.axis-days .day.today b {{ color: {_TODAY}; }}
.axis-days .day.today i {{ color: {_TODAY}; font-weight: 630; }}
.sec {{ flex: none; margin-top: 10px; }}
.sec-head {{ display: flex; align-items: center; gap: 10px; height: {_SEC_HEAD_H}px; }}
.sec-head .dot {{ width: 14px; height: 14px; border-radius: 7px; flex: none; }}
.sec-head .sec-name {{ font-size: 22px; font-weight: 700; }}
.sec-head .sec-count {{ font-size: 14px; color: #8b95a8; }}
.sec-head .sec-line {{ flex: 1; height: 2px; background: #232c44; }}
.row {{ display: flex; height: {_ROW_H}px; align-items: center; }}
.row.empty {{ height: {_EMPTY_H}px; }}
.info {{ width: 340px; flex: none; padding-right: 16px; overflow: hidden; }}
.info .l1 {{ display: flex; align-items: center; gap: 8px; }}
.info .chip {{
  flex: none; font-size: 12px; font-weight: 630; color: #ffffff;
  padding: 2px 8px; border-radius: 4px; letter-spacing: 1px;
}}
.info .nm {{
  font-size: 20px; font-weight: 700; color: #ffffff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.info .l2, .info .l3, .info .l4 {{ margin-top: 4px; font-size: 12.5px; color: #9aa7bd; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }}
.row.empty .l2 {{ font-size: 13px; }}
.track {{ flex: 1; position: relative; height: 100%; }}
.vline {{ position: absolute; top: 6px; bottom: 6px; width: 1px;
  border-left: 1px dashed #2b3550; }}
.today-line {{ position: absolute; top: 2px; bottom: 2px; width: 2px;
  background: {_TODAY}; z-index: 3; opacity: 0.9; }}
.bridge {{ position: absolute; top: 50%; transform: translateY(-50%);
  height: 9px; border-radius: 5px; background: {_BRIDGE_BG}; z-index: 1; }}
.apply-window {{ position: absolute; top: 50%; transform: translateY(-50%);
  height: 15px; border-radius: 7px; z-index: 2; }}
.tick {{ position: absolute; top: 50%; transform: translateY(-50%);
  height: 13px; border-radius: 6px; z-index: 2; }}
.tick.mid {{ height: 11px; }}
.tick.heavy {{ height: 19px; border-radius: 9px; box-shadow: 0 0 8px rgba(255,255,255,0.18); }}
.chev {{ position: absolute; top: 50%; transform: translateY(-50%);
  width: 0; height: 0; border-top: 8px solid transparent; border-bottom: 8px solid transparent;
  z-index: 2; }}
.chev-l {{ left: 0; border-right: 12px solid; }}
.chev-r {{ right: 0; border-left: 12px solid; }}
.edge-note {{ position: absolute; top: 5px; font-size: 11px; font-weight: 630; z-index: 4; white-space: nowrap; }}
.edge-note span {{ margin-right: 10px; }}
.edge-note.left {{ left: 10px; }}
.edge-note.right {{ right: 10px; }}
.footer {{ flex: none; width: 850px; height: {_FOOT_H}px;
  margin: {_FOOT_GAP}px auto {_FOOT_GAP}px; }}
</style>
</head>
<body>
<div class="page">
  <div class="head">
    <div class="title">IPO 日历</div>
    <div class="subtitle">{
        _e(
            f"{data.window_start.year}年 T-2 至 T+7 · "
            f"{data.window_start.month:02d}-{data.window_start.day:02d} ~ "
            f"{data.window_end.month:02d}-{data.window_end.day:02d} · 共 {data.total} 只"
        )
    }</div>
    {legend}
  </div>
  {axis}
  {sections}
  <img class="footer" src="{_footer_uri()}" width="850" height="{_FOOT_H}" />
</div>
</body>
</html>
"""
