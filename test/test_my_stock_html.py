"""我的自选 HTML 结构（不依赖 pytakumi / 网络）。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import Quote, SymbolRef
from SayuStock.utils.my_stock_html import build_my_stock_html, my_stock_canvas_size
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
    assert h == 541 + 9 * 110 + 60


def test_html_embeds_textures_and_layout() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    idx = DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001")
    html = build_my_stock_html(quotes=[(q, "512880")], index_items=[idx], title_num="5")
    assert "width: 900px" in html
    assert "height: 711px" in html
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
