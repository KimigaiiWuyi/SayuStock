"""我的自选 HTML 结构（不依赖 pytakumi / 网络）。"""

from __future__ import annotations

import re
from datetime import datetime

from SayuStock.utils.warn_box import Failure
from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import Quote, SymbolRef
from SayuStock.utils.my_stock_html import WATCH_INDEXES, watchlist_width, build_my_stock_html


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


def test_full_mood_and_four_indexes_in_single_and_double_layout() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    index_quote = _quote("000001", "上证指数", 3930.12, -0.3)
    for saved_count in (3, 17, 18, 22):
        html = build_my_stock_html(
            quotes=[(q, "512880")], index_quotes=[index_quote, None, None, None], saved_count=saved_count
        )
        expected_columns = 2 if saved_count >= 18 else 1
        assert f'data-columns="{expected_columns}"' in html
        assert len(re.findall(r'class="card (?:up|down|flat)"', html)) == 4
        assert html.count("暂无行情") == 3
        assert html.index('class="mood"') < html.index('class="card-grid"') < html.index('class="list-grid"')
        mood = re.search(r'class="mood"[^>]+width="(\d+)" height="([\d.]+)"', html)
        assert mood is not None
        width, height = (float(x) for x in mood.groups())
        assert width == watchlist_width(saved_count) - 48
        assert abs(height / width - 400 / 850) < 1e-4
        assert "证券ETF国泰" in html and "+0.64%" in html and "1.105" in html
        assert all(name in html for name, _ in WATCH_INDEXES)


def test_source_sits_above_footer_and_sparkline_is_optional() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
    html = build_my_stock_html(
        quotes=[(q, "512880")],
        index_quotes=[None] * 4,
        saved_count=1,
        sparklines={"512880": svg},
        sources=("eastmoney", "sina", "tencent"),
    )
    assert 'class="spark"' in html and "<svg" in html
    assert html.index("数据来源：东方财富、新浪财经、腾讯财经") < html.index('class="footer-brand"')


def test_failed_stocks_render_warning_box_not_silent() -> None:
    q = _quote("512880", "证券ETF国泰", 1.105, 0.64)
    failures = [
        Failure(label="600519", reason="东方财富网络异常"),
        Failure(label="000858", reason="东方财富网络异常"),
        Failure(label="300750", reason="未找到标的"),
    ]
    html = build_my_stock_html(quotes=[(q, "512880")], index_quotes=[None] * 4, saved_count=4, failures=failures)
    assert 'class="column warn"' in html
    assert "3/4 只自选股行情获取失败" in html
    assert "东方财富网络异常：600519、000858" in html
    assert "未找到标的：300750" in html
    assert 'class="row bar up"' in html


def test_all_failed_page_has_warning_and_no_bars() -> None:
    failures = [Failure(label="600519", reason="东方财富网络异常")]
    html = build_my_stock_html(quotes=[], index_quotes=[None] * 4, saved_count=1, failures=failures)
    assert 'class="column warn"' in html
    assert 'class="row bar ' not in html
    assert "暂无可展示的行情" in html and "涨跌幅暂无数据" in html
