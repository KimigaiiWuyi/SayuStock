"""基金持仓条：每票用自己的代码，名称带市场后缀。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import Quote, SymbolRef
from SayuStock.stock_info.draw_my_info import bar_labels_from_quote


def _quote(*, code: str, name: str, sec_type: str, provider_symbol: str) -> Quote:
    return Quote(
        symbol=SymbolRef(
            code=code,
            name=name,
            asset_class=AssetClass.EQUITY,
            exchange="EM",
            provider_symbol=provider_symbol,
            sec_type=sec_type,
        ),
        price=10.0,
        open=10.0,
        high=11.0,
        low=9.0,
        prev_close=9.9,
        change_pct=1.01,
        change_amount=0.1,
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
        as_of=datetime(2026, 8, 21),
    )


def test_bar_labels_use_each_stock_code_not_fund_code() -> None:
    fund_code = "1.000001"
    holdings = [
        _quote(code="600519", name="贵州茅台", sec_type="沪深A", provider_symbol="1.600519"),
        _quote(code="00700", name="腾讯控股", sec_type="港股", provider_symbol="116.00700"),
        _quote(code="300750", name="宁德时代", sec_type="创业板", provider_symbol="0.300750"),
    ]
    labels = [bar_labels_from_quote(q, fund_code) for q in holdings]
    codes = [code for _name, code in labels]
    assert codes == ["600519", "00700", "300750"]
    assert labels[0][0] == "贵州茅台 (沪深A)"
    assert labels[1][0] == "腾讯控股 (港股)"
    assert labels[2][0] == "宁德时代 (创业板)"


def test_bar_labels_fall_back_when_quote_code_empty() -> None:
    q = _quote(code="", name="贵州茅台", sec_type="沪深A", provider_symbol="1.600519")
    name, code = bar_labels_from_quote(q, "600519")
    assert name == "贵州茅台 (沪深A)"
    assert code == "600519"


def test_draw_bar_from_quote_ignores_wrong_fallback_code() -> None:
    from SayuStock.stock_info.draw_my_info import draw_bar_from_quote

    q = _quote(code="00700", name="腾讯控股", sec_type="港股", provider_symbol="116.00700")
    img = draw_bar_from_quote(q, "1.000001", percent="8.50%")
    assert img.size[0] > 0
    assert img.size[1] > 0
