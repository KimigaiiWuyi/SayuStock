"""Presentation adapter for 我的自选."""

from __future__ import annotations

from collections.abc import Sequence

from .market import Quote
from .warn_box import Failure
from .market.display import source_footer
from ..render.holdings import DOUBLE_WIDTH, SINGLE_WIDTH, holdings_html
from ..render.card_widgets import row_spark, mood_number, quote_cards, watch_quote_row

WATCH_INDEXES = (
    ("上证指数", "1.000001"),
    ("深证成指", "0.399001"),
    ("中证A500", "1.000510"),
    ("中证2000", "2.932000"),
)
SPARK_W = 130.0
SPARK_H = 40.0
TWO_COLUMNS_FROM = 18


def watchlist_width(saved_count: int) -> int:
    return DOUBLE_WIDTH if saved_count >= TWO_COLUMNS_FROM else SINGLE_WIDTH


def watchlist_average(quotes: Sequence[tuple[Quote, str]]) -> float | None:
    changes = [q.change_pct for q, _ in quotes if q.change_pct is not None]
    return sum(changes) / len(changes) if changes else None


def build_my_stock_html(
    *,
    quotes: list[tuple[Quote, str]],
    index_quotes: Sequence[Quote | None],
    saved_count: int,
    sparklines: dict[str, str] | None = None,
    sources: Sequence[str | None] = (),
    failures: Sequence[Failure] = (),
) -> str:
    indexes = [(name, q) for (name, _), q in zip(WATCH_INDEXES, index_quotes, strict=True)]
    changes = [q.change_pct for q, _ in quotes if q.change_pct is not None]
    rise = sum(change > 0 for change in changes)
    fall = sum(change < 0 for change in changes)
    average = watchlist_average(quotes)
    subtitle = f"共 {saved_count} 只 · 上涨 {rise} / 平 {len(changes) - rise - fall} / 下跌 {fall}"
    if not changes:
        subtitle = f"共 {saved_count} 只 · 涨跌幅暂无数据"
    used = [*sources, *(q.provider for _, q in indexes if q is not None), *(q.provider for q, _ in quotes)]
    return holdings_html(
        eyebrow="自选行情",
        title="我的自选",
        subtitle=subtitle,
        average=average,
        title_num=mood_number(average),
        cards=quote_cards(indexes),
        rows=[watch_quote_row(q, code, row_spark(sparklines, code, q.symbol.code)) for q, code in quotes],
        section_title="自选股",
        section_note="按自选顺序排列",
        source=source_footer(*used) if any(used) else "数据来源：未标注 | SayuStock",
        width=watchlist_width(saved_count),
        two_columns=saved_count >= TWO_COLUMNS_FROM,
        failures=failures,
        warning_title=f"⚠ {len(failures)}/{saved_count} 只自选股行情获取失败",
    )
