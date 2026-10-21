"""基金持仓 HTML（pytakumi / render_html_to_bytes），版式对齐「我的自选」。

三条硬规矩：

1. **一列网格**：行卡片素材自带 30px 透明边，可见区是 30..869。全页的卡片
   （指数条 / 基金信息条 / 图例 / 分隔线）都按这一列对齐，不再各占各的。
2. **颜色有语义**：红/绿/灰行底 = 涨跌（和「我的自选」同一套素材），
   **金色行底 = 这一行取数失败**（myup_gold.png，自 myup 色相偏移而来）。
   黄色只留给失败，正常行不许染黄。
3. **失败写在原位**：取不到行情就在该行涨跌幅格写「失败」，原因写在那行
   折线的位置。**不另挂失败横幅**——失败了就该没有涨跌幅。
"""

from __future__ import annotations

import html as html_lib
from base64 import b64encode
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

from .utils import number_to_chinese
from .market import DisplayItem
from .bottom_chrome import FOOTER_H, FOOTER_W, footer_top, source_top, footer_left, chrome_height, source_band_h
from .market.display import source_footer

TEXT_PATH = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"
_FOOTER_PATH = Path(__file__).resolve().parent / "texture2d" / "footer.png"

PAGE_W = 900
# 行卡片素材自带 30px 透明边，可见区 30..869 —— 全页按这一列对齐
CONTENT_L = 30
CONTENT_W = 839
CONTENT_R = CONTENT_L + CONTENT_W
GAP = 12

# 漫画横幅：素材里的可见面板是 32..823（791 宽），等比放大到整列宽，左右沿才对得上
_TITLE_PANEL_L = 32
_TITLE_PANEL_W = 791
_TITLE_SCALE = CONTENT_W / _TITLE_PANEL_W
TITLE_W = round(850 * _TITLE_SCALE)
TITLE_H = round(400 * _TITLE_SCALE)
TITLE_LEFT = round(CONTENT_L - _TITLE_PANEL_L * _TITLE_SCALE, 1)
TITLE_Y = -31

IDX_Y = 336
IDX_CARD_W = 200
IDX_GAP = 13  # 4 张卡 + 3 个间隔 = 839
IDX_H = 128

FUND_Y = 476
FUND_H = 76
LEGEND_Y = 564
LEGEND_H = 36
SEP_Y = 612
SEP_H = 48
BARS_Y = 672
_SRC_FONT = 15
FOOT_PAD = chrome_height(font_size=_SRC_FONT)

BAR_H = 110
# 行底素材：红/绿/灰 = 涨跌（与「我的自选」同一套），金色 = 该行取数失败
BAR_ASSET_UP = "myup.png"
BAR_ASSET_DOWN = "mydown.png"
BAR_ASSET_FLAT = "myeq.png"
BAR_ASSET_FAILED = "myup_gold.png"

# 分时小图：右侧留出「占比 / 涨跌幅」两列
SPARK_W = 135.0
SPARK_H = 62.0
SPARK_LEFT = 440.0
SPARK_TOP = 24.0
WEIGHT_X = 620.0
PCT_X = 762.0

# 红涨绿跌（与「我的自选」一致）
SUB_UP = "rgb(213,102,102)"
SUB_FLAT = "rgb(240,240,240)"
SUB_DOWN = "rgb(175,231,170)"
PCT_TEXT = "#ffffff"  # 药丸是红/绿底，白字
PCT_FAILED = "#3A2A00"  # 金色药丸上白字只有 3:1，失败两字走深色
GOLD_TEXT = "#ffd278"  # 金色失败行上的说明文字
WEIGHT_TEXT = "#f0f0f0"
NAME_TEXT = "#ffffff"
META_TEXT = "#c5cce0"


@dataclass(frozen=True, slots=True)
class FundHoldingRow:
    """单只重仓股。``failed`` 为 True 时该行用金色行底 + 「失败」。"""

    code: str
    name: str
    weight: str
    price: float | None = None
    change_pct: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None
    provider: str | None = None
    failed: bool = False
    reason: str = ""


def fund_holdings_canvas_size(n: int) -> tuple[int, int]:
    """画布尺寸。"""
    return PAGE_W, BARS_Y + n * BAR_H + FOOT_PAD


def _e(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


@lru_cache(maxsize=16)
def _data_uri(path: str) -> str:
    """磁盘素材 → data URI。**直接读原始字节**，不要用 PIL 重新编码：
    不同 Pillow 版本的 PNG 编码器输出不一样，重新编码会让同一份素材在不同
    机器上生成不同的字节（CI 上按字节比对就会假失败）。"""
    file = Path(path)
    raw = file.read_bytes()
    suffix = file.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{b64encode(raw).decode('ascii')}"


def _bar_asset(change_pct: float) -> str:
    """涨跌行底素材。黄色只给失败行，不给正常行。"""
    if change_pct > 0:
        return BAR_ASSET_UP
    if change_pct < 0:
        return BAR_ASSET_DOWN
    return BAR_ASSET_FLAT


def _sub_color(change_pct: float) -> str:
    if change_pct > 0:
        return SUB_UP
    if change_pct < 0:
        return SUB_DOWN
    return SUB_FLAT


def _idx_colors(change_pct: float) -> tuple[str, str]:
    if change_pct >= 0:
        return "rgba(140,18,22,0.216)", "rgb(206,34,30)"
    return "rgba(59,140,18,0.216)", "rgb(36,206,30)"


def _idx_html(item: DisplayItem, n: int) -> str:
    left = CONTENT_L + (IDX_CARD_W + IDX_GAP) * n
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


def _row_title(row: FundHoldingRow) -> str:
    name = (row.name or row.code).strip()
    return name if name else row.code


def _bar_html(row: FundHoldingRow, y: int, spark_svg: str) -> str:
    """一行重仓股。取不到数值的行：金底 + 涨跌幅格写「失败」。"""
    title = _row_title(row)
    code = row.code.strip()
    weight = f"{row.weight}%" if row.weight else ""
    if row.failed:
        sub = f"({code})" + (f" 占比 {weight}" if weight else "")
        uri = _data_uri(str(TEXT_PATH / BAR_ASSET_FAILED))
        return (
            f'<div class="bar" style="top:{y}px;background-image:url({uri})">'
            f'<div class="bt">{_e(title)}</div>'
            f'<div class="st" style="color:{SUB_FLAT}">{_e(sub)}</div>'
            f'<div class="warn-inline">⚠ {_e(row.reason or "行情获取失败")}</div>'
            f'<div class="pct" style="color:{PCT_FAILED}">失败</div>'
            f"</div>"
        )

    p = float(row.change_pct) if row.change_pct is not None else 0.0
    e_money = number_to_chinese(row.amount) if row.amount is not None else "-"
    hs = row.turnover_rate if row.turnover_rate is not None else 0
    price = row.price if row.price is not None else "-"
    sub = f"({code}) 价 {price} · 换 {hs}% · 额 {e_money}"
    pct = f"+{p}%" if p >= 0 else f"{p}%"
    uri = _data_uri(str(TEXT_PATH / _bar_asset(p)))
    spark = f'<div class="spark">{spark_svg}</div>' if spark_svg else ""
    return (
        f'<div class="bar" style="top:{y}px;background-image:url({uri})">'
        f'<div class="bt">{_e(title)}</div>'
        f'<div class="st" style="color:{_sub_color(p)}">{_e(sub)}</div>'
        f"{spark}"
        f'<div class="wt">{_e(weight)}</div>'
        f'<div class="pct" style="color:{PCT_TEXT}">{_e(pct)}</div>'
        f"</div>"
    )


def _fund_strip(fund_name: str, fund_code: str, meta: str) -> str:
    return (
        f'<div class="fund" style="top:{FUND_Y}px;height:{FUND_H}px">'
        f'<div class="fname">{_e(fund_name)}<span class="fcode">({_e(fund_code)})</span></div>'
        f'<div class="fmeta">{_e(meta)}</div>'
        f"</div>"
    )


def _separator() -> str:
    """自选页那张 bar5 横幅写死了「添加自选」，基金页用不了，这里自己画一条分隔线。"""
    return (
        f'<div class="sep" style="top:{SEP_Y}px;height:{SEP_H}px">'
        f'<span class="sep-l"></span><span class="sep-d">◆</span>'
        f'<span class="sep-t">重仓股</span>'
        f'<span class="sep-d">◆</span><span class="sep-l"></span>'
        f"</div>"
    )


def build_fund_holdings_html(
    *,
    fund_name: str,
    fund_code: str,
    rows: list[FundHoldingRow],
    index_items: list[DisplayItem],
    title_num: str,
    sparklines: dict[str, str] | None = None,
    sources: tuple[str | None, ...] = (),
    meta: str = "",
) -> str:
    """基金持仓页。失败的重仓股用金色行底在自己那行标「失败」，不另挂横幅。"""
    n = len(rows)
    width, height = fund_holdings_canvas_size(n)
    title_uri = _data_uri(str(TEXT_PATH / f"title{title_num}.png"))
    footer_uri = _data_uri(str(_FOOTER_PATH))
    idx_html = "".join(_idx_html(item, i) for i, item in enumerate(index_items))

    bars: list[str] = []
    for i, row in enumerate(rows):
        spark = ""
        if sparklines:
            if row.code in sparklines:
                spark = sparklines[row.code]
            elif row.name in sparklines:
                spark = sparklines[row.name]
        bars.append(_bar_html(row, BARS_Y + i * BAR_H, spark))
    inner_bars = "".join(bars)

    used: list[str | None] = list(sources)
    used.extend(item.provider for item in index_items)
    used.extend(row.provider for row in rows)
    source_text = _e(source_footer(*used))
    src_top = source_top(height, font_size=_SRC_FONT)
    src_h = source_band_h(_SRC_FONT)
    foot_top = footer_top(height)
    foot_left = footer_left(width)

    legend_html = (
        f'<div class="legend" style="top:{LEGEND_Y}px;height:{LEGEND_H}px">'
        f"<b>图例</b>"
        f'<span><span class="chip c-wt">占</span> 基金持仓权重</span>'
        f'<span><span class="chip c-day">涨</span> 今日涨跌幅</span>'
        f'<span><span class="chip c-spark">折线</span> 当日分时</span>'
        f'<span><span class="chip c-fail">金</span> 该行取数失败</span>'
        f"</div>"
        if n
        else ""
    )
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
  position: absolute; left: {TITLE_LEFT}px; top: {TITLE_Y}px;
  width: {TITLE_W}px; height: {TITLE_H}px;
}}
.idx {{ position: absolute; width: {IDX_CARD_W}px; height: {IDX_H}px; }}
.ibox {{
  position: absolute; left: 15px; top: 13px; width: 170px; height: 114px;
  border-radius: 8px;
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
  font-size: 22px; font-weight: 630; color: rgba(255, 255, 255, 0.72); white-space: nowrap;
}}
.fund {{
  position: absolute; left: {CONTENT_L}px; width: {CONTENT_W}px;
  padding: 0 18px;
  background: linear-gradient(180deg, rgba(28,34,62,0.96), rgba(16,20,40,0.96));
  border: 1px solid rgba(212,160,23,0.28);
  border-radius: 12px;
  display: flex; align-items: center; justify-content: space-between;
}}
.fname {{ font-size: 24px; font-weight: 700; color: {GOLD_TEXT}; white-space: nowrap; }}
.fcode {{ margin-left: 10px; font-size: 18px; font-weight: 400; color: #9aa3b5; }}
.fmeta {{ font-size: 16px; color: {META_TEXT}; white-space: nowrap; }}
.legend {{
  position: absolute; left: {CONTENT_L}px; width: {CONTENT_W}px;
  display: flex; align-items: center; gap: 14px; padding: 0 18px;
  background: rgba(20, 24, 48, 0.86);
  border-radius: 10px; color: {META_TEXT}; font-size: 13px;
}}
.legend b {{ color: {GOLD_TEXT}; font-weight: 700; margin-right: 4px; }}
.chip {{
  display: inline-block; min-width: 28px; padding: 1px 6px; border-radius: 6px;
  font-size: 12px; font-weight: 700; text-align: center;
}}
/* 图例小标签用独立类名：.wt/.day 是持仓条的绝对定位类，同名会把图例挤到别处 */
.chip.c-wt {{ background: rgba(212, 160, 23, 0.16); color: {GOLD_TEXT}; }}
.chip.c-day {{ background: rgba(226, 90, 90, 0.22); color: #f0b4b4; }}
.chip.c-spark {{ background: rgba(212, 160, 23, 0.16); color: {GOLD_TEXT}; }}
.chip.c-fail {{ background: rgba(212, 160, 23, 0.28); color: {GOLD_TEXT}; }}
.sep {{
  position: absolute; left: {CONTENT_L}px; width: {CONTENT_W}px;
  display: flex; align-items: center;
}}
/* 分隔线的类名要避开持仓条用的 .st（那是绝对定位的副标题） */
.sep-l {{ flex: 1; height: 2px; background: rgba(255, 255, 255, 0.85); }}
.sep-d {{ margin: 0 10px; font-size: 15px; color: #ffffff; }}
.sep-t {{ font-size: 30px; font-weight: 800; color: #ffffff; white-space: nowrap; }}
.bar {{
  position: absolute; left: 0; width: {PAGE_W}px; height: {BAR_H}px;
  background-repeat: no-repeat; background-size: {PAGE_W}px {BAR_H}px;
}}
.bt {{
  position: absolute; left: 82px; top: 36px; transform: translateY(-50%);
  max-width: 330px; overflow: hidden; text-overflow: ellipsis;
  font-size: 26px; font-weight: 700; color: {NAME_TEXT}; white-space: nowrap;
}}
.st {{
  position: absolute; left: 82px; top: 74px; transform: translateY(-50%);
  max-width: 340px; overflow: hidden; text-overflow: ellipsis;
  font-size: 16px; font-weight: 400; white-space: nowrap;
}}
.spark {{
  position: absolute; left: {SPARK_LEFT:.1f}px; top: {SPARK_TOP:.0f}px;
  width: {SPARK_W:.1f}px; height: {SPARK_H:.0f}px;
}}
.spark svg {{ display: block; width: 100%; height: 100%; }}
.wt {{
  position: absolute; left: {WEIGHT_X:.0f}px; top: 55px; transform: translate(-50%, -50%);
  font-size: 22px; font-weight: 700; color: {WEIGHT_TEXT}; white-space: nowrap;
}}
.pct {{
  position: absolute; left: {PCT_X:.0f}px; top: 55px; transform: translate(-50%, -50%);
  font-size: 24px; font-weight: 700; color: {PCT_TEXT}; white-space: nowrap;
}}
.warn-inline {{
  position: absolute; left: {SPARK_LEFT:.0f}px; top: 55px;
  font-size: 19px; font-weight: 700; color: {GOLD_TEXT}; white-space: nowrap;
}}
.footer {{
  position: absolute; left: {foot_left}px; top: {foot_top}px;
  width: {FOOTER_W}px; height: {FOOTER_H}px;
}}
.src {{
  position: absolute; left: {CONTENT_L}px; top: {src_top}px;
  width: {CONTENT_W}px; height: {src_h}px; line-height: {src_h}px;
  font-size: {_SRC_FONT}px; color: rgba(210, 210, 210, 0.9);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
</style>
</head>
<body>
<div class="page">
  <img class="title" src="{title_uri}" width="{TITLE_W}" height="{TITLE_H}" />
  {idx_html}
  {_fund_strip(fund_name, fund_code, meta)}
  {legend_html}
  {_separator()}
  {inner_bars}
  <div class="src">{source_text}</div>
  <img class="footer" src="{footer_uri}" width="850" height="40" />
</div>
</body>
</html>
"""
