"""模拟盘持仓简图 HTML（pytakumi / render_html_to_bytes），风格对齐「我的自选」。"""

from __future__ import annotations

import html as html_lib
from base64 import b64encode
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

from .market import DisplayItem

TEXT_PATH = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"
_FOOTER_PATH = Path(__file__).resolve().parent / "texture2d" / "footer.png"

BAR_H = 110
TITLE_X = 25
TITLE_Y = -31
IDX_Y = 308
IDX_X0 = 50
SUM_Y = 448
SUM_H = 208
LEGEND_GAP = 10
LEGEND_H = 36
BAR5_GAP = 8
BAR5_H = 90
BARS_GAP = 6
FOOT_PAD = 60
SPARK_W = 120.0
SPARK_H = 52.0
SPARK_LEFT = 400.0
SPARK_TOP = 28.0


@dataclass(frozen=True)
class HoldingBarRow:
    """单只持仓条（简图用，不含流水）。"""

    code: str
    name: str
    qty: int
    avg_cost: float
    current_price: float
    day_change_pct: float | None
    unrealized_pnl: float
    unrealized_pnl_pct: float
    market_value: float


def paper_holdings_canvas_size(n: int) -> tuple[int, int]:
    rows = max(n, 1)
    legend_y = SUM_Y + SUM_H + LEGEND_GAP
    bar5_y = legend_y + LEGEND_H + BAR5_GAP
    bars_y = bar5_y + BAR5_H + BARS_GAP
    return 900, bars_y + rows * BAR_H + FOOT_PAD


def _e(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


@lru_cache(maxsize=16)
def _data_uri(path: str) -> str:
    file = Path(path)
    raw = file.read_bytes()
    suffix = file.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{b64encode(raw).decode('ascii')}"


def _cn_money(num: float) -> str:
    sign = "-" if num < 0 else ""
    n = abs(num)
    if n >= 100_000_000:
        text = f"{n / 100_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{text}亿"
    if n >= 10_000:
        text = f"{n / 10_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{text}万"
    return f"{sign}{n:.0f}"


def _cn_signed(num: float) -> str:
    if num > 0:
        return f"+{_cn_money(num)}"
    return _cn_money(num)


def _pct(value: float) -> str:
    return f"{value:+.2f}%"


def _fmt_px(price: float) -> str:
    if abs(price) >= 10:
        return f"{price:.2f}"
    return f"{price:.3f}"


def _paint(row: HoldingBarRow) -> float:
    if row.day_change_pct is None:
        return row.unrealized_pnl_pct
    return row.day_change_pct


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


def _tone(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "eq"


def _idx_html(item: DisplayItem, n: int) -> str:
    left = IDX_X0 + 200 * n
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


def _donut_svg(pos_pct: float) -> str:
    share = max(0.0, min(100.0, pos_pct))
    return (
        '<svg class="donut-svg" viewBox="0 0 36 36" aria-hidden="true">'
        '<circle cx="18" cy="18" r="15.9155" fill="none" stroke="#2c3348" stroke-width="4"/>'
        f'<circle cx="18" cy="18" r="15.9155" fill="none" stroke="#d4a017" stroke-width="4" '
        f'stroke-dasharray="{share:.2f} {100.0 - share:.2f}" stroke-dashoffset="25"/>'
        "</svg>"
    )


def _tile(label: str, value: str, sub: str, tone: str) -> str:
    extra = f" {tone}" if tone != "eq" and label in {"浮盈", "累计"} else ""
    return (
        f'<div class="tile">'
        f'<div class="tl">{_e(label)}</div>'
        f'<div class="tv{extra}">{_e(value)}</div>'
        f'<div class="ts{extra}">{_e(sub)}</div>'
        f"</div>"
    )


def _bar_html(row: HoldingBarRow, y: int, spark_svg: str, equity: float) -> str:
    paint = _paint(row)
    weight = (row.market_value / equity * 100.0) if equity else 0.0
    title = (row.name or row.code).split(" (")[0]
    sub = f"({row.code}) {row.qty}股  成本 {_fmt_px(row.avg_cost)}  现价 {_fmt_px(row.current_price)}  占{weight:.1f}%"
    if row.day_change_pct is None:
        day_label = "今 —"
        day_tone = "eq"
    else:
        day_label = f"今{_pct(row.day_change_pct)}"
        day_tone = _tone(row.day_change_pct)
    hold_label = f"持{_pct(row.unrealized_pnl_pct)}"
    uri = _data_uri(str(TEXT_PATH / _bar_asset(paint)))
    spark = f'<div class="spark">{spark_svg}</div>' if spark_svg else ""
    return (
        f'<div class="bar" style="top:{y}px;background-image:url({uri})">'
        f'<div class="bt">{_e(title)}</div>'
        f'<div class="st" style="color:{_sub_color(paint)}">{_e(sub)}</div>'
        f"{spark}"
        f'<div class="hold {_tone(row.unrealized_pnl_pct)}">{_e(hold_label)}</div>'
        f'<div class="day {day_tone}">{_e(day_label)}</div>'
        f"</div>"
    )


def build_paper_holdings_html(
    *,
    account_name: str,
    strategy_id: str,
    enabled: bool,
    cash: float,
    initial_cash: float,
    holdings: list[HoldingBarRow],
    index_items: list[DisplayItem],
    title_num: str,
    sparklines: dict[str, str] | None = None,
) -> str:
    n = len(holdings)
    width, height = paper_holdings_canvas_size(n)
    pos_value = sum(h.market_value for h in holdings)
    total_equity = cash + pos_value
    total_unreal = sum(h.unrealized_pnl for h in holdings)
    total_unreal_pct = (total_unreal / pos_value * 100.0) if pos_value else 0.0
    total_pnl = total_equity - initial_cash
    total_pnl_pct = (total_pnl / initial_cash * 100.0) if initial_cash else 0.0
    pos_pct = (pos_value / total_equity * 100.0) if total_equity else 0.0
    cash_pct = 100.0 - pos_pct if total_equity else 100.0
    cash_share = (cash / total_equity * 100.0) if total_equity else 100.0

    title_uri = _data_uri(str(TEXT_PATH / f"title{title_num}.png"))
    bar5_uri = _data_uri(str(TEXT_PATH / "bar5.png"))
    footer_uri = _data_uri(str(_FOOTER_PATH))
    idx_html = "".join(_idx_html(item, i) for i, item in enumerate(index_items))

    legend_y = SUM_Y + SUM_H + LEGEND_GAP
    bar5_y = legend_y + LEGEND_H + BAR5_GAP
    bars_y = bar5_y + BAR5_H + BARS_GAP

    status = "运行中" if enabled else "已停用"
    status_cls = "on" if enabled else "off"
    tiles = "".join(
        (
            _tile("总资产", _cn_money(total_equity), f"本金 {_cn_money(initial_cash)}", "eq"),
            _tile("现金", _cn_money(cash), f"{cash_share:.1f}%", "eq"),
            _tile("持仓市值", _cn_money(pos_value), f"{pos_pct:.1f}% · {n} 只", "eq"),
            _tile("浮盈", _cn_signed(total_unreal), _pct(total_unreal_pct), _tone(total_unreal)),
            _tile("累计", _cn_signed(total_pnl), _pct(total_pnl_pct), _tone(total_pnl)),
        )
    )

    if holdings:
        bars: list[str] = []
        for i, row in enumerate(holdings):
            spark = ""
            if sparklines:
                if row.code in sparklines:
                    spark = sparklines[row.code]
            bars.append(_bar_html(row, bars_y + i * BAR_H, spark, total_equity))
        inner_bars = "".join(bars)
    else:
        inner_bars = f'<div class="empty" style="top:{bars_y}px">当前无持仓 · 资金都在现金</div>'

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
  position: absolute; left: {TITLE_X}px; top: {TITLE_Y}px;
  width: 850px; height: 400px;
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
.sum {{
  position: absolute; left: 25px; top: {SUM_Y}px;
  width: 850px; height: {SUM_H}px;
  padding: 14px 16px 12px;
  background: linear-gradient(180deg, rgba(28,34,62,0.96), rgba(16,20,40,0.96));
  border: 1px solid rgba(212,160,23,0.28);
  border-radius: 14px;
}}
.sum-head {{
  display: flex; align-items: center; justify-content: space-between;
  height: 28px; margin-bottom: 10px;
}}
.sum-title {{
  font-size: 22px; font-weight: 700; color: #ffd278; letter-spacing: 0.5px;
}}
.sum-meta {{
  display: flex; align-items: center; gap: 10px;
  font-size: 14px; color: #9aa3b5;
}}
.badge {{
  padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 700;
}}
.badge.on {{ background: rgba(46, 184, 92, 0.22); color: #7ee0a3; }}
.badge.off {{ background: rgba(180, 70, 70, 0.22); color: #f0a0a0; }}
.sum-body {{ display: flex; align-items: center; gap: 14px; height: 118px; }}
.donut-wrap {{
  position: relative; width: 112px; height: 112px; flex: 0 0 112px;
}}
.donut-svg {{ width: 112px; height: 112px; display: block; }}
.donut-lab {{
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  text-align: center; color: #ffd278;
}}
.donut-pct {{ font-size: 20px; font-weight: 800; line-height: 1.1; }}
.donut-cap {{ font-size: 11px; color: #9aa3b5; margin-top: 2px; }}
.tiles {{
  flex: 1; display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px;
}}
.tile {{
  height: 118px; padding: 12px 10px 10px;
  background: rgba(7, 9, 27, 0.45);
  border-radius: 10px; border: 1px solid rgba(255,255,255,0.04);
}}
.tl {{ font-size: 13px; color: #8b95a8; margin-bottom: 8px; }}
.tv {{
  font-size: 22px; font-weight: 800; color: #f4f6fb;
  font-variant-numeric: tabular-nums; line-height: 1.15;
}}
.ts {{
  margin-top: 8px; font-size: 13px; color: #9aa3b5;
  font-variant-numeric: tabular-nums;
}}
.tv.up, .ts.up {{ color: #e06a6a; }}
.tv.down, .ts.down {{ color: #6fd67a; }}
.alloc {{
  display: flex; align-items: center; gap: 10px; margin-top: 8px; height: 18px;
}}
.alloc-track {{
  flex: 1; height: 8px; border-radius: 99px; overflow: hidden; background: #2c3348;
}}
.alloc-pos {{ height: 8px; background: linear-gradient(90deg, #c9a227, #e6c25a); }}
.alloc-cap {{ font-size: 12px; color: #9aa3b5; white-space: nowrap; }}
.legend {{
  position: absolute; left: 25px; top: {legend_y}px;
  width: 850px; height: {LEGEND_H}px;
  display: flex; align-items: center; gap: 16px;
  padding: 0 16px;
  background: rgba(20, 24, 48, 0.86);
  border-radius: 10px; color: #c5cce0; font-size: 13px;
}}
.legend b {{ color: #ffd278; font-weight: 700; margin-right: 4px; }}
.chip {{
  display: inline-block; min-width: 28px; padding: 1px 6px; border-radius: 6px;
  font-size: 12px; font-weight: 700; text-align: center;
}}
.chip.day {{ background: rgba(226, 90, 90, 0.22); color: #f0b4b4; }}
.chip.hold {{ background: rgba(255, 255, 255, 0.08); color: #e8eaf2; }}
.chip.spark {{ background: rgba(212, 160, 23, 0.16); color: #ffd278; }}
.bar5 {{
  position: absolute; left: 25px; top: {bar5_y}px;
  width: 850px; height: 90px;
}}
.bar {{
  position: absolute; left: 0; width: 900px; height: 110px;
  background-repeat: no-repeat; background-size: 900px 110px;
}}
.bt {{
  position: absolute; left: 82px; top: 40px; transform: translateY(-50%);
  max-width: 300px; overflow: hidden; text-overflow: ellipsis;
  font-size: 28px; font-weight: 700; color: #ffffff; white-space: nowrap;
}}
.st {{
  position: absolute; left: 82px; top: 76px; transform: translateY(-50%);
  max-width: 310px; overflow: hidden; text-overflow: ellipsis;
  font-size: 15px; font-weight: 400; white-space: nowrap;
}}
.spark {{
  position: absolute; left: {SPARK_LEFT:.1f}px; top: {SPARK_TOP:.0f}px;
  width: {SPARK_W:.1f}px; height: {SPARK_H:.0f}px;
}}
.spark svg {{ display: block; width: 100%; height: 100%; }}
.hold {{
  position: absolute; left: 600px; top: 55px; transform: translate(-50%, -50%);
  font-size: 22px; font-weight: 700; color: #e8eaf2; white-space: nowrap;
}}
.day {{
  position: absolute; left: 758px; top: 55px; transform: translate(-50%, -50%);
  font-size: 22px; font-weight: 700; color: #ffffff; white-space: nowrap;
}}
.hold.up {{ color: #f3b0b0; }}
.hold.down {{ color: #b8e8b4; }}
.empty {{
  position: absolute; left: 25px; width: 850px; height: 90px;
  display: flex; align-items: center; justify-content: center;
  background: rgba(20, 24, 48, 0.8); border-radius: 10px;
  color: #9aa3b5; font-size: 24px;
}}
.footer {{
  position: absolute; left: 25px; top: {height - 55}px;
  width: 850px; height: 40px;
}}
</style>
</head>
<body>
<div class="page">
  <img class="title" src="{title_uri}" width="850" height="400" />
  {idx_html}
  <div class="sum">
    <div class="sum-head">
      <div class="sum-title">模拟盘 · {_e(account_name)}</div>
      <div class="sum-meta">
        <span class="badge {status_cls}">{status}</span>
        <span>策略 {_e(strategy_id)}</span>
        <span>持仓 {n} 只</span>
      </div>
    </div>
    <div class="sum-body">
      <div class="donut-wrap">
        {_donut_svg(pos_pct)}
        <div class="donut-lab">
          <div class="donut-pct">{pos_pct:.0f}%</div>
          <div class="donut-cap">仓位</div>
        </div>
      </div>
      <div class="tiles">{tiles}</div>
    </div>
    <div class="alloc">
      <div class="alloc-track"><div class="alloc-pos" style="width:{pos_pct:.2f}%"></div></div>
      <div class="alloc-cap">持仓 {_cn_money(pos_value)} · {pos_pct:.1f}% 现金 {_cn_money(cash)} · {cash_pct:.1f}%</div>
    </div>
  </div>
  <div class="legend">
    <b>图例</b>
    <span><span class="chip day">今</span> 今日涨跌（相对昨收）</span>
    <span><span class="chip hold">持</span> 持仓收益（相对成本）</span>
    <span><span class="chip spark">折线</span> 当日分时</span>
  </div>
  <img class="bar5" src="{bar5_uri}" width="850" height="90" />
  {inner_bars}
  <img class="footer" src="{footer_uri}" width="850" height="40" />
</div>
</body>
</html>
"""
