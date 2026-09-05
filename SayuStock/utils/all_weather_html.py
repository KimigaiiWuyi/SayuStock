"""全天候板块 HTML（pytakumi / render_html_to_bytes）。"""

from __future__ import annotations

import html as html_lib
from base64 import b64encode
from pathlib import Path
from datetime import datetime
from functools import lru_cache

from .constant import bond, whsc, crypto, i_code, commodity
from .time_range import now_bjt, is_market_active_now, get_sessions_for_code, has_session_started_today
from .market.display import DisplayItem

_FOOTER_PATH = Path(__file__).resolve().parent / "texture2d" / "footer.png"

# 全 HTML 排版，高度随分区行数 / 是否有分时变
CSS_WIDTH = 1000
_HEAD_H = 420
_SEC_HEAD_H = 44
_TILE_H = 124
_TILE_H_SPARK = 160
_CELL_H = 140
_CELL_H_SPARK = 176
_FOOT_H = 40
_FOOT_GAP = 16
_FOOT_PAD = _FOOT_GAP + _FOOT_H + _FOOT_GAP
SPARK_W = 168.0
SPARK_H = 72.0

# 名称左侧国旗 / 资源 emoji（Twemoji COLR）
ALL_WEATHER_EMOJI: dict[str, str] = {
    "上证指数": "🇨🇳",
    "恒生指数": "🇭🇰",
    "日经225": "🇯🇵",
    "韩国KOSPI200": "🇰🇷",
    "纳斯达克": "🇺🇸",
    "道琼斯": "🇺🇸",
    "标普500": "🇺🇸",
    "罗素2000价值股ETF-iShares": "🇺🇸",
    "罗素2000": "🇺🇸",
    "欧洲斯托克600": "🇪🇺",
    "英国富时100": "🇬🇧",
    "法国CAC40": "🇫🇷",
    "德国DAX30": "🇩🇪",
    "XAU": "🥇",
    "XAG": "🥈",
    "综合铜03": "🟠",
    "NYMEX原油": "🛢️",
    "螺纹钢主连": "🏗️",
    "豆粕主连": "🌾",
    "焦煤主连": "🪨",
    "生猪主连": "🐷",
    "中国30年期国债": "🇨🇳",
    "中国10年期国债": "🇨🇳",
    "中国2年期国债": "🇨🇳",
    "美国30年期国债收益率": "🇺🇸",
    "美国10年期国债收益率": "🇺🇸",
    "美国2年期国债收益率": "🇺🇸",
    "JP 30Y": "🇯🇵",
    "JP 10Y": "🇯🇵",
    "美元兑离岸人民币": "🇺🇸🇨🇳",
    "美元兑瑞郎": "🇺🇸🇨🇭",
    "美元兑日元": "🇺🇸🇯🇵",
    "美元指数": "🇺🇸",
    "BTC": "🟠",
    "ETH": "💠",
    "SOL": "☀️",
    "XRP": "💧",
}

_SHORT_NAME: dict[str, str] = {
    "罗素2000价值股ETF-iShares": "罗素2000",
    "美国30年期国债收益率": "美国30年国债",
    "美国10年期国债收益率": "美国10年国债",
    "美国2年期国债收益率": "美国2年国债",
}

_SEC_COLOR: dict[str, str] = {
    "国际市场": "rgb(116,41,48)",
    "大宗商品": "rgb(70,21,77)",
    "债券市场": "rgb(76,63,19)",
    "外汇市场": "rgb(22,73,76)",
    "加密货币": "rgb(122,64,42)",
}
_SEC_COLOR_FALLBACK = "rgb(75,85,104)"
_TRACK_H = 44
_TRACK_COUNT = 7
_PLOT_H = _TRACK_H * _TRACK_COUNT
_AXIS_START_MIN = 8 * 60
_AXIS_SPAN_MIN = 24 * 60
_TRACK_CODE: dict[str, str] = {
    "A股": "1.000001",
    "港股": "100.HSI",
    "日股": "100.N225",
    "韩股": "100.KOSPI200",
    "欧股": "100.SXXP",
    "美股": "100.NDX",
    "加密": "BTC",
}
_FALLBACK_EMOJI = "🌐"
_STALE_PRICE = "#9aa7bd"

_CODE_BY_NAME: dict[str, str] = {}
for _table in (i_code, commodity, bond, whsc, crypto):
    _CODE_BY_NAME.update({k: v for k, v in _table.items() if v})


def resolve_emoji(name: str) -> str:
    if name in ALL_WEATHER_EMOJI:
        return ALL_WEATHER_EMOJI[name]
    for key in sorted(ALL_WEATHER_EMOJI, key=len, reverse=True):
        if key and (key in name or name in key):
            return ALL_WEATHER_EMOJI[key]
    return _FALLBACK_EMOJI


def display_label(name: str) -> str:
    if name in _SHORT_NAME:
        return _SHORT_NAME[name]
    for key, short in _SHORT_NAME.items():
        if key in name or name in key:
            return short
    return name


def format_price(price: float) -> str:
    av = abs(price)
    if av >= 100:
        text = f"{price:.2f}"
    elif av >= 10:
        text = f"{price:.2f}"
    else:
        text = f"{price:.4f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_change(change_pct: float) -> str:
    sign = "+" if change_pct >= 0 else ""
    return f"{sign}{change_pct:.2f}%"


def _e(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


@lru_cache(maxsize=4)
def _data_uri(path: str) -> str:
    file = Path(path)
    raw = file.read_bytes()
    suffix = file.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{b64encode(raw).decode('ascii')}"


def _tile_colors(change_pct: float) -> tuple[str, str]:
    """对齐旧 PIL：灰底 + alpha 150 叠在 #07091b 上，字色涨红跌绿。"""
    intensity = min(abs(change_pct), 4.0) / 4.0
    base = 26
    peak = int(base + intensity * (170 - base))
    alpha = 150 / 255
    br, bg0, bb = 7, 9, 27

    def _blend(channel: int, over: int) -> int:
        return int(channel + alpha * (over - channel))

    if change_pct >= 0:
        bg = f"rgb({_blend(br, peak)},{_blend(bg0, base)},{_blend(bb, base)})"
        fg = "#ce221e"
    else:
        bg = f"rgb({_blend(br, base)},{_blend(bg0, peak)},{_blend(bb, base)})"
        fg = "#24ce1e"
    return bg, fg


def em_secid(code: str) -> str:
    """clist 国际市场键是 ``i:100.HSI``；quote / trends2 要去掉 ``i:``。"""
    text = code.strip()
    if len(text) >= 2 and text[:2].lower() == "i:":
        return text[2:]
    return text


def resolve_item_code(item: DisplayItem) -> str:
    if item.name in _CODE_BY_NAME:
        return _CODE_BY_NAME[item.name]
    for key, code in _CODE_BY_NAME.items():
        if key and (key in item.name or item.name in key):
            return code
    return item.code or ""


def is_stale_quote(item: DisplayItem, now: datetime) -> bool:
    code = resolve_item_code(item)
    if not code:
        return now.weekday() >= 5
    return not has_session_started_today(code, now_bjt=now)


def _spark_for(item: DisplayItem, sparklines: dict[str, str] | None) -> str:
    if not sparklines:
        return ""
    if item.name in sparklines:
        return sparklines[item.name]
    if item.code and item.code in sparklines:
        return sparklines[item.code]
    resolved = resolve_item_code(item)
    if resolved:
        if resolved in sparklines:
            return sparklines[resolved]
        secid = em_secid(resolved)
        if secid and secid in sparklines:
            return sparklines[secid]
    for key, svg in sparklines.items():
        if key and (key in item.name or item.name in key):
            return svg
    return ""


def _section_uses_spark(items: list[DisplayItem], sparklines: dict[str, str] | None) -> bool:
    return any(bool(_spark_for(item, sparklines)) for item in items)


def all_weather_canvas_size(
    sections: list[tuple[str, list[DisplayItem]]],
    sparklines: dict[str, str] | None = None,
) -> tuple[int, int]:
    height = _HEAD_H + _FOOT_PAD
    for _, items in sections:
        if not items:
            continue
        height += _SEC_HEAD_H
        rows = max((len(items) + 3) // 4, 1)
        cell = _CELL_H_SPARK if _section_uses_spark(items, sparklines) else _CELL_H
        height += rows * cell
    return CSS_WIDTH, height


def _tile_html(item: DisplayItem, now: datetime, spark_svg: str = "") -> str:
    bg, fg = _tile_colors(item.change_pct)
    label = display_label(item.name)
    emoji = resolve_emoji(item.name)
    cls = "up" if item.change_pct >= 0 else "down"
    nm_cls = "nm long" if len(label) >= 9 else "nm"
    stale = is_stale_quote(item, now)
    tile_cls = "tile stale" if stale else "tile"
    price_color = _STALE_PRICE if stale else fg
    rest = '<span class="rest">[休]</span>' if stale else ""
    spark = f'<div class="spark">{spark_svg}</div>' if spark_svg else ""
    has_spark = " has-spark" if spark_svg else ""
    return (
        f'<div class="{tile_cls}{has_spark}" style="background:{bg}">'
        f'<div class="price {cls}" style="color:{price_color}">{_e(format_price(item.price))}</div>'
        f'<div class="chg {cls}" style="color:{fg}">{_e(format_change(item.change_pct))}</div>'
        f"{spark}"
        f'<div class="name"><span class="emo">{emoji}</span>'
        f'<span class="{nm_cls}">{_e(label)}</span>{rest}</div>'
        f"</div>"
    )


def _hhmm_pct(hhmm: str, *, close: bool = False) -> float:
    """08:00→次日 08:00 轴上的百分比。close 时 08:00 为 100%。"""
    if close and hhmm == "08:00":
        return 100.0
    hour, minute = map(int, hhmm.split(":"))
    mins = hour * 60 + minute
    if mins < _AXIS_START_MIN:
        mins += 24 * 60
    return max(0.0, min(100.0, (mins - _AXIS_START_MIN) / _AXIS_SPAN_MIN * 100.0))


def _now_pct(now: datetime) -> float:
    mins = now.hour * 60 + now.minute
    if mins < _AXIS_START_MIN:
        mins += 24 * 60
    return max(0.0, min(100.0, (mins - _AXIS_START_MIN) / _AXIS_SPAN_MIN * 100.0))


_TRACK_COLOR: dict[str, str] = {
    "A股": "#ef4444",
    "港股": "#d946ef",
    "日股": "#fb923c",
    "韩股": "#38bdf8",
    "欧股": "#a78bfa",
    "美股": "#60a5fa",
    "加密": "#fbbf24",
}


def _timeline_tracks(now: datetime) -> list[tuple[str, str, list[tuple[str, str]]]]:
    rows: list[tuple[str, str, list[tuple[str, str]]]] = []
    for name, code in _TRACK_CODE.items():
        if name == "加密":
            segs = [("08:00", "08:00")]
        else:
            segs = get_sessions_for_code(code, now)
        rows.append((name, _TRACK_COLOR[name], segs))
    return rows


def _timeline_html(now: datetime) -> str:
    tracks = _timeline_tracks(now)
    tick_at = (
        ("08:00", False),
        ("09:30", False),
        ("12:00", False),
        ("15:00", False),
        ("16:00", False),
        ("21:30", False),
        ("00:00", False),
        ("04:00", False),
        ("08:00", True),
    )
    vlines = "".join(
        f'<div class="vline" style="left:{_hhmm_pct(label, close=close):.2f}%"></div>' for label, close in tick_at
    )
    tick_html = "".join(
        f'<div class="tick" style="left:{93.0 if close else _hhmm_pct(label):.2f}%">{_e(label)}</div>'
        for label, close in tick_at
    )
    labels: list[str] = []
    rows: list[str] = []
    for name, color, segs in tracks:
        code = _TRACK_CODE.get(name, "")
        live = bool(code) and is_market_active_now(code, now_bjt=now) and has_session_started_today(code, now_bjt=now)
        state = "on" if live else "off"
        name_style = f"color:{color}" if live else ""
        labels.append(f'<div class="tname {state}" style="{name_style}">{_e(name)}</div>')
        bars: list[str] = []
        for start, end in segs:
            if start == end:
                left, width = 0.0, 100.0
            else:
                left = _hhmm_pct(start)
                right = _hhmm_pct(end, close=(end == "08:00"))
                if right < left:
                    right += 100.0
                width = max(0.8, right - left)
            glow = f"box-shadow:0 0 10px {color},0 0 3px #ffffff;border:1px solid #ffffff;" if live else "opacity:0.22;"
            bars.append(
                f'<div class="tbar" style="left:{left:.2f}%;width:{width:.2f}%;background:{color};{glow}"></div>'
            )
        rows.append(f'<div class="track {state}">{"".join(bars)}</div>')
    now_left = _now_pct(now)
    now_label = now.strftime("%H:%M")
    return (
        '<div class="topbar"><div class="top-l"></div><div class="top-r"></div></div>'
        '<div class="gantt">'
        f'<div class="tcol">{"".join(labels)}</div>'
        '<div class="tplot">'
        f"{vlines}{''.join(rows)}"
        f'<div class="now-line" style="left:{now_left:.2f}%">'
        f'<div class="now-dot"></div><div class="now-cap">{_e(now_label)}</div></div>'
        f'<div class="ticks">{tick_html}</div>'
        "</div></div>"
    )


def _section_html(
    title: str,
    items: list[DisplayItem],
    now: datetime,
    sparklines: dict[str, str] | None,
) -> str:
    if not items:
        return ""
    color = _SEC_COLOR[title] if title in _SEC_COLOR else _SEC_COLOR_FALLBACK
    spark_sec = " has-spark" if _section_uses_spark(items, sparklines) else ""
    tiles = "".join(_tile_html(item, now, _spark_for(item, sparklines)) for item in items)
    return (
        f'<section class="sec{spark_sec}">'
        f'<div class="sec-title">'
        f'<div class="sec-bar" style="background:{color}"></div>'
        f"<span>{_e(title)}</span>"
        f'<div class="sec-bar" style="background:{color}"></div>'
        f"</div>"
        f'<div class="grid">{tiles}</div>'
        f"</section>"
    )


def build_all_weather_html(
    sections: list[tuple[str, list[DisplayItem]]],
    *,
    now: datetime | None = None,
    sparklines: dict[str, str] | None = None,
) -> str:
    """时间轴 + 分区标题 + 行情格全部 HTML；高度随内容变。"""
    as_of = now or now_bjt()
    stamp = as_of.strftime("%Y-%m-%d %H:%M")
    width, height = all_weather_canvas_size(sections, sparklines)
    inner = "".join(_section_html(title, items, as_of, sparklines) for title, items in sections)
    footer_uri = _data_uri(str(_FOOTER_PATH))
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  width: {width}px;
  height: {height}px;
  background: #07091b;
  font-family: "MiSans", "Twemoji Mozilla", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: #e9eef7;
}}
.page {{
  width: {width}px;
  height: {height}px;
  background: #07091b;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}}
.head {{
  flex: none;
  height: {_HEAD_H}px;
  padding: 36px 70px 8px;
}}
.topbar {{ display: flex; height: 8px; border-radius: 8px; overflow: hidden; }}
.top-l {{ flex: 1; background: #8b3a42; }}
.top-r {{ flex: 1; background: #2a6a6a; }}
.gantt {{ margin-top: 14px; display: flex; align-items: flex-start; }}
.tcol {{ width: 40px; flex: none; padding-top: 16px; }}
.tname {{
  height: {_TRACK_H}px; font-size: 14px; font-weight: 630; line-height: {_TRACK_H}px;
}}
.tname.on {{ text-shadow: 0 0 8px currentColor; }}
.tname.off {{ color: #4b5568; font-weight: 400; }}
.tplot {{ flex: 1; position: relative; padding-top: 16px; }}
.vline {{
  position: absolute; top: 16px; height: {_PLOT_H}px; width: 1px;
  border-left: 1px dashed #3a4258;
}}
.track {{ position: relative; height: {_TRACK_H}px; }}
.tbar {{
  position: absolute; top: {(_TRACK_H - 12) // 2}px; height: 12px; border-radius: 6px;
  box-sizing: border-box;
}}
.track.on .tbar {{
  height: 16px; top: {(_TRACK_H - 16) // 2}px;
}}
.now-line {{
  position: absolute; top: 2px; height: {14 + _PLOT_H}px; width: 2px;
  background: #fde68a; z-index: 3;
}}
.now-dot {{
  position: absolute; left: -4px; top: 0;
  width: 10px; height: 10px; border-radius: 5px; background: #fde68a;
}}
.now-cap {{
  position: absolute; left: 8px; top: -1px;
  font-size: 12px; font-weight: 630; color: #fde68a; white-space: nowrap;
}}
.ticks {{ position: relative; height: 22px; margin-top: 4px; }}
.tick {{
  position: absolute; top: 0; font-size: 12px; color: #8b95a8; font-weight: 400; white-space: nowrap;
}}
.body {{ flex: none; padding: 0 62px; }}
.sec-title {{
  display: flex; align-items: center; gap: 16px; height: {_SEC_HEAD_H}px;
}}
.sec-bar {{ flex: 1; height: 20px; }}
.sec-title span {{
  flex: none; font-size: 22px; font-weight: 700; color: #ffffff; letter-spacing: 2px;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  column-gap: 9px;
}}
.tile {{
  width: 100%; height: {_TILE_H}px; margin: 8px 0; border-radius: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
}}
.sec.has-spark .tile {{ height: {_TILE_H_SPARK}px; }}
.tile .price {{ font-size: 28px; font-weight: 700; line-height: 1.1; }}
.tile .chg {{ font-size: 26px; font-weight: 630; line-height: 1.15; margin-top: 2px; }}
.tile.has-spark .price {{ font-size: 24px; }}
.tile.has-spark .chg {{ font-size: 20px; margin-top: 2px; }}
.tile .spark {{
  width: {SPARK_W:.0f}px; height: {SPARK_H:.0f}px; margin-top: 4px;
  flex: none; overflow: hidden;
}}
.tile .spark svg {{ display: block; width: 100%; height: 100%; }}
.tile .name {{
  display: flex; align-items: center; justify-content: center; gap: 5px;
  margin-top: 8px;
}}
.tile.has-spark .name {{ margin-top: 8px; }}
.tile .emo {{ font-size: 18px; line-height: 1; flex: none; }}
.tile .nm {{ font-size: 20px; color: #ffffff; font-weight: 700; line-height: 1.2; }}
.tile .nm.long {{ font-size: 16px; font-weight: 630; }}
.tile .rest {{ font-size: 12px; font-weight: 630; color: #fde68a; flex: none; }}
.tile.stale {{ opacity: 0.6; }}
.footer {{
  flex: none;
  width: 850px; height: {_FOOT_H}px;
  margin: {_FOOT_GAP}px 75px {_FOOT_GAP}px;
}}
</style>
</head>
<body>
<div class="page">
  <!-- as_of:{_e(stamp)} -->
  <div class="head">{_timeline_html(as_of)}</div>
  <div class="body">{inner}</div>
  <img class="footer" src="{footer_uri}" width="850" height="40" />
</div>
</body>
</html>
"""
