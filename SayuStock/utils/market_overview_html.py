"""大盘概览 HTML（pytakumi / render_html_to_bytes）。"""

from __future__ import annotations

import html as html_lib
from base64 import b64encode
from typing import Dict, Tuple
from pathlib import Path
from datetime import datetime
from functools import lru_cache

from .market import BREADTH_DIRECTION, DisplayItem

TEXT_PATH = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"
_FOOTER_PATH = Path(__file__).resolve().parent / "texture2d" / "footer.png"

CSS_WIDTH = 1700
CANVAS_HEIGHT = 2885

# 涨跌分布柱几何（div.png 内坐标系，画布 850×500）
_BREADTH_BAR_LEFT = 45
_BREADTH_BAR_WIDTH = 36
_BREADTH_BAR_RIGHT = 807

_BREADTH_DIRECTION_COLORS: Dict[int, Tuple[int, int, int]] = {
    1: (187, 26, 26),
    0: (150, 150, 150),
    -1: (23, 199, 30),
}
_BREADTH_BAR_COLORS: Dict[str, Tuple[int, int, int]] = {
    label: _BREADTH_DIRECTION_COLORS[direction] for label, direction in BREADTH_DIRECTION.items()
}


def breadth_bar_left(index: int, total: int) -> int:
    """分布柱左缘 x：index 从 0 起（0 = 跌停侧），末柱右缘恒为 _BREADTH_BAR_RIGHT。"""
    if total < 2:
        return _BREADTH_BAR_LEFT
    span = _BREADTH_BAR_RIGHT - _BREADTH_BAR_WIDTH - _BREADTH_BAR_LEFT
    return round(_BREADTH_BAR_LEFT + index * span / (total - 1))


def _e(val: object) -> str:
    return html_lib.escape(str(val) if val is not None else "", quote=True)


@lru_cache(maxsize=32)
def _data_uri(path: str) -> str:
    file = Path(path)
    raw = file.read_bytes()
    suffix = file.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64encode(raw).decode('ascii')}"


def card_gradient_bg(change_pct: float) -> str:
    max_diff = 4.0
    neutral = 26
    intensity = min(abs(change_pct), max_diff) / max_diff
    peak = int(neutral + intensity * (170 - neutral))
    if change_pct > 0:
        return f"rgba({peak}, {neutral}, {neutral}, 0.75)"
    elif change_pct < 0:
        return f"rgba({neutral}, {peak}, {neutral}, 0.75)"
    return f"rgba({neutral}, {neutral}, {neutral}, 0.75)"


def build_market_overview_html(
    *,
    data_zs_items: list[DisplayItem],
    sparks: dict[str, str],
    diff_bar: dict[str, int],
    up_value: int,
    down_value: int,
    em_flow_uri: str,
    now: datetime,
    trade_date: datetime | None,
    all_f6_str: str,
    f6diff_str: str,
    fcolor: str,
    qz_diff: float,
    sz_diff: float,
    data_hy_fall: list[DisplayItem],
    data_hy_rise: list[DisplayItem],
    data_gn_fall: list[DisplayItem],
    data_gn_rise: list[DisplayItem],
    source_line: str,
    title_num: int,
) -> str:
    """构建大盘概览完整 HTML。"""
    title_path = TEXT_PATH / f"title{title_num}.png"
    title_uri = _data_uri(str(title_path)) if title_path.exists() else ""
    bar1_uri = _data_uri(str(TEXT_PATH / "bar1.png"))
    bar2_uri = _data_uri(str(TEXT_PATH / "bar2.png"))
    bar3_uri = _data_uri(str(TEXT_PATH / "bar3.png"))
    bar4_uri = _data_uri(str(TEXT_PATH / "bar4.png"))
    div_uri = _data_uri(str(TEXT_PATH / "div.png"))
    footer_uri = _data_uri(str(_FOOTER_PATH))

    # 1. 右上角时间与成交额卡片
    weekday = now.strftime("星期" + "一二三四五六日"[now.weekday()])
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%Y.%m.%d")

    time_color_bg = "rgba(186, 26, 27, 0.45)" if sz_diff >= 0 else "rgba(18, 199, 30, 0.45)"
    time_text_color = "#ba1a1b" if sz_diff >= 0 else "#12c71e"

    stale_days = 0 if trade_date is None else (now.date() - trade_date.date()).days
    if stale_days > 0:
        days_label = {1: "上日", 2: "前日", 3: "三日前"}.get(stale_days, f"{stale_days}日前")
        time_block_bg = "rgba(60, 60, 60, 0.85)"
        weekday_color = "#a0a0a0"
        main_time_text = "休  市"
        main_time_color = "#ffc800"
        date_color = "#a0a0a0"
        vol_label = f"成交额({days_label}): {_e(all_f6_str)}"
    else:
        time_block_bg = time_color_bg
        weekday_color = "#ffffff"
        main_time_text = time_str
        main_time_color = "#ffffff"
        date_color = "#ffffff"
        vol_label = f"成交额: {_e(all_f6_str)}"

    # 2. 主要指数卡片 HTML（现价写在下面，上面是名称和涨跌幅，中间是折线图）
    cards_html = []
    for it in data_zs_items[:12]:
        diff = round(it.change_pct, 2)
        diff_str = f"+{diff:.2f}%" if diff >= 0 else f"{diff:.2f}%"
        bg = card_gradient_bg(diff)
        if diff > 0:
            pct_color = "#ff4d4f"
            price_color = "#ff7875"
        elif diff < 0:
            pct_color = "#00e676"
            price_color = "#24ce1e"
        else:
            pct_color = "#d1d5db"
            price_color = "#9ca3af"

        svg = sparks.get(it.name, "") or sparks.get(it.code or "", "")
        chart_div = f'<div class="idx-chart">{svg}</div>' if svg else '<div class="idx-chart empty"></div>'

        cards_html.append(f"""
        <div class="idx-card" style="background: {bg};">
            <div class="idx-name">{_e(it.name)}</div>
            <div class="idx-pct" style="color: {pct_color};">{_e(diff_str)}</div>
            {chart_div}
            <div class="idx-price" style="color: {price_color};">{_e(it.price)}</div>
        </div>
        """)
    cards_str = "".join(cards_html)

    # 3. 涨跌分布直方柱 HTML
    bands = list(diff_bar.items())[::-1]
    max_num = max(diff_bar.values()) if diff_bar and max(diff_bar.values()) > 0 else 1
    max_h = 366
    breadth_bars_html = []
    for dindex, (band, ij_num) in enumerate(bands):
        if ij_num == 0:
            continue
        left = breadth_bar_left(dindex, len(bands))
        lenth = int(max_h * ij_num / max_num)
        color = "rgb" + str(_BREADTH_BAR_COLORS[band])
        top_y = 413 - lenth
        num_y = top_y - 28
        bar_tag = (
            f'<div style="position: absolute; left: {left}px; top: {top_y}px; '
            f'width: {_BREADTH_BAR_WIDTH}px; height: {lenth}px; background: {color};"></div>'
        )
        num_tag = (
            f'<div style="position: absolute; left: {left - 12}px; top: {num_y}px; width: 60px; '
            f"text-align: center; color: #ffffff; font-size: 24px; font-weight: bold; "
            f'font-family: sans-serif;">{ij_num}</div>'
        )
        breadth_bars_html.append(f"{bar_tag}\n{num_tag}\n")
    breadth_bars_str = "".join(breadth_bars_html)

    # 4. 行业与概念条目 HTML
    def render_board_col(items: list[DisplayItem], left_x: int) -> str:
        rows_html = []
        ls = 20
        for hindex, hy in enumerate(items[:20]):
            hy_diff = hy.change_pct
            base_o = round((((ls + 1) - hindex) / ls), 3)
            if hy_diff >= 0:
                bg = f"rgba(140, 18, 22, {base_o})"
                lead_color = "rgb(201, 26, 32)"
                lead = hy.lead_name or ""
                lead_pct = f"+{hy.lead_change_pct:.2f}%" if hy.lead_change_pct is not None else ""
            else:
                bg = f"rgba(59, 140, 18, {base_o})"
                lead_color = "rgb(25, 199, 16)"
                lead = hy.fall_name or hy.lead_name or ""
                lead_pct = (
                    f"{hy.fall_change_pct:.2f}%"
                    if hy.fall_change_pct is not None
                    else (f"{hy.lead_change_pct:.2f}%" if hy.lead_change_pct is not None else "")
                )

            diff_str = f"+{hy_diff:.2f}%" if hy_diff >= 0 else f"{hy_diff:.2f}%"
            top_y = 980 + 90 * hindex

            rows_html.append(
                f'<div style="position: absolute; left: {left_x}px; top: {top_y}px; width: 425px; height: 90px;">'
                f'<div style="position: absolute; left: 23px; top: 2px; width: 380px; height: 55px; '
                f'background: {bg}; border-radius: 0;"></div>'
                f'<div style="position: absolute; left: 53px; top: 12px; width: 220px; font-size: 30px; '
                f"color: #ffffff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; "
                f'font-weight: 500;">{_e(hy.name)}</div>'
                f'<div style="position: absolute; right: 41px; top: 12px; font-size: 30px; '
                f'color: #ffffff; font-weight: 600;">{_e(diff_str)}</div>'
                f'<div style="position: absolute; left: 53px; top: 60px; width: 220px; font-size: 24px; '
                f"color: {lead_color}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; "
                f'font-weight: 500;">{_e(lead)}</div>'
                f'<div style="position: absolute; right: 41px; top: 60px; font-size: 24px; '
                f'color: {lead_color}; font-weight: 600;">{_e(lead_pct)}</div>'
                f"</div>"
            )
        return "".join(rows_html)

    hy_fall_col = render_board_col(data_hy_fall, 0)
    hy_rise_col = render_board_col(data_hy_rise, 415)
    gn_fall_col = render_board_col(data_gn_fall, 850)
    gn_rise_col = render_board_col(data_gn_rise, 1265)

    em_flow_tag = f'<img class="em-flow" src="{em_flow_uri}" />' if em_flow_uri else ""
    title_tag = f'<img class="header-title" src="{title_uri}" />' if title_uri else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    width: {CSS_WIDTH}px;
    height: {CANVAS_HEIGHT}px;
    background: #07091b;
    font-family: "MiSans", "Twemoji Mozilla", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #e9eef7;
    position: relative;
    overflow: hidden;
}}

.header-title {{
    position: absolute;
    left: 0;
    top: -30px;
    width: 850px;
    height: 400px;
    pointer-events: none;
}}

.em-flow {{
    position: absolute;
    left: 882px;
    top: 32px;
    width: 500px;
    height: 274px;
}}

.time-box {{
    position: absolute;
    left: 1395px;
    top: 62px;
    width: 260px;
    height: 167px;
    background: {time_block_bg};
    border-radius: 4px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
}}
.time-weekday {{
    font-size: 36px;
    color: {weekday_color};
    line-height: 42px;
}}
.time-main {{
    font-size: 58px;
    font-weight: bold;
    color: {main_time_color};
    line-height: 64px;
}}
.time-date {{
    font-size: 36px;
    color: {date_color};
    line-height: 42px;
}}
.vol-label {{
    position: absolute;
    left: 1395px;
    top: 246px;
    width: 260px;
    text-align: center;
    font-size: 28px;
    color: {time_text_color};
    font-weight: 500;
}}
.vol-diff {{
    position: absolute;
    left: 1395px;
    top: 288px;
    width: 260px;
    text-align: center;
    font-size: 34px;
    color: {fcolor};
    font-weight: bold;
}}

.bar1 {{ position: absolute; left: 0; top: 331px; width: 850px; height: 90px; }}
.bar4 {{ position: absolute; left: 850px; top: 331px; width: 850px; height: 90px; }}
.bar2 {{ position: absolute; left: 0; top: 875px; width: 850px; height: 90px; }}
.bar3 {{ position: absolute; left: 850px; top: 875px; width: 850px; height: 90px; }}

/* 金色框：只圈住上证指数和中证全指（第一行前两个格子） */
.gold-focus-box {{
    position: absolute;
    left: 16px;
    top: 428px;
    width: 407px;
    height: 144px;
    border: 3.5px solid #f6b400;
    border-radius: 8px;
    box-sizing: border-box;
    pointer-events: none;
    box-shadow: 0 0 10px rgba(246, 180, 0, 0.4);
}}

/* 主要指数容器：间距加到 12px，卡片精致紧凑 */
.index-container {{
    position: absolute;
    left: 20px;
    top: 432px;
    width: 810px;
    height: 432px;
    box-sizing: border-box;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    grid-template-rows: repeat(3, 1fr);
    gap: 12px;
}}

.idx-card {{
    border-radius: 6px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    align-items: center;
    padding: 6px 6px 5px 6px;
    box-sizing: border-box;
    overflow: hidden;
}}

.idx-name {{
    font-size: 15px;
    font-weight: 600;
    color: #ffffff;
    text-align: center;
    line-height: 18px;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    width: 100%;
    margin-bottom: 1px;
}}

.idx-pct {{
    font-size: 28px;
    font-weight: 800;
    line-height: 30px;
    text-align: center;
    width: 100%;
    letter-spacing: -0.5px;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.7);
}}

.idx-chart {{
    height: 32px;
    width: 100%;
    margin: 2px 0;
}}
.idx-chart svg {{
    width: 100%;
    height: 100%;
    display: block;
}}
.idx-chart.empty {{
    border-bottom: 1px dashed rgba(255, 255, 255, 0.15);
    height: 18px;
    margin: 7px 0;
}}

.idx-price {{
    font-size: 19px;
    font-weight: 600;
    line-height: 20px;
    text-align: center;
    width: 100%;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
}}

.breadth-container {{
    position: absolute;
    left: 850px;
    top: 420px;
    width: 850px;
    height: 500px;
    background-image: url('{div_uri}');
    background-size: 850px 500px;
}}
.breadth-down-box {{
    position: absolute;
    left: 20px;
    top: 0;
    width: 80px;
    height: 40px;
    background: rgba(23, 199, 30, 0.6);
    display: flex;
    justify-content: center;
    align-items: center;
    color: #ffffff;
    font-size: 24px;
    font-weight: bold;
}}
.breadth-up-box {{
    position: absolute;
    left: 750px;
    top: 0;
    width: 80px;
    height: 40px;
    background: rgba(187, 26, 26, 0.6);
    display: flex;
    justify-content: center;
    align-items: center;
    color: #ffffff;
    font-size: 24px;
    font-weight: bold;
}}

.source-footer {{
    position: absolute;
    left: 24px;
    top: 2795px;
    font-size: 24px;
    color: rgb(150, 150, 150);
}}
.footer-img {{
    position: absolute;
    left: 425px;
    top: 2835px;
    width: 850px;
    height: 40px;
}}
</style>
</head>
<body>
    {title_tag}
    {em_flow_tag}
    <div class="time-box">
        <div class="time-weekday">{_e(weekday)}</div>
        <div class="time-main">{_e(main_time_text)}</div>
        <div class="time-date">{_e(date_str)}</div>
    </div>
    <div class="vol-label">{vol_label}</div>
    <div class="vol-diff">{_e(f6diff_str)}</div>

    <img class="bar1" src="{bar1_uri}" />
    <img class="bar4" src="{bar4_uri}" />

    <!-- 金色焦点框：只框上证指数和中证全指 -->
    <div class="gold-focus-box"></div>

    <div class="index-container">
        {cards_str}
    </div>

    <div class="breadth-container">
        <div class="breadth-down-box">{down_value}</div>
        <div class="breadth-up-box">{up_value}</div>
        {breadth_bars_str}
    </div>

    <img class="bar2" src="{bar2_uri}" />
    <img class="bar3" src="{bar3_uri}" />

    {hy_fall_col}
    {hy_rise_col}
    {gn_fall_col}
    {gn_rise_col}

    <div class="source-footer">{_e(source_line)}</div>
    <img class="footer-img" src="{footer_uri}" />
</body>
</html>
"""
