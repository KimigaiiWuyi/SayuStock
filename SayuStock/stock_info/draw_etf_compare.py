"""申赎篮子对比图和 AI 文本输出。"""

from __future__ import annotations

from math import cos, sin, tau, ceil
from dataclasses import dataclass
from collections.abc import Sequence

from gsuid_core.ai_core.trigger_bridge import ai_return

from ..render import RenderError, render_html
from .etf_compare import (
    UNKNOWN_INDUSTRY,
    PREVIEW_STOCK_LIMIT,
    FundValue,
    TargetStock,
    BasketResult,
    SearchResult,
    fund_theme,
    shares_overlap,
    comparison_stocks,
)
from ..utils.warn_box import WARN_CSS, Failure, failure_lines, warn_box_html, warn_box_height
from ..render.templates import render_template
from ..utils.market.adapters.etf_basket import HOLDER_PAGE_SIZE

WIDTH = 800
_INDUSTRIES_PER_PIE = 5
_COLORS = (
    "#43c2a1",
    "#72aaf5",
    "#f0af6c",
    "#b793ee",
    "#ed8c9e",
    "#58c4db",
    "#e3cc72",
    "#8ebf73",
    "#d99478",
    "#8899e6",
    "#eb7779",
    "#74b7b2",
    "#c4a5db",
    "#e7c694",
    "#96c9d7",
    "#91c7a8",
    "#bd8f91",
    "#92aeda",
    "#b8cf91",
    "#dba5c7",
)
_OTHER_INDUSTRIES = "其余二级行业"
_BASIS = (
    "计价股票指申赎清单里股数大于 0、用当日报价估值的股票。单股市值为股数乘以当日报价，"
    "权重分母是全部计价股票市值之和，篮子现金和零股数项不计入。"
    "按整篮分批取价，任一所需的当日报价缺失时，该 ETF 暂不出权重。"
)
_SOURCE = "申赎清单：沪深交易所 · 行情：腾讯 · 行业：申万二级"


@dataclass(frozen=True, slots=True)
class _Theme:
    name: str
    range_text: str
    caption: str
    index_text: str
    start: float
    width: float


@dataclass(frozen=True, slots=True)
class _PieSlice:
    label: str
    percent: float
    color: str
    path: str


def _warning(failures: Sequence[Failure]) -> tuple[str, int]:
    lines = failure_lines(failures, per_line=2)
    height = warn_box_height(len(lines))
    return warn_box_html(title="本次未纳入的项目", lines=lines, top=0, left=0, width=WIDTH - 64, height=height), height


def _quote_window(funds: Sequence[FundValue]) -> str:
    if not funds:
        return ""
    first = min(fund.first_quote for fund in funds)
    last = max(fund.last_quote for fund in funds)
    return f"{first:%Y-%m-%d} · 报价时间 {first:%H:%M}–{last:%H:%M}（北京时间）"


def _theme_rows(result: SearchResult) -> tuple[list[_Theme], int]:
    by_code = {fund.code: fund_theme(fund.name) for fund in result.candidates}
    groups: dict[str, list[FundValue]] = {}
    totals: dict[str, int] = {}
    for theme in by_code.values():
        totals[theme] = totals.setdefault(theme, 0) + 1
    for fund in result.preview.funds:
        groups.setdefault(by_code[fund.code], []).append(fund)
    target = result.stock.code
    maximum = max((fund.weight(target) for fund in result.preview.funds), default=0.0)
    axis = max(5, ceil(maximum / 5) * 5)
    display: list[_Theme] = []
    for name in sorted(totals, key=lambda theme: theme not in groups):
        if name not in groups:
            display.append(_Theme(name, "", f"{totals[name]} 只披露候选", "", 0, 0))
            continue
        funds = groups[name]
        ordered = sorted(funds, key=lambda fund: -fund.weight(target))
        low, high = ordered[-1].weight(target), ordered[0].weight(target)
        percent = f"{high:.2f}%" if len(funds) == 1 else f"{low:.2f}%–{high:.2f}%"
        lead = ordered[0]
        indices = list(dict.fromkeys(fund.index_name for fund in funds if fund.index_name))
        display.append(
            _Theme(
                name,
                percent,
                f"{totals[name]} 只披露候选 · 本轮核验 {len(funds)} 只 · {lead.name} {lead.code}",
                "、".join(indices) or "暂无",
                100 * low / axis,
                100 * (high - low) / axis,
            )
        )
    return display, axis


def _industry_breakdown(fund: FundValue) -> list[tuple[str, float]]:
    known = sorted(
        ((label, weight) for label, weight in fund.industries.items() if label != UNKNOWN_INDUSTRY),
        key=lambda item: (-item[1], item[0]),
    )
    visible = known[:_INDUSTRIES_PER_PIE]
    remainder = sum(weight for _, weight in known[_INDUSTRIES_PER_PIE:])
    unknown = fund.industry_weight(UNKNOWN_INDUSTRY)
    if remainder > 0:
        visible.append((_OTHER_INDUSTRIES, remainder))
    if unknown > 0:
        visible.append((UNKNOWN_INDUSTRY, unknown))
    return visible


def _pie_slices(weights: Sequence[tuple[str, float]], colors: dict[str, str]) -> list[_PieSlice]:
    slices: list[_PieSlice] = []
    offset = 0.0
    for label, weight in weights:
        if weight <= 1e-9:
            continue
        start, end = offset * tau / 100, (offset + weight) * tau / 100
        path = (
            f"M60,60 L{60 + 54 * sin(start):.3f},{60 - 54 * cos(start):.3f} "
            f"A54,54 0 {int(weight > 50)} 1 {60 + 54 * sin(end):.3f},{60 - 54 * cos(end):.3f} Z"
        )
        slices.append(_PieSlice(label, weight, colors[label], path))
        offset += weight
    return slices


def _industry_pies(funds: Sequence[FundValue]) -> list[list[_PieSlice]]:
    breakdowns = [_industry_breakdown(fund) for fund in funds]
    labels = list(
        dict.fromkeys(
            label
            for breakdown in breakdowns
            for label, _ in breakdown
            if label not in (_OTHER_INDUSTRIES, UNKNOWN_INDUSTRY)
        )
    )
    colors: dict[str, str] = dict(zip(labels, _COLORS[: len(labels)], strict=True))
    colors.update({_OTHER_INDUSTRIES: "#647889", UNKNOWN_INDUSTRY: "#bdc5ce"})
    return [_pie_slices(breakdown, colors) for breakdown in breakdowns]


def _compare_view(
    result: BasketResult, target: TargetStock | None
) -> tuple[list[FundValue], list[tuple[TargetStock, list[str | None], float]]]:
    rows = comparison_stocks(result.funds, target)
    focus = rows[0].code
    funds = sorted(result.funds, key=lambda fund: -fund.weight(focus))
    table: list[tuple[TargetStock, list[str | None], float]] = []
    for stock in rows:
        weights = [fund.weight(stock.code) for fund in funds]
        text = [
            f"{weight:.2f}%" if stock.code in fund.stocks else ("零股数" if stock.code in fund.zero_stocks else None)
            for fund, weight in zip(funds, weights, strict=True)
        ]
        table.append((stock, text, max(weights)))
    return funds, table


def build_search_html(result: SearchResult) -> tuple[str, str]:
    title = f"{result.stock.name} · ETF 找股"
    rows, axis = _theme_rows(result)
    scope = (
        f"持仓披露截至 {result.period:%Y-%m-%d}。本次成功查 {result.page}/{result.planned_pages} 页"
        f"（数据源共 {result.pages} 页，每页最多 {HOLDER_PAGE_SIZE} 条），发现 {len(result.candidates)} 只 ETF 候选。"
    )
    source = _SOURCE + " · 披露反查：东方财富"
    quote_window = _quote_window(result.preview.funds)
    names = {fund.code: fund.name for fund in result.candidates}
    membership = [
        f"{names[item.code]} {item.code} · {item.stock_count} 只成分股有正股数"
        for item in result.preview.membership_only
    ]
    preview_note = f"篮子超过 {PREVIEW_STOCK_LIMIT} 只正股数成分股时，找股只核验成分。展开或直接对比时按整篮估值。"
    lines = [title, quote_window, _BASIS, scope]
    lines.extend(
        f"{row.name} {row.range_text}（{row.caption}）· 参考指数 {row.index_text}"
        if row.range_text
        else f"{row.name} · {row.caption} · 本轮无可估算权重"
        for row in rows
    )
    if not result.preview.funds:
        lines.append("本轮暂无当日权重估算。")
    if membership:
        lines.extend((f"当日篮子包含{result.stock.name} · 仅核验成分", preview_note, *membership))
    lines.extend(("发送「展开 主题名」对比该类 ETF。", source))
    lines.extend(failure_lines(result.preview.failures))
    warn, warn_height = _warning(result.preview.failures)
    html = render_template(
        "etf_baskets.html",
        title=title,
        basis=_BASIS,
        source=source,
        quote_window=quote_window,
        mode="search",
        themes=rows,
        last_tick=axis,
        scope=scope,
        has_weights=bool(result.preview.funds),
        membership=membership,
        membership_title=f"当日篮子包含{result.stock.name} · 仅核验成分",
        preview_note=preview_note,
        warn=warn,
        warn_css=WARN_CSS,
        warn_height=warn_height,
    )
    return html, "\n".join(line for line in lines if line)


def build_comparison_html(result: BasketResult, title: str, target: TargetStock | None) -> tuple[str, str]:
    funds, matrix = _compare_view(result, target)
    overlaps = shares_overlap(funds)
    if len(overlaps) == 1:
        overlap_note = f"两只 ETF 篮子股票权重重合度：{overlaps[0][2]:.2f}%"
    else:
        low = min(value for _, _, value in overlaps)
        high = max(value for _, _, value in overlaps)
        overlap_note = f"两两篮子股票权重重合度：{low:.2f}%–{high:.2f}%"
    quote_window = _quote_window(funds)
    lines = [title, quote_window, _BASIS, overlap_note]
    lines.extend(
        f"{fund.name} {fund.code} · {len(fund.stocks)} 只计价股票参与估算 · {fund.index_name or '跟踪指数暂无'}"
        for fund in funds
    )
    for stock, values, _ in matrix:
        cells = " · ".join(f"{fund.code} {value or '无计价股数'}" for fund, value in zip(funds, values, strict=True))
        lines.append(f"{stock.name}：{cells}")
    for fund in funds:
        sectors_text = " · ".join(f"{name} {weight:.2f}%" for name, weight in fund.industries.items())
        lines.append(f"{fund.code} 申万二级：{sectors_text}")
    lines.extend(f"{left} / {right} 重合度 {overlap:.2f}%" for left, right, overlap in overlaps)
    lines.extend(failure_lines(result.failures))
    warn, warn_height = _warning(result.failures)
    html = render_template(
        "etf_baskets.html",
        title=title,
        basis=_BASIS,
        source=_SOURCE,
        quote_window=quote_window,
        mode="compare",
        funds=funds,
        matrix=matrix,
        pies=_industry_pies(funds),
        industry_limit=_INDUSTRIES_PER_PIE,
        overlap=overlap_note,
        warn=warn,
        warn_css=WARN_CSS,
        warn_height=warn_height,
    )
    return html, "\n".join(lines)


async def _render_etf_result(html: str, text: str, label: str) -> bytes | str:
    ai_return(text)
    try:
        return await render_html(html, width=WIDTH, label=label)
    except RenderError:
        return text


async def draw_etf_search(result: SearchResult) -> bytes | str:
    html, text = build_search_html(result)
    return await _render_etf_result(html, text, "ETF找股")


async def draw_etf_comparison(result: BasketResult, title: str, target: TargetStock | None = None) -> bytes | str:
    if len(result.funds) < 2:
        lines = failure_lines(result.failures)
        message = "\n".join(["当日可核验的 ETF 不足两只，暂无法对比。", *lines])
        ai_return(message)
        return message
    html, text = build_comparison_html(result, title, target)
    return await _render_etf_result(html, text, title)
