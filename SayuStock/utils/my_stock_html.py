"""我的自选 HTML（pytakumi / render_html_to_bytes），像素对齐原 PIL。"""

from __future__ import annotations

import html as html_lib
from base64 import b64encode
from pathlib import Path
from functools import lru_cache

from .utils import number_to_chinese
from .market import Quote, DisplayItem

TEXT_PATH = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"
_FOOTER_PATH = Path(__file__).resolve().parent / "texture2d" / "footer.png"

BAR_H = 110
HEAD_H = 541
FOOT_PAD = 60
TITLE_X_SINGLE = 25
TITLE_X_DOUBLE = 475
TITLE_Y = -31
IDX_Y = 308
BAR5_Y = 443
BARS_Y = 541
IDX_X0_SINGLE = 50
IDX_X0_DOUBLE = 100
# 分时小图：原 210×66 左起 430；缩到 0.65 宽并右对齐，避免压住名称/副标题
SPARK_W = 210.0 * 0.65
SPARK_H = 66.0
SPARK_LEFT = 430.0 + 210.0 - SPARK_W
SPARK_TOP = 22.0


def my_stock_canvas_size(n: int) -> tuple[int, int, bool]:
    two_col = n >= 18
    if two_col:
        rows = ((n - 1) // 2) + 1
        return 1800, HEAD_H + rows * BAR_H + FOOT_PAD, True
    return 900, HEAD_H + n * BAR_H + FOOT_PAD, False


def _e(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


@lru_cache(maxsize=16)
def _data_uri(path: str) -> str:
    file = Path(path)
    raw = file.read_bytes()
    suffix = file.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{b64encode(raw).decode('ascii')}"


def _bar_labels(q: Quote, fallback_code: str) -> tuple[str, str]:
    name = q.symbol.display_name or (q.symbol.name or "").strip()
    code = (q.symbol.code or "").strip() or fallback_code
    return name, code


def _bar_asset(change_pct: float) -> str:
    if change_pct > 0:
        return "myup.png"
    if change_pct == 0:
        return "myeq.png"
    return "mydown.png"


def _sub_color(change_pct: float) -> str:
    if change_pct > 0:
        return "rgb(213,102,102)"
    if change_pct == 0:
        return "rgb(240,240,240)"
    return "rgb(175,231,170)"


def _idx_colors(change_pct: float) -> tuple[str, str]:
    if change_pct >= 0:
        return "rgba(140,18,22,0.216)", "rgb(206,34,30)"
    return "rgba(59,140,18,0.216)", "rgb(36,206,30)"


def _bar_html(q: Quote, uid: str, y: int, x: int, spark_svg: str) -> str:
    p = float(q.change_pct) if q.change_pct is not None else 0.0
    e_money = number_to_chinese(q.amount) if q.amount is not None else "-"
    hs = q.turnover_rate if q.turnover_rate is not None else 0
    b_title, code = _bar_labels(q, uid)
    s_title = f"({code}) 换: {hs}% 额: {e_money} 价: {q.price}"
    pct = f"+{p}%" if p >= 0 else f"{p}%"
    uri = _data_uri(str(TEXT_PATH / _bar_asset(p)))
    spark = f'<div class="spark">{spark_svg}</div>' if spark_svg else ""
    return (
        f'<div class="bar" style="left:{x}px;top:{y}px;background-image:url({uri})">'
        f'<div class="bt">{_e(b_title)}</div>'
        f'<div class="st" style="color:{_sub_color(p)}">{_e(s_title)}</div>'
        f"{spark}"
        f'<div class="pct">{_e(pct)}</div>'
        f"</div>"
    )


def _idx_html(item: DisplayItem, n: int, two_col: bool) -> str:
    x0 = IDX_X0_DOUBLE if two_col else IDX_X0_SINGLE
    left = x0 + 200 * n
    bg, fg = _idx_colors(item.change_pct)
    diff = item.change_pct
    chg = f"{'+' if diff >= 0 else ''}{diff}%"
    name = item.name.split("(")[0].strip()
    return (
        f'<div class="idx" style="left:{left}px;top:{IDX_Y}px">'
        f'<div class="ibox" style="background:{bg}"></div>'
        f'<div class="ipx" style="color:{fg}">{_e(item.price)}</div>'
        f'<div class="ichg" style="color:{fg}">{_e(chg)}</div>'
        f'<div class="inm">{_e(name)}</div>'
        f"</div>"
    )


def build_my_stock_html(
    *,
    quotes: list[tuple[Quote, str]],
    index_items: list[DisplayItem],
    title_num: str,
    sparklines: dict[str, str] | None = None,
) -> str:
    """quotes: (Quote, 原始自选字符串)。index_items 已按展示顺序排好。"""
    n = len(quotes)
    width, height, two_col = my_stock_canvas_size(n)
    title_x = TITLE_X_DOUBLE if two_col else TITLE_X_SINGLE
    title_uri = _data_uri(str(TEXT_PATH / f"title{title_num}.png"))
    bar5_uri = _data_uri(str(TEXT_PATH / "bar5.png"))
    footer_uri = _data_uri(str(_FOOTER_PATH))
    idx_html = "".join(_idx_html(item, i, two_col) for i, item in enumerate(index_items))
    bars: list[str] = []
    split = ((n - 1) // 2) + 1
    for index, (q, uid) in enumerate(quotes):
        if two_col and index >= split:
            x = 900
            y = BARS_Y + (index - split) * BAR_H
        else:
            x = 0
            y = BARS_Y + index * BAR_H
        spark = ""
        if sparklines:
            if uid in sparklines:
                spark = sparklines[uid]
            elif q.symbol.code in sparklines:
                spark = sparklines[q.symbol.code]
        bars.append(_bar_html(q, uid, y, x, spark))
    inner_bars = "".join(bars)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  width: {width}px; height: {height}px; background: #07091b;
  font-family: "MiSans", "Twemoji Mozilla", "PingFang SC", "Microsoft YaHei", sans-serif;
}}
.page {{
  width: {width}px; height: {height}px; position: relative; overflow: hidden;
  background: #07091b;
}}
.title {{
  position: absolute; left: {title_x}px; top: {TITLE_Y}px;
  width: 850px; height: 400px;
}}
.bar5 {{
  position: absolute; left: {title_x}px; top: {BAR5_Y}px;
  width: 850px; height: 90px;
}}
.idx {{ position: absolute; width: 200px; height: 140px; }}
.ibox {{
  position: absolute; left: 15px; top: 13px; width: 170px; height: 114px;
}}
.ipx {{
  position: absolute; left: 100px; top: 38px; transform: translate(-50%, -50%);
  font-size: 28px; font-weight: 700; white-space: nowrap;
}}
.ichg {{
  position: absolute; left: 100px; top: 70px; transform: translate(-50%, -50%);
  font-size: 28px; font-weight: 700; white-space: nowrap;
}}
.inm {{
  position: absolute; left: 100px; top: 99px; transform: translate(-50%, -50%);
  font-size: 22px; font-weight: 630; color: #ffffff; white-space: nowrap;
}}
.bar {{
  position: absolute; width: 900px; height: 110px;
  background-repeat: no-repeat; background-size: 900px 110px;
}}
.bt {{
  position: absolute; left: 82px; top: 40px; transform: translateY(-50%);
  font-size: 30px; font-weight: 700; color: #ffffff; white-space: nowrap;
}}
.st {{
  position: absolute; left: 82px; top: 75px; transform: translateY(-50%);
  font-size: 18px; font-weight: 400; white-space: nowrap;
}}
.spark {{
  position: absolute; left: {SPARK_LEFT:.1f}px; top: {SPARK_TOP:.0f}px;
  width: {SPARK_W:.1f}px; height: {SPARK_H:.0f}px;
}}
.spark svg {{ display: block; width: 100%; height: 100%; }}
.pct {{
  position: absolute; left: 758px; top: 55px; transform: translate(-50%, -50%);
  font-size: 26px; font-weight: 700; color: #ffffff; white-space: nowrap;
}}
.footer {{
  position: absolute; left: {title_x}px; top: {height - 55}px;
  width: 850px; height: 40px;
}}
</style>
</head>
<body>
<div class="page">
  <img class="title" src="{title_uri}" width="850" height="400" />
  {idx_html}
  <img class="bar5" src="{bar5_uri}" width="850" height="90" />
  {inner_bars}
  <img class="footer" src="{footer_uri}" width="850" height="40" />
</div>
</body>
</html>
"""
