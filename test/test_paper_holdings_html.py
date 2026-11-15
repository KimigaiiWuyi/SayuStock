"""模拟盘持仓：账面口径、失败状态和行情来源使用同一份行数据。"""

from __future__ import annotations

from dataclasses import replace

from SayuStock.utils.warn_box import Failure
from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.paper_holdings_html import HoldingBarRow, build_paper_holdings_html


def _row(*, day: float | None = 1.2) -> HoldingBarRow:
    return HoldingBarRow(
        code="512880",
        name="示例证券ETF",
        qty=200,
        avg_cost=1.0,
        current_price=1.1,
        day_change_pct=day,
        unrealized_pnl=20.0,
        unrealized_pnl_pct=10.0,
        market_value=220.0,
        provider="tencent",
    )


def test_html_embeds_summary_legend_spark_and_both_return_series() -> None:
    row = _row()
    html = build_paper_holdings_html(
        account_name="示例模拟盘",
        strategy_id="multi_factor",
        enabled=True,
        cash=780.0,
        initial_cash=900.0,
        holdings=[row],
        index_items=[DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001", provider="eastmoney")],
        sparklines={row.code: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'},
    )
    for label in ("示例模拟盘", "multi_factor", "运行中", "总资产", "1000", "仓位", "22%", "图例", "上证指数"):
        assert label in html
    assert "今日涨跌相对昨收" in html and "持仓收益相对成本" in html
    assert "+1.20%" in html and "+10.00%" in html and "+11.11%" in html
    assert "+9.09%" in html  # 浮盈沿用市值分母，不能与单只持仓成本分母混用
    assert "数据来源：东方财富、腾讯财经" in html
    assert '<div class="spark"><svg' in html
    assert html.index("示例证券ETF") < html.index('<footer class="foot extra-foot">')


def test_empty_holdings_render_cash_and_no_fabricated_quote() -> None:
    html = build_paper_holdings_html(
        account_name="示例模拟盘",
        strategy_id="multi_factor",
        enabled=False,
        cash=1_000_000.0,
        initial_cash=1_000_000.0,
        holdings=[],
        index_items=[],
    )
    assert "当前无持仓" in html and "100万" in html and "已停用" in html
    assert 'class="spark"' not in html
    assert "可用行情平均日涨跌" not in html
    assert "数据来源：东方财富" not in html


def test_failed_quote_uses_existing_value_and_names_reason() -> None:
    row = replace(_row(day=None), quote_error="腾讯财经网络异常", price_basis="上次报价", provider=None)
    html = build_paper_holdings_html(
        account_name="示例模拟盘",
        strategy_id="multi_factor",
        enabled=True,
        cash=1_000_000.0,
        initial_cash=1_000_000.0,
        holdings=[row],
        index_items=[],
        failures=[Failure(f"{row.name}({row.code})", row.quote_error)],
        warn_title="1/1 只持仓今日行情获取失败",
    )
    assert "1/1 只持仓今日行情获取失败" in html
    assert "腾讯财经网络异常：示例证券ETF(512880)" in html
    assert 'class="hold-change ex-warn">未获取' in html
    assert "成本 1.000 · 上次报价 1.100" in html
    assert "+10.00%" in html and 'class="spark"' not in html
