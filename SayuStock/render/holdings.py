"""The shared holdings layout; watchlist and fund supply their own row semantics."""

from __future__ import annotations

from collections.abc import Sequence

from .templates import render_template
from .card_widgets import Card, QuoteRow, tone, mood_uri, footer_uri, signed_pct
from ..utils.warn_box import Failure, failure_lines

PAD = 24
SINGLE_WIDTH, DOUBLE_WIDTH = 760, 1360


def holdings_html(
    *,
    eyebrow: str,
    title: str,
    subtitle: str,
    average: float | None,
    title_num: str,
    cards: list[Card],
    rows: list[QuoteRow],
    section_title: str,
    section_note: str,
    source: str,
    width: int,
    two_columns: bool = False,
    failures: Sequence[Failure] = (),
    warning_title: str = "",
) -> str:
    split = (len(rows) + 1) // 2
    mood_width = width - 2 * PAD
    return render_template(
        "holdings.html",
        eyebrow=eyebrow,
        title=title,
        subtitle=subtitle,
        summary=signed_pct(average) if average is not None else "",
        summary_tone=tone(average),
        mood=mood_uri(title_num),
        mood_width=mood_width,
        mood_height=round(mood_width * 400 / 850, 2),
        cards=cards,
        columns=(rows[:split], rows[split:]) if two_columns else (rows,),
        empty=not rows,
        section_title=section_title,
        section_note=section_note,
        source=source,
        brand=footer_uri(),
        width=width,
        warning_lines=failure_lines(failures, per_line=2),
        warning_title=warning_title,
    )
