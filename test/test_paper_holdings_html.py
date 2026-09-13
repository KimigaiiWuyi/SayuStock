"""模拟盘持仓 HTML 结构（不依赖 pytakumi / 网络）。"""

from __future__ import annotations

from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.paper_holdings_html import (
    HoldingBarRow,
    build_paper_holdings_html,
    paper_holdings_canvas_size,
)


def _row(
    code: str,
    name: str,
    *,
    qty: int = 200,
    cost: float = 1.0,
    price: float = 1.1,
    day: float | None = 1.2,
    hold_pct: float = 10.0,
) -> HoldingBarRow:
    mv = price * qty
    unreal = (price - cost) * qty
    return HoldingBarRow(
        code=code,
        name=name,
        qty=qty,
        avg_cost=cost,
        current_price=price,
        day_change_pct=day,
        unrealized_pnl=round(unreal, 2),
        unrealized_pnl_pct=hold_pct,
        market_value=round(mv, 2),
    )


def test_canvas_size_nine_rows() -> None:
    w, h = paper_holdings_canvas_size(9)
    assert w == 900
    empty_w, empty_h = paper_holdings_canvas_size(0)
    assert empty_w == 900
    assert h > empty_h
    assert empty_h == paper_holdings_canvas_size(1)[1]


def test_html_embeds_summary_legend_and_bars() -> None:
    row = _row("512880", "证券ETF国泰")
    idx = DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001")
    html = build_paper_holdings_html(
        account_name="默认模拟盘",
        strategy_id="multi_factor",
        enabled=True,
        cash=718885.0,
        initial_cash=1_000_000.0,
        holdings=[row],
        index_items=[idx],
        title_num="5",
    )
    assert "width: 900px" in html
    assert "data:image/png;base64," in html
    assert "模拟盘 · 默认模拟盘" in html
    assert "multi_factor" in html
    assert "运行中" in html
    assert "总资产" in html
    assert "71.91万" in html or "71.89万" in html
    assert "图例" in html
    assert "今日涨跌" in html
    assert "持仓收益" in html
    assert "当日分时" in html
    assert "证券ETF国泰" in html
    assert "今+1.20%" in html
    assert "持+10.00%" in html
    assert 'class="donut-svg"' in html
    assert 'class="legend"' in html
    assert 'class="tile"' in html
    assert "上证指数" in html
    assert 'class="spark"' not in html


def test_html_injects_sparkline_and_empty_state() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
    html = build_paper_holdings_html(
        account_name="放量盘",
        strategy_id="volume_extremum",
        enabled=False,
        cash=1_000_000.0,
        initial_cash=1_000_000.0,
        holdings=[_row("512880", "证券ETF国泰")],
        index_items=[],
        title_num="5",
        sparklines={"512880": svg},
    )
    assert 'class="spark"' in html
    assert "<svg" in html
    assert "已停用" in html

    empty = build_paper_holdings_html(
        account_name="默认模拟盘",
        strategy_id="multi_factor",
        enabled=True,
        cash=1_000_000.0,
        initial_cash=1_000_000.0,
        holdings=[],
        index_items=[],
        title_num="5",
    )
    assert "当前无持仓" in empty
    assert "100万" in empty
    assert 'class="spark"' not in empty
