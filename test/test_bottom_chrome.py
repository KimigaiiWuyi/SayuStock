"""来源行与 footer 图不共用一条底边。"""

from __future__ import annotations

from SayuStock.utils.bottom_chrome import source_band_h, layout_bottom_chrome


def test_overview_source_clears_bars_and_footer() -> None:
    content_bottom = 980 + 20 * 90
    chrome = layout_bottom_chrome(1700, content_bottom, font_size=24, footer_w=850, footer_h=40)
    band = source_band_h(24)
    source_top = chrome.source_y - band // 2
    source_bottom = chrome.source_y + band // 2
    assert source_top >= content_bottom
    assert source_bottom <= chrome.footer_y
    assert chrome.footer_y + 40 <= chrome.canvas_h
    assert chrome.footer_x == (1700 - 850) // 2


def test_fund_source_clears_last_bar() -> None:
    content_bottom = 400 + 8 * 110
    chrome = layout_bottom_chrome(900, content_bottom, font_size=18, footer_w=850, footer_h=40)
    band = source_band_h(18)
    assert chrome.source_y - band // 2 >= content_bottom
    assert chrome.source_y + band // 2 <= chrome.footer_y
    assert chrome.footer_x == (900 - 850) // 2
