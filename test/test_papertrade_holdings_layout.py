"""模拟盘持仓简图布局：摘要 / banner / 持仓条不重叠。"""

from __future__ import annotations

from PIL import Image, ImageDraw

from gsuid_core.utils.fonts.fonts import core_font as ss_font
from SayuStock.stock_papertrade.render import (
    _HS_ROW_H,
    _HS_BAR5_H,
    _HS_SUMMARY_H,
    _HS_SUMMARY_Y,
    _fit_text,
    _holdings_layout,
)


def test_holdings_layout_no_overlap() -> None:
    body_h, bar5_y, rows_y0 = _holdings_layout(3)
    # 摘要在 bar5 之上
    assert _HS_SUMMARY_Y + _HS_SUMMARY_H <= bar5_y
    # bar5 在持仓条之上
    assert bar5_y + _HS_BAR5_H <= rows_y0
    # 画布够放下 3 条
    assert body_h >= rows_y0 + 3 * _HS_ROW_H


def test_fit_text_truncates_to_width() -> None:
    img = Image.new("RGBA", (100, 40))
    draw = ImageDraw.Draw(img)
    font = ss_font(20)
    long = "总资产 1,234,567  现金 999,999  持仓市值 888,888  浮盈 +12,345(+1.23%)  累计 +98,765(+9.87%)"
    fitted = _fit_text(draw, long, font, 200)
    assert fitted != long
    assert fitted.endswith("…")
    bbox = draw.textbbox((0, 0), fitted, font=font)
    assert bbox[2] - bbox[0] <= 200
