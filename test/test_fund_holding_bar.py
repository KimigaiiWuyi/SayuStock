"""基金持仓条：每票用自己的代码，名称带市场后缀。"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.market.enums import AssetClass
from SayuStock.render.card_widgets import watch_quote_row
from SayuStock.utils.market.models import Quote, SymbolRef


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
    holdings = [
        _quote(code="600519", name="贵州茅台", sec_type="沪深A", provider_symbol="1.600519"),
        _quote(code="00700", name="腾讯控股", sec_type="港股", provider_symbol="116.00700"),
        _quote(code="300750", name="宁德时代", sec_type="创业板", provider_symbol="0.300750"),
    ]
    rows = [watch_quote_row(q, "1.000001") for q in holdings]
    assert [r.details.split(" · ")[0] for r in rows] == ["600519", "00700", "300750"]
    assert [(r.name, r.market) for r in rows] == [("贵州茅台", "沪深A"), ("腾讯控股", "港股"), ("宁德时代", "创业板")]


def test_bar_labels_fall_back_when_quote_code_empty() -> None:
    q = _quote(code="", name="贵州茅台", sec_type="沪深A", provider_symbol="1.600519")
    row = watch_quote_row(q, "600519")
    assert row.name == "贵州茅台"
    assert row.details.startswith("600519")
