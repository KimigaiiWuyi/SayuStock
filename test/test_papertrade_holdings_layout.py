"""模拟盘持仓简图布局：摘要 / 图例 / banner / 持仓条不重叠。"""

from __future__ import annotations

from SayuStock.utils.paper_holdings_html import (
    BAR_H,
    SUM_H,
    SUM_Y,
    BAR5_H,
    BAR5_GAP,
    BARS_GAP,
    LEGEND_H,
    LEGEND_GAP,
    paper_holdings_canvas_size,
)


def test_holdings_layout_no_overlap() -> None:
    width, height = paper_holdings_canvas_size(3)
    legend_y = SUM_Y + SUM_H + LEGEND_GAP
    bar5_y = legend_y + LEGEND_H + BAR5_GAP
    bars_y = bar5_y + BAR5_H + BARS_GAP
    assert width == 900
    assert SUM_Y + SUM_H <= legend_y
    assert legend_y + LEGEND_H <= bar5_y
    assert bar5_y + BAR5_H <= bars_y
    assert height >= bars_y + 3 * BAR_H


def test_canvas_grows_with_rows() -> None:
    _, h1 = paper_holdings_canvas_size(1)
    _, h4 = paper_holdings_canvas_size(4)
    assert h4 - h1 == 3 * BAR_H
