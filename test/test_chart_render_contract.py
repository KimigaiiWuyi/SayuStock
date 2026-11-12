"""Tick placement and observable output of the chart page renderer."""

import os
import re
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta

import pytest
from PIL import Image

from SayuStock.render import render_html
from SayuStock.render.runtime import font_paths
from SayuStock.render.intraday import prepare_single_intraday
from SayuStock.render.chart_page import WIDTH, Panel, ChartPage, page_html, date_ticks
from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import SymbolRef, IntradayPoint, IntradaySeries


def test_intraday_ticks_have_only_one_visible_clock_layer() -> None:
    start = datetime(2026, 8, 21, 9, 30)
    points = tuple(
        IntradayPoint(start + timedelta(minutes=i), 10 + i / 100, 10, 11, 10, 100, 1000, 10) for i in range(60)
    )
    series = IntradaySeries(SymbolRef("510300", "沪深300ETF", AssetClass.ETF, "SSE", "510300"), points, None)
    page = prepare_single_intraday(series)
    assert isinstance(page, ChartPage)
    price, amount = page.panels
    assert not price.x_ticks
    clocks = [tick.label for tick in amount.x_ticks]
    assert len(clocks) >= 2
    assert all(re.fullmatch(r"\d{2}:\d{2}", clock) for clock in clocks)
    assert clocks[0] == "09:30"
    visible_labels = re.findall(r'<span class="chart-label"[^>]*>([^<]+)</span>', page_html(page))
    assert [label for label in visible_labels if re.fullmatch(r"\d{2}:\d{2}", label)] == clocks


def test_cross_year_ticks_disambiguate_both_dates() -> None:
    stamps = (datetime(2025, 12, 31, 23, 50), datetime(2026, 1, 1, 0, 10))
    ticks = date_ticks(stamps, (100, 200), intraday=True)
    assert [(tick.pos, tick.label) for tick in ticks] == [(100, "2025-12-31 23:50"), (200, "2026-01-01 00:10")]


def test_renderer_registers_core_misans() -> None:
    from gsuid_core.utils.fonts.fonts import FONT_ORIGIN_PATH

    assert font_paths()[0] == ("MiSans", FONT_ORIGIN_PATH)


@pytest.mark.skipif(
    "SAYUSTOCK_RENDER_RUNTIME" not in os.environ and os.getenv("SAYUSTOCK_REQUIRE_RENDER_RUNTIME") != "1",
    reason="configure the Takumi runtime to render the PNG contract",
)
@pytest.mark.asyncio
async def test_chart_png_draws_dark_canvas_chinese_glyphs_and_source_footer() -> None:
    source = "数据来源：腾讯财经 | SayuStock"
    page = ChartPage("甲乙", "分时", "", [Panel("价格", 140, [])], source)
    html = page_html(page)
    assert f'<div class="chart-source">{source}</div>' in html
    payload = await render_html(html, width=WIDTH, scale=1, label="图表输出回归")
    evidence = os.getenv("SAYUSTOCK_RENDER_EVIDENCE_DIR")
    if evidence:
        out = Path(evidence)
        out.mkdir(parents=True, exist_ok=True)
        (out / "chart-contract.png").write_bytes(payload)

    with Image.open(BytesIO(payload)) as png:
        assert png.format == "PNG"
        assert png.width == WIDTH
        alpha = png.convert("RGBA").getchannel("A")
        for box in (
            (0, 0, png.width, 1),
            (0, png.height - 1, png.width, png.height),
            (0, 0, 1, png.height),
            (png.width - 1, 0, png.width, png.height),
        ):
            assert alpha.crop(box).getextrema() == (255, 255)
        image = png.convert("L")
    assert image.crop((0, 0, 20, image.height)).getextrema()[1] < 40
    assert image.crop((100, 150, 300, 175)).getextrema()[1] < 40
    title = image.crop((28, 48, 96, 90)).point(lambda v: 255 if v > 100 else 0)
    masks = []
    for i in (0, 34):
        glyph = title.crop((i, 0, i + 34, title.height))
        ink = glyph.getbbox()
        assert ink is not None and ink[2] - ink[0] > 15 and ink[3] - ink[1] > 15
        masks.append(glyph.crop(ink))
    assert masks[0].tobytes() != masks[1].tobytes()
    footer = image.crop((28, image.height - 52, 420, image.height - 26)).point(lambda v: 255 if v > 80 else 0)
    extent = footer.getbbox()
    assert extent is not None and extent[2] - extent[0] > 120
