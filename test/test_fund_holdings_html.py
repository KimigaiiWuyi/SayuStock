"""基金持仓：保留行、占比、报价、分时及原位失败。"""

from __future__ import annotations

from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.fund_holdings_html import FundHoldingRow, build_fund_holdings_html


def _build(rows: list[FundHoldingRow], *, sparklines: dict[str, str] | None = None) -> str:
    return build_fund_holdings_html(
        fund_name="易方达蓝筹精选",
        fund_code="1.000005",
        rows=rows,
        index_items=[DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001")],
        meta="持仓 3 只 · 前十占比 21.70%",
        average=0.5,
        report_period="报告期 2026-09-30",
        sparklines=sparklines,
        sources=("tencent",),
    )


def test_html_shows_weight_price_and_sparkline() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
    html = _build(
        [FundHoldingRow("600519", "贵州茅台", "8.50", price=1680.003, change_pct=1.23)],
        sparklines={"600519": svg},
    )
    for label in (
        "易方达蓝筹精选",
        "报告期 2026-09-30",
        "贵州茅台",
        "8.50%",
        "1680.003",
        "+1.23%",
        "当日分时",
        "基金持仓权重",
        "前十占比 21.70%",
        "+0.50%",
        'class="spark"',
    ):
        assert label in html
    assert html.index("数据来源：东方财富、腾讯财经") < html.index('class="footer-brand"')


def test_failure_stays_in_its_own_gold_row_without_fake_values() -> None:
    html = _build(
        [
            FundHoldingRow("300750", "宁德时代", "6.20", failed=True, reason="东方财富网络异常"),
            FundHoldingRow("600519", "贵州茅台", "8.50", price=1680.0, change_pct=1.23),
            FundHoldingRow("000858", "五粮液", "7.00", price=80.0, change_pct=-2.04),
        ]
    )
    failure = html[html.index('class="row bar failed"') : html.index('class="row bar up"')]
    assert "⚠ 东方财富网络异常" in failure and "6.20%" in failure
    assert '<b class="pill number">失败</b>' in failure
    assert 'class="column q-price' not in failure
    assert 'class="column warn"' not in html
    assert 'class="row bar down"' in html and "-2.04%" in html
