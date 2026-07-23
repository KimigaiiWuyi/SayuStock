"""分析模块出图：技术分析 / 股票卡片 / 自动选股 / 组合体检。"""

from __future__ import annotations

from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from .card import TradeCardData
from .screener import ScreenerResult
from .portfolio import PortfolioRiskReport
from .technical import TechnicalReport
from ..utils.utils import number_to_chinese
from ..stock_stockinfo.chart_base import (
    BG_COLOR,
    FG_COLOR,
    UP_COLOR,
    DOWN_COLOR,
    GRID_COLOR,
    _setup_mpl,
    _fig_to_image,
)


def _score_color(score: int) -> str:
    if score >= 65:
        return UP_COLOR
    if score <= 40:
        return DOWN_COLOR
    return "#f1c40f"


def _level_val(lv: dict[str, float | None], key: str) -> float | None:
    return lv[key] if key in lv else None


def _fin_val(fin: dict[str, Any], key: str) -> object:
    return fin[key] if key in fin else None


def _set3_colors(n: int) -> list[tuple[float, float, float, float]]:
    if n <= 0:
        return []
    cmap = plt.colormaps["Set3"]
    if n == 1:
        return [cmap(0.0)]
    return [cmap(i / (n - 1)) for i in range(n)]


def render_technical_image(report: TechnicalReport) -> Image.Image:
    _setup_mpl()
    fig, ax = plt.subplots(figsize=(12, 14), dpi=140)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    accent = _score_color(report.score)
    ax.text(
        0.5,
        0.94,
        f"{report.name}  ·  {report.code}",
        ha="center",
        va="center",
        fontsize=26,
        color=FG_COLOR,
        fontweight="bold",
    )
    ax.text(
        0.5,
        0.88,
        f"{report.period_label}技术分析",
        ha="center",
        va="center",
        fontsize=16,
        color="#aaaaaa",
    )

    ax.text(0.5, 0.72, str(report.score), ha="center", va="center", fontsize=72, color=accent, fontweight="bold")
    ax.text(0.5, 0.64, "技术分 / 100", ha="center", va="center", fontsize=14, color="#888888")

    boxes = [
        (0.08, 0.48, "趋势", report.trend),
        (0.52, 0.48, "动量", report.momentum),
        (0.08, 0.36, "量能", report.volume),
        (0.52, 0.36, "位置", report.position),
    ]
    for x, y, title, val in boxes:
        rect = FancyBboxPatch(
            (x, y),
            0.40,
            0.10,
            boxstyle="round,pad=0.01",
            facecolor="#121212",
            edgecolor=GRID_COLOR,
            linewidth=1.0,
            transform=ax.transAxes,
        )
        ax.add_patch(rect)
        ax.text(x + 0.03, y + 0.065, title, fontsize=12, color="#888888", transform=ax.transAxes)
        ax.text(x + 0.03, y + 0.025, val, fontsize=18, color=FG_COLOR, fontweight="bold", transform=ax.transAxes)

    y = 0.30
    ax.text(0.08, y, "关键位", fontsize=14, color="#f1c40f", fontweight="bold")
    y -= 0.04
    lv = report.levels
    ax.text(
        0.08,
        y,
        f"支撑 {_fmt(_level_val(lv, 'support'))}   压力 {_fmt(_level_val(lv, 'resistance'))}   "
        f"MA20 {_fmt(_level_val(lv, 'ma20'))}   MA60 {_fmt(_level_val(lv, 'ma60'))}",
        fontsize=13,
        color=FG_COLOR,
    )
    y -= 0.045
    ax.text(
        0.08,
        y,
        f"参考止损 {_fmt(_level_val(lv, 'stop_ref'))}   参考目标 {_fmt(_level_val(lv, 'target_ref'))}",
        fontsize=12,
        color="#bbbbbb",
    )

    y -= 0.06
    ax.text(0.08, y, "信号", fontsize=14, color=UP_COLOR, fontweight="bold")
    y -= 0.035
    for s in (report.signals or ["暂无"])[:6]:
        ax.text(0.10, y, f"· {s}", fontsize=12, color=FG_COLOR)
        y -= 0.032

    y -= 0.02
    ax.text(0.08, y, "风险", fontsize=14, color=DOWN_COLOR, fontweight="bold")
    y -= 0.035
    for s in (report.risk_flags or ["暂无"])[:5]:
        ax.text(0.10, y, f"· {s}", fontsize=12, color="#ffcccc" if s != "暂无" else FG_COLOR)
        y -= 0.032

    y -= 0.02
    ax.text(0.08, y, "摘要", fontsize=14, color="#f1c40f", fontweight="bold")
    y -= 0.04
    ax.text(0.08, y, report.summary, fontsize=12, color="#dddddd", wrap=True)

    fig.text(0.02, 0.01, "数据来源：东方财富 | SayuStock 技术分析", color=FG_COLOR, fontsize=9, alpha=0.6)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.98, bottom=0.04)
    return _fig_to_image(fig, dpi=140)


def render_card_image(card: TradeCardData) -> Image.Image:
    _setup_mpl()
    fig, ax = plt.subplots(figsize=(11, 15), dpi=140)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    pct = card.pct if card.pct is not None else 0.0
    accent = UP_COLOR if pct >= 0 else DOWN_COLOR
    price_s = _fmt(card.price)
    pct_s = f"{pct:+.2f}%" if card.pct is not None else "—"

    ax.text(0.5, 0.95, card.name, ha="center", fontsize=28, color=FG_COLOR, fontweight="bold")
    ax.text(0.5, 0.905, card.code, ha="center", fontsize=16, color="#aaaaaa")
    ax.text(0.5, 0.84, f"{price_s}   {pct_s}", ha="center", fontsize=36, color=accent, fontweight="bold")

    meta = [
        f"开盘 {_fmt(card.open_price)}",
        f"昨收 {_fmt(card.prev_close)}",
        f"最高 {_fmt(card.high)}",
        f"最低 {_fmt(card.low)}",
        f"换手 {_fmt(card.turnover)}%",
    ]
    if card.amount is not None:
        meta.append(f"成交额 {number_to_chinese(float(card.amount))}")
    ax.text(0.5, 0.78, "  |  ".join(meta), ha="center", fontsize=11, color="#cccccc")

    ind_line = f"行业 {card.industry}"
    if card.industry_pct is not None:
        ind_line += f"  ({card.industry_pct:+.2f}%)"
    ax.text(0.5, 0.735, ind_line, ha="center", fontsize=13, color="#f1c40f")

    mv_s = "—"
    if card.mv is not None:
        mv_s = f"{card.mv / 1e8:.1f}亿"
    ax.text(
        0.5,
        0.69,
        f"PE {_fmt(card.pe)}   PB {_fmt(card.pb)}   总市值 {mv_s}",
        ha="center",
        fontsize=13,
        color=FG_COLOR,
    )

    rect = FancyBboxPatch(
        (0.06, 0.34),
        0.88,
        0.30,
        boxstyle="round,pad=0.01",
        facecolor="#101010",
        edgecolor=GRID_COLOR,
        transform=ax.transAxes,
    )
    ax.add_patch(rect)
    tech = card.technical
    if tech:
        ax.text(
            0.10,
            0.60,
            f"技术面  {tech.score}/100",
            fontsize=16,
            color=_score_color(tech.score),
            fontweight="bold",
            transform=ax.transAxes,
        )
        ax.text(
            0.10,
            0.555,
            f"{tech.trend} · {tech.momentum} · {tech.volume} · {tech.position}",
            fontsize=13,
            color=FG_COLOR,
            transform=ax.transAxes,
        )
        lv = tech.levels
        ax.text(
            0.10,
            0.51,
            f"支撑 {_fmt(_level_val(lv, 'support'))}  压力 {_fmt(_level_val(lv, 'resistance'))}  "
            f"MA20 {_fmt(_level_val(lv, 'ma20'))}",
            fontsize=12,
            color="#cccccc",
            transform=ax.transAxes,
        )
        sig = "；".join((tech.signals or [])[:3]) or "—"
        risk = "；".join((tech.risk_flags or [])[:2]) or "—"
        ax.text(0.10, 0.46, f"信号: {sig}", fontsize=11, color=UP_COLOR, transform=ax.transAxes)
        ax.text(0.10, 0.415, f"风险: {risk}", fontsize=11, color=DOWN_COLOR, transform=ax.transAxes)
        ax.text(0.10, 0.37, tech.summary[:80], fontsize=11, color="#aaaaaa", transform=ax.transAxes)
    else:
        ax.text(0.5, 0.49, "技术面数据暂缺", ha="center", fontsize=14, color="#888888", transform=ax.transAxes)

    fin = card.finance
    ax.text(0.08, 0.28, "财务快照", fontsize=15, color="#f1c40f", fontweight="bold")
    fin_items: Sequence[tuple[str, object, str]] = (
        ("ROE", _fin_val(fin, "roe"), "%"),
        ("营收同比", _fin_val(fin, "revenue_yoy"), "%"),
        ("净利同比", _fin_val(fin, "profit_yoy"), "%"),
        ("毛利率", _fin_val(fin, "gross_margin"), "%"),
        ("净利率", _fin_val(fin, "net_margin"), "%"),
        ("负债率", _fin_val(fin, "debt_ratio"), "%"),
        ("EPS", _fin_val(fin, "eps"), ""),
        ("BPS", _fin_val(fin, "bps"), ""),
    )
    y = 0.23
    line_parts: list[str] = []
    for label, val, suf in fin_items:
        if val is None:
            continue
        line_parts.append(f"{label} {_fmt(val)}{suf}")
    if not line_parts:
        ax.text(0.08, y, "暂无财务数据", fontsize=12, color="#888888")
    else:
        mid = (len(line_parts) + 1) // 2
        ax.text(0.08, y, "   ".join(line_parts[:mid]), fontsize=12, color=FG_COLOR)
        if mid < len(line_parts):
            ax.text(0.08, y - 0.04, "   ".join(line_parts[mid:]), fontsize=12, color=FG_COLOR)
    report_date = _fin_val(fin, "report_date")
    if report_date:
        ax.text(0.08, 0.12, f"报告期 {report_date}", fontsize=11, color="#888888")

    fig.text(0.02, 0.01, "数据来源：东方财富 | SayuStock 股票卡片", color=FG_COLOR, fontsize=9, alpha=0.6)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.98, bottom=0.04)
    return _fig_to_image(fig, dpi=140)


def render_screener_image(result: ScreenerResult) -> Image.Image:
    _setup_mpl()
    df = result.df
    n = max(len(df), 1)
    fig_h = min(16, 4 + n * 0.45)
    fig, ax = plt.subplots(figsize=(14, fig_h), dpi=140)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.axis("off")

    ax.set_title(
        f"自动选股 · {result.scope}",
        color=FG_COLOR,
        fontsize=20,
        fontweight="bold",
        pad=16,
    )
    filt = "  ".join(result.filters_desc) if result.filters_desc else "（仅板块范围）"
    ax.text(
        0.5,
        0.96,
        f"条件: {filt}   池 {result.total_pool} → 命中 {result.matched}  展示 {result.shown}",
        transform=ax.transAxes,
        ha="center",
        fontsize=12,
        color="#aaaaaa",
    )

    if df.empty:
        ax.text(0.5, 0.5, "无匹配结果", transform=ax.transAxes, ha="center", fontsize=18, color="#888888")
    else:
        headers = ["代码", "名称", "现价", "涨跌%", "市值(亿)", "PE", "换手%", "量比", "行业"]
        col_x = [0.04, 0.14, 0.30, 0.40, 0.50, 0.62, 0.72, 0.82, 0.90]
        y = 0.88
        for i, h in enumerate(headers):
            ax.text(col_x[i], y, h, transform=ax.transAxes, fontsize=11, color="#f1c40f", fontweight="bold")
        y -= 0.04
        ax.plot([0.03, 0.97], [y + 0.02, y + 0.02], transform=ax.transAxes, color=GRID_COLOR, lw=0.6)
        for _, row in df.iterrows():
            pct = row["pct"] if "pct" in row.index else None
            color = UP_COLOR if isinstance(pct, (int, float)) and pct >= 0 else DOWN_COLOR
            mv_yi = row["mv_yi"] if "mv_yi" in row.index else None
            vals = [
                str(row["code"] if "code" in row.index else ""),
                str(row["name"] if "name" in row.index else "")[:8],
                _fmt(row["price"] if "price" in row.index else None),
                f"{pct:+.2f}" if isinstance(pct, (int, float)) else "—",
                _fmt(mv_yi, 1) if mv_yi is not None else "—",
                _fmt(row["pe"] if "pe" in row.index else None, 1),
                _fmt(row["turnover"] if "turnover" in row.index else None, 1),
                _fmt(row["vol_ratio"] if "vol_ratio" in row.index else None, 2),
                str(row["industry"] if "industry" in row.index else "")[:6],
            ]
            for i, v in enumerate(vals):
                c = color if i == 3 else FG_COLOR
                ax.text(col_x[i], y, v, transform=ax.transAxes, fontsize=11, color=c)
            y -= 0.038
            if y < 0.06:
                break

    fig.text(0.02, 0.01, "数据来源：东方财富 | SayuStock 自动选股", color=FG_COLOR, fontsize=9, alpha=0.6)
    fig.subplots_adjust(left=0.03, right=0.98, top=0.90, bottom=0.05)
    return _fig_to_image(fig, dpi=140)


def render_portfolio_image(report: PortfolioRiskReport) -> Image.Image:
    _setup_mpl()
    fig = plt.figure(figsize=(13, 11), dpi=140)
    fig.patch.set_facecolor(BG_COLOR)

    ax_pie = fig.add_axes([0.08, 0.38, 0.40, 0.48])
    ax_bar = fig.add_axes([0.55, 0.38, 0.40, 0.48])
    ax_txt = fig.add_axes([0.08, 0.05, 0.84, 0.28])
    for a in (ax_pie, ax_bar, ax_txt):
        a.set_facecolor(BG_COLOR)

    labels = list(report.industry_weights.keys())
    sizes = [report.industry_weights[k] * 100 for k in labels]
    colors = _set3_colors(len(sizes))
    if sizes:
        _wedges, _texts, autotexts = ax_pie.pie(
            sizes,
            labels=None,
            autopct=lambda p: f"{p:.0f}%" if p >= 5 else "",
            colors=colors,
            textprops={"color": FG_COLOR, "fontsize": 10},
            startangle=90,
        )
        for t in autotexts:
            t.set_color("#111111")
            t.set_fontsize(9)
        ax_pie.legend(
            labels,
            loc="upper left",
            bbox_to_anchor=(-0.1, 1.0),
            fontsize=9,
            facecolor=BG_COLOR,
            edgecolor=GRID_COLOR,
            labelcolor=FG_COLOR,
        )
    ax_pie.set_title("行业权重", color=FG_COLOR, fontsize=14)

    y_pos = range(len(labels))
    ax_bar.barh(list(y_pos), sizes, color=UP_COLOR, alpha=0.75)
    ax_bar.set_yticks(list(y_pos))
    ax_bar.set_yticklabels(labels, color=FG_COLOR, fontsize=10)
    ax_bar.invert_yaxis()
    ax_bar.set_xlabel("权重 %", color=FG_COLOR)
    ax_bar.tick_params(colors=FG_COLOR)
    ax_bar.spines["bottom"].set_color(GRID_COLOR)
    ax_bar.spines["left"].set_color(GRID_COLOR)
    ax_bar.spines["top"].set_visible(False)
    ax_bar.spines["right"].set_visible(False)
    ax_bar.set_title("行业权重条", color=FG_COLOR, fontsize=14)

    level_colors = {
        "分散": DOWN_COLOR,
        "适中": "#f1c40f",
        "集中": UP_COLOR,
        "极集中": "#ff4444",
    }
    level_color = level_colors[report.risk_level] if report.risk_level in level_colors else FG_COLOR

    ax_txt.axis("off")
    ax_txt.text(
        0.0,
        0.90,
        f"组合行业集中度  【{report.risk_level}】",
        fontsize=18,
        color=level_color,
        fontweight="bold",
    )
    ax_txt.text(
        0.0,
        0.70,
        f"HHI {report.hhi:.3f}   有效行业数 {report.effective_n:.1f}   "
        f"Top1 {report.top1_name} {report.top1_weight * 100:.1f}%   Top3 {report.top3_weight * 100:.1f}%",
        fontsize=12,
        color=FG_COLOR,
    )
    y = 0.50
    for msg in report.messages:
        ax_txt.text(0.0, y, f"· {msg}", fontsize=12, color="#dddddd")
        y -= 0.14

    fig.text(0.02, 0.01, "数据来源：东方财富 | SayuStock 组合体检（等权）", color=FG_COLOR, fontsize=9, alpha=0.6)
    return _fig_to_image(fig, dpi=140)


def _fmt(v: object, d: int = 2) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if isinstance(v, bool):
        return "—"
    if isinstance(v, (int, float)):
        return f"{float(v):.{d}f}"
    return str(v)
