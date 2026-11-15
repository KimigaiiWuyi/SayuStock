"""全天候板块与交易时段的模板数据。"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from .constant import bond, whsc, crypto, i_code, commodity
from .time_range import now_bjt, is_market_active_now, get_sessions_for_code, has_session_started_today
from .market.display import DisplayItem, source_footer
from ..render.templates import render_template
from ..render.card_widgets import footer_uri

CSS_WIDTH = 1000
SPARK_W = 168.0
SPARK_H = 32.0

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

_SEC_COLOR = {
    "国际市场": "rgb(116,41,48)",
    "大宗商品": "rgb(70,21,77)",
    "债券市场": "rgb(76,63,19)",
    "外汇市场": "rgb(22,73,76)",
    "加密货币": "rgb(122,64,42)",
}
_SEC_COLOR_FALLBACK = "rgb(75,85,104)"
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


def _tile_colors(change_pct: float) -> tuple[str, str]:
    """还原原版热力底色（#07091b 叠半透红/绿）与高对比涨红跌绿文字。"""
    intensity = min(abs(change_pct), 4.0) / 4.0
    base = 26
    peak = int(base + intensity * (170 - base))
    alpha = 150 / 255
    br, bg0, bb = 7, 9, 27

    def _blend(channel: int, over: int) -> int:
        return int(channel + alpha * (over - channel))

    if change_pct >= 0:
        return f"rgb({_blend(br, peak)},{_blend(bg0, base)},{_blend(bb, base)})", "#ce221e"
    return f"rgb({_blend(br, base)},{_blend(bg0, peak)},{_blend(bb, base)})", "#24ce1e"


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


_TRACK_COLOR = {
    "A股": "#ef4444",
    "港股": "#d946ef",
    "日股": "#fb923c",
    "韩股": "#38bdf8",
    "欧股": "#a78bfa",
    "美股": "#60a5fa",
    "加密": "#fbbf24",
}


@dataclass(frozen=True, slots=True)
class WeatherTile:
    label: str
    long_name: bool
    emoji: str
    price: str
    price_color: str
    change: str
    change_color: str
    bg: str
    tone: str
    stale: bool
    spark: str


@dataclass(frozen=True, slots=True)
class WeatherSection:
    title: str
    accent: str
    tiles: list[WeatherTile]
    has_spark: bool


@dataclass(frozen=True, slots=True)
class SessionBar:
    left: float
    width: float


@dataclass(frozen=True, slots=True)
class SessionTrack:
    name: str
    color: str
    active: bool
    bars: list[SessionBar]


def _session_tracks(now: datetime) -> list[SessionTrack]:
    rows: list[SessionTrack] = []
    for name, code in _TRACK_CODE.items():
        spans = [("08:00", "08:00")] if name == "加密" else get_sessions_for_code(code, now)
        bars: list[SessionBar] = []
        for start, end in spans:
            left = _hhmm_pct(start)
            right = _hhmm_pct(end, close=end == "08:00")
            if start == end:
                bars.append(SessionBar(0.0, 100.0))
            elif right < left:
                bars.extend((SessionBar(left, 100.0 - left), SessionBar(0.0, right)))
            else:
                bars.append(SessionBar(left, max(0.8, right - left)))
        active = is_market_active_now(code, now_bjt=now) and has_session_started_today(code, now_bjt=now)
        rows.append(SessionTrack(name, _TRACK_COLOR[name], active, bars))
    return rows


def build_all_weather_html(
    sections: list[tuple[str, list[DisplayItem]]],
    *,
    now: datetime | None = None,
    sparklines: dict[str, str] | None = None,
    sources: tuple[str | None, ...] = (),
) -> str:
    clock = now or now_bjt()
    tick_labels = ("08:00", "09:30", "12:00", "15:00", "16:00", "21:30", "00:00", "04:00", "08:00")
    ticks = [(label, 93.0 if i == len(tick_labels) - 1 else _hhmm_pct(label)) for i, label in enumerate(tick_labels)]
    vlines = [_hhmm_pct(label, close=i == len(tick_labels) - 1) for i, label in enumerate(tick_labels)]
    view: list[WeatherSection] = []
    used = list(sources)
    for title, items in sections:
        if not items:
            continue
        used.extend(item.provider for item in items)
        tiles: list[WeatherTile] = []
        for item in items:
            label = display_label(item.name)
            stale = is_stale_quote(item, clock)
            bg, fg = _tile_colors(item.change_pct)
            tiles.append(
                WeatherTile(
                    label=label,
                    long_name=sum(1.0 if ord(ch) > 127 else 0.6 for ch in label) >= 5.8,
                    emoji=resolve_emoji(item.name),
                    price=format_price(item.price) + ("%" if "收益率" in item.name else ""),
                    price_color=_STALE_PRICE if stale else fg,
                    change=format_change(item.change_pct),
                    change_color=fg,
                    bg=bg,
                    tone="up" if item.change_pct >= 0 else "down",
                    stale=stale,
                    spark=_spark_for(item, sparklines),
                )
            )
        view.append(
            WeatherSection(
                title,
                _SEC_COLOR[title] if title in _SEC_COLOR else _SEC_COLOR_FALLBACK,
                tiles,
                any(t.spark for t in tiles),
            )
        )
    return render_template(
        "all_weather.html",
        width=CSS_WIDTH,
        spark_w=int(SPARK_W),
        spark_h=int(SPARK_H),
        stamp=clock.strftime("%Y-%m-%d %H:%M"),
        clock=clock.strftime("%H:%M"),
        now_left=_now_pct(clock),
        vlines=vlines,
        ticks=ticks,
        tracks=_session_tracks(clock),
        sections=view,
        source=source_footer(*used),
        brand=footer_uri(),
    )
