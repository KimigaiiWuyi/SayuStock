"""我的自选 HTML 结构（不依赖 pytakumi / 网络）。"""

from __future__ import annotations

import re
from datetime import datetime

from SayuStock.utils.warn_box import Failure, warn_box_height
from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import Quote, SymbolRef
from SayuStock.utils.my_stock_html import BAR_H, HEAD_H, FOOT_PAD, build_my_stock_html, my_stock_canvas_size
from SayuStock.utils.market.display import DisplayItem


def _quote(code: str, name: str, price: float, chg: float) -> Quote:
    symbol = SymbolRef(
        code=code,
        name=name,
        asset_class=AssetClass.ETF,
        exchange="SSE",
        provider_symbol=f"1.{code}",
        sec_type="沪A",
    )
    return Quote(
        symbol=symbol,
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=price,
        change_pct=chg,
        change_amount=None,
        volume=1.0,
        amount=1.0e8,
        turnover_rate=1.2,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=datetime(2026, 9, 4, 15, 0),
    )


def test_canvas_size_nine_rows() -> None:
    w, h, two = my_stock_canvas_size(9)
    assert w == 900
    assert two is False
    assert h == HEAD_H + 9 * BAR_H + FOOT_PAD


def test_html_embeds_textures_and_layout() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    idx = DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001")
    html = build_my_stock_html(quotes=[(q, "512880")], index_items=[idx], title_num="5")
    _, canvas_h, _ = my_stock_canvas_size(1)
    assert "width: 900px" in html
    assert f"height: {canvas_h}px" in html
    assert "data:image/png;base64," in html
    assert "证券ETF国泰" in html
    assert "+0.64%" in html
    assert "上证指数" in html
    assert 'class="bar"' in html
    assert 'class="spark"' not in html


def test_html_injects_sparkline() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
    html = build_my_stock_html(
        quotes=[(q, "512880")],
        index_items=[],
        title_num="5",
        sparklines={"512880": svg},
    )
    assert 'class="spark"' in html
    assert "<svg" in html
    assert "font-size: 30px" in html
    assert "width: 136.5px" in html
    assert "left: 503.5px" in html


def _css_px(html: str, selector: str, prop: str) -> int:
    block = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", html)
    assert block is not None
    found = re.search(rf"{prop}:\s*(-?\d+)px", block.group(1))
    assert found is not None
    return int(found.group(1))


def test_source_sits_above_footer() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    html = build_my_stock_html(
        quotes=[(q, "512880")],
        index_items=[],
        title_num="5",
        sources=("eastmoney", "sina", "tencent"),
    )
    _, height, _ = my_stock_canvas_size(1)
    src_top = _css_px(html, ".src", "top")
    src_h = _css_px(html, ".src", "height")
    foot_top = _css_px(html, ".footer", "top")
    foot_h = _css_px(html, ".footer", "height")
    assert src_top >= height - FOOT_PAD
    assert src_top + src_h <= foot_top
    assert foot_top + foot_h <= height
    assert "数据来源：" in html


def test_failed_stocks_render_warning_box_not_silent() -> None:
    """取不到的自选股必须画黄色警告，不许悄悄少一张卡。"""
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    failures = [
        Failure(label="600519", reason="东方财富网络异常"),
        Failure(label="000858", reason="东方财富网络异常"),
        Failure(label="300750", reason="未找到标的"),
    ]
    html = build_my_stock_html(
        quotes=[(q, "512880")],
        index_items=[],
        title_num="5",
        failures=failures,
        warn_title="⚠ 3/4 只自选股行情获取失败",
    )
    assert 'class="warn"' in html
    assert "3/4 只自选股行情获取失败" in html
    assert "东方财富网络异常：600519、000858" in html
    assert "未找到标的：300750" in html
    # 成功的卡照常画
    assert 'class="bar"' in html
    assert "证券ETF国泰" in html


def test_canvas_grows_with_warning_box() -> None:
    failures = [Failure(label="600519", reason="东方财富网络异常")]
    _, base_h, _ = my_stock_canvas_size(1)
    _, fail_h, _ = my_stock_canvas_size(1, failures)
    assert fail_h > base_h
    assert fail_h - base_h == warn_box_height(1)


def test_all_failed_page_has_warning_and_no_bars() -> None:
    """全失败也要出图（警告框），不许回一句「暂无自选行情」就完事。"""
    failures = [Failure(label="600519", reason="东方财富网络异常")]
    html = build_my_stock_html(
        quotes=[],
        index_items=[],
        title_num="5",
        failures=failures,
        warn_title="⚠ 1/1 只自选股行情获取失败",
    )
    assert 'class="warn"' in html
    assert 'class="bar"' not in html
    _, height, _ = my_stock_canvas_size(0, failures)
    assert height > HEAD_H
