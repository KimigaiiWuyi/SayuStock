"""PyTakumi images for the four stock analysis commands."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from gsuid_core.utils.plugins_config.gs_config import pic_gen_config

from .card import TradeCardData
from ..render import render_html
from .screener import ScreenerResult
from .portfolio import PortfolioRiskReport
from .technical import TechnicalReport
from ..utils.utils import number_to_chinese
from ..render.palette import SERIES
from ..render.extras_ui import Tone, Metric, direction, fmt_number
from ..render.templates import render_template
from ..utils.market.display import source_footer

REPORT_WIDTH = 840
SCREENER_WIDTH = 990

_LEVELS = (
    ("支撑", "support"),
    ("压力", "resistance"),
    ("MA20", "ma20"),
    ("MA60", "ma60"),
    ("参考止损", "stop_ref"),
    ("参考目标", "target_ref"),
)
_FINANCE = (
    ("ROE", "roe", "%"),
    ("营收同比", "revenue_yoy", "%"),
    ("净利同比", "profit_yoy", "%"),
    ("毛利率", "gross_margin", "%"),
    ("净利率", "net_margin", "%"),
    ("负债率", "debt_ratio", "%"),
    ("EPS", "eps", ""),
    ("BPS", "bps", ""),
)
_RISK_TONES: dict[str, Tone] = {"分散": "down", "适中": "warn", "集中": "up", "极集中": "up"}


@dataclass(frozen=True, slots=True)
class _TechnicalPanel:
    score: int
    tone: Tone
    readings: tuple[Metric, ...]
    levels: tuple[Metric, ...]
    signals: tuple[str, ...]
    risks: tuple[str, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class _ScreenerRow:
    code: str
    name: str
    price: str
    change: str
    tone: Tone
    market_cap: str
    pe: str
    turnover: str
    volume_ratio: str
    industry: str


@dataclass(frozen=True, slots=True)
class _IndustryWeight:
    label: str
    weight: float
    value: str
    color: str


def _score_tone(score: int) -> Tone:
    if score >= 65:
        return "up"
    if score <= 40:
        return "down"
    return "warn"


def _field(values: Mapping[str, object], key: str) -> object:
    return values[key] if key in values else None


def _technical_panel(report: TechnicalReport, *, compact: bool = False) -> _TechnicalPanel:
    return _TechnicalPanel(
        score=report.score,
        tone=_score_tone(report.score),
        readings=(
            Metric("趋势", report.trend),
            Metric("动量", report.momentum),
            Metric("量能", report.volume),
            Metric("位置", report.position),
        ),
        levels=tuple(
            Metric(label, fmt_number(_field(report.levels, key)))
            for label, key in (_LEVELS[:3] if compact else _LEVELS)
        ),
        signals=tuple(report.signals[:3] if compact else report.signals[:6]),
        risks=tuple(report.risk_flags[:2] if compact else report.risk_flags[:5]),
        summary=report.summary,
    )


def build_technical_html(report: TechnicalReport) -> str:
    return render_template(
        "analysis_technical.html",
        width=REPORT_WIDTH,
        stock_name=report.name,
        code=report.code,
        period=report.period_label,
        last_close=fmt_number(report.last_close),
        technical=_technical_panel(report),
        footer=source_footer(*report.source_ids, note="技术分析"),
    )


def build_trade_card_html(card: TradeCardData) -> str:
    finance = tuple(
        Metric(label, fmt_number(value, suffix=suffix))
        for label, key, suffix in _FINANCE
        if (value := _field(card.finance, key)) is not None
    )
    report_date = _field(card.finance, "report_date")
    quotes = (
        Metric("开盘", fmt_number(card.open_price)),
        Metric("昨收", fmt_number(card.prev_close)),
        Metric("最高", fmt_number(card.high)),
        Metric("最低", fmt_number(card.low)),
        Metric("换手率", fmt_number(card.turnover, suffix="%")),
        Metric("成交额", number_to_chinese(card.amount) if card.amount is not None else "—"),
    )
    valuation = (
        Metric("PE", fmt_number(card.pe)),
        Metric("PB", fmt_number(card.pb)),
        Metric("总市值", fmt_number(card.mv / 1e8, 1, suffix="亿") if card.mv is not None else "—"),
    )
    return render_template(
        "analysis_trade_card.html",
        width=REPORT_WIDTH,
        stock_name=card.name,
        code=card.code,
        price=fmt_number(card.price),
        change=fmt_number(card.pct, signed=True, suffix="%"),
        tone=direction(card.pct),
        industry=card.industry,
        industry_change=fmt_number(card.industry_pct, signed=True, suffix="%") if card.industry_pct is not None else "",
        quotes=quotes,
        valuation=valuation,
        technical=_technical_panel(card.technical, compact=True) if card.technical else None,
        finance=finance,
        report_date=report_date if isinstance(report_date, str) else "",
        footer=source_footer(*card.source_ids, note="股票卡片"),
    )


def _row_text(row: Mapping[str, object], key: str) -> str:
    value = _field(row, key)
    return value if isinstance(value, str) and value else "—"


def build_screener_html(result: ScreenerResult) -> str:
    records: list[dict[str, object]] = result.df.to_dict("records")
    rows: list[_ScreenerRow] = []
    for row in records:
        pct = _field(row, "pct")
        rows.append(
            _ScreenerRow(
                code=_row_text(row, "code"),
                name=_row_text(row, "name"),
                price=fmt_number(_field(row, "price")),
                change=fmt_number(pct, signed=True),
                tone=direction(pct if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None),
                market_cap=fmt_number(_field(row, "mv_yi"), 1),
                pe=fmt_number(_field(row, "pe"), 1),
                turnover=fmt_number(_field(row, "turnover"), 1),
                volume_ratio=fmt_number(_field(row, "vol_ratio")),
                industry=_row_text(row, "industry"),
            )
        )
    return render_template(
        "analysis_screener.html",
        width=SCREENER_WIDTH,
        scope=result.scope,
        counts=(
            Metric("股票池", str(result.total_pool)),
            Metric("命中", str(result.matched), "info"),
            Metric("展示", str(result.shown)),
        ),
        filters=result.filters_desc,
        rows=rows,
        footer=source_footer(*result.source_ids, note="自动选股"),
    )


def build_portfolio_html(report: PortfolioRiskReport, *, requested_count: int | None = None) -> str:
    weights = tuple(
        _IndustryWeight(label, weight * 100, f"{weight:.1%}", SERIES[i % len(SERIES)])
        for i, (label, weight) in enumerate(report.industry_weights.items())
    )
    metrics = (
        Metric("HHI", f"{report.hhi:.3f}"),
        Metric("有效行业数", f"{report.effective_n:.1f}"),
        Metric("最大行业", f"{report.top1_weight:.1%}", note=report.top1_name),
        Metric("Top 3 行业", f"{report.top3_weight:.1%}"),
    )
    count = len(report.holdings)
    return render_template(
        "analysis_portfolio.html",
        width=REPORT_WIDTH,
        risk_level=report.risk_level,
        risk_tone=_RISK_TONES[report.risk_level] if report.risk_level in _RISK_TONES else "neutral",
        metrics_values=metrics,
        weights=weights,
        messages=report.messages,
        count=count,
        requested_count=requested_count if requested_count is not None and count < requested_count else None,
        footer=source_footer(*report.source_ids, note="组合体检（等权）"),
    )


async def _render_report(html: str, *, width: int, label: str) -> bytes:
    return await render_html(
        html,
        width=width,
        label=label,
        image_format="jpeg",
        jpeg_quality=pic_gen_config.get_config("PicQuality").data,
    )


async def render_technical_image(report: TechnicalReport) -> bytes:
    return await _render_report(build_technical_html(report), width=REPORT_WIDTH, label="技术分析")


async def render_card_image(card: TradeCardData) -> bytes:
    return await _render_report(build_trade_card_html(card), width=REPORT_WIDTH, label="股票卡片")


async def render_screener_image(result: ScreenerResult) -> bytes:
    return await _render_report(build_screener_html(result), width=SCREENER_WIDTH, label="自动选股")


async def render_portfolio_image(report: PortfolioRiskReport, *, requested_count: int | None = None) -> bytes:
    return await _render_report(
        build_portfolio_html(report, requested_count=requested_count), width=REPORT_WIDTH, label="组合体检"
    )
