"""用本地 data 缓存渲染全天候 / 我的自选到 test_output，不请求东财。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime
from dataclasses import replace

import pytest
from offline_cache import cache_dir, load_series, load_board_items
from test_sparkline import make_crypto_series

from SayuStock.utils.constant import bond, whsc, crypto, i_code, commodity
from SayuStock.utils.sparkline import build_sparkline_svg, sparkline_from_series
from SayuStock.utils.my_stock_html import SPARK_H, SPARK_W, build_my_stock_html, my_stock_canvas_size
from SayuStock.utils.market.display import DisplayItem, from_quote, pick_display_items
from SayuStock.utils.all_weather_html import (
    SPARK_H as AW_SPARK_H,
    SPARK_W as AW_SPARK_W,
    em_secid,
    build_all_weather_html,
    all_weather_canvas_size,
)

_TEST_OUTPUT = Path(__file__).resolve().parent.parent / "test_output"
_WATCH = [
    "1.512880",
    "0.159996",
    "0.399997",
    "1.512070",
    "1.512200",
    "0.159517",
    "1.513010",
    "1.517900",
    "0.159745",
]
_DIFF_MAP: dict[float, str] = {
    3.3: "1",
    2.7: "2",
    2: "3",
    1: "4",
    0: "5",
    -0.5: "6",
    -1.3: "7",
    -2.1: "8",
    -3.1: "9",
    -4: "10",
}
_JP_FALLBACK: dict[str, tuple[float, float]] = {
    "JP 30Y": (4.06, 1.3),
    "JP 10Y": (2.87, 0.77),
}
_SAMPLE_CRYPTO: dict[str, tuple[float, float]] = {
    "BTC": (76617.0, -0.49),
    "ETH": (2415.33, 0.18),
    "SOL": (93.4, 0.35),
    "XRP": (1.484, 3.09),
}


def _walk_spark(price: float, chg: float) -> str:
    open_px = price / (1.0 + chg / 100.0) if chg != -100 else price
    n = 48
    prices = [
        open_px + (price - open_px) * i / (n - 1) + abs(price) * 0.003 * (1 if i % 2 == 0 else -1) for i in range(n)
    ]
    prices[-1] = price
    return build_sparkline_svg(prices, open_px, up=chg >= 0, width=AW_SPARK_W, height=AW_SPARK_H)


def _skip_no_cache() -> Path:
    data = cache_dir()
    if data is None:
        pytest.skip("无本地 SayuStock data 缓存")
    return data


def _render(html: str, width: int, height: int) -> bytes:
    pytest.importorskip("pytakumi")
    from gsuid_core.utils.html_render import render_html_to_bytes

    async def _go() -> bytes:
        return await render_html_to_bytes(
            html,
            max_width=float(width * 2),
            dpi=192.0,
            device_height=float(height * 2),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(width),
        )

    return asyncio.run(_go())


def test_all_weather_crypto_sparks_without_cache() -> None:
    """不依赖东财 JSON：合成加密分时也应写入 spark 槽（CI 必跑）。"""
    as_of = datetime(2026, 9, 5, 17, 2)
    items: list[DisplayItem] = []
    sparks: dict[str, str] = {}
    for name, (price, chg) in _SAMPLE_CRYPTO.items():
        items.append(DisplayItem(name=name, price=price, change_pct=chg, code=crypto[name]))
        svg = sparkline_from_series(
            make_crypto_series(name, price, chg, as_of=as_of),
            width=AW_SPARK_W,
            height=AW_SPARK_H,
        )
        assert svg
        sparks[name] = svg
    html = build_all_weather_html([("加密货币", items)], now=as_of, sparklines=sparks)
    assert "has-spark" in html
    assert html.count('class="spark"') == 4
    assert "polyline" in html


def test_all_weather_png_skips_when_cache_has_no_series(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("test_offline_card_render.cache_dir", lambda: tmp_path)
    with pytest.raises(pytest.skip.Exception, match="分时缓存不足"):
        test_offline_all_weather_png()


def test_offline_all_weather_png() -> None:
    data = _skip_no_cache()
    intl = load_board_items(data, "国际市场")
    sparks: dict[str, str] = {}

    def _section(table: dict[str, str]) -> list[DisplayItem]:
        pool: dict[str, DisplayItem] = {}
        for name, code in table.items():
            if not code:
                continue
            series = load_series(data, code)
            if series is None or series.quote is None:
                if name in _JP_FALLBACK:
                    p, c = _JP_FALLBACK[name]
                    pool[name] = DisplayItem(name=name, price=p, change_pct=c, code=code)
                continue
            item = from_quote(series.quote)
            if name != item.name:
                item = replace(item, name=name)
            pool[item.name] = item
            svg = sparkline_from_series(series, width=AW_SPARK_W, height=AW_SPARK_H)
            if svg:
                sparks[item.name] = svg
                sparks[code] = svg
        return pick_display_items(pool, table)

    as_of = datetime(2026, 9, 5, 17, 2)
    crypto_items: list[DisplayItem] = []
    for name, (price, chg) in _SAMPLE_CRYPTO.items():
        crypto_items.append(DisplayItem(name=name, price=price, change_pct=chg, code=crypto[name]))
        series = make_crypto_series(name, price, chg, as_of=as_of)
        svg = sparkline_from_series(series, width=AW_SPARK_W, height=AW_SPARK_H)
        if svg:
            sparks[name] = svg
            sparks[crypto[name]] = svg
    intl_items = pick_display_items(intl, i_code)
    for item in intl_items:
        load_id = ""
        for name, code in i_code.items():
            if name != item.name and name not in item.name and item.name not in name:
                continue
            load_id = em_secid(code)
            break
        if not load_id and item.code:
            load_id = em_secid(item.code)
        series = load_series(data, load_id) if load_id else None
        svg = ""
        if series is not None:
            svg = sparkline_from_series(series, width=AW_SPARK_W, height=AW_SPARK_H)
        if not svg:
            svg = _walk_spark(item.price, item.change_pct)
        if svg:
            sparks[item.name] = svg
            if item.code:
                sparks[item.code] = svg
            if load_id:
                sparks[load_id] = svg
    sections = [
        ("国际市场", intl_items),
        ("大宗商品", _section(commodity)),
        ("债券市场", _section(bond)),
        ("外汇市场", _section(whsc)),
        ("加密货币", crypto_items),
    ]
    html = build_all_weather_html(sections, now=as_of, sparklines=sparks)
    spark_n = html.count('class="spark"')
    # 加密 4 + 国际 12（缓存或走步合成）；大宗/债/汇仍靠本地 trends。空目录 skip。
    if spark_n < 12:
        pytest.skip(f"全天候分时缓存不足 spark={spark_n}")
    assert "has-spark" in html
    assert spark_n >= 12
    assert 'class="sec-title"' in html
    assert "data:image/jpeg" not in html
    assert sparks["BTC"]
    width, height = all_weather_canvas_size(sections, sparks)
    png = _render(html, width, height)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    _TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    (_TEST_OUTPUT / "all_weather_spark.png").write_bytes(png)


def test_offline_my_stock_png() -> None:
    data = _skip_no_cache()
    zs = load_board_items(data, "主要指数")
    wanted = ["上证指数", "深证成指", "中证A500", "中证2000"]
    index_items: list[DisplayItem] = []
    for name in wanted:
        for item in zs:
            if name != item.name.split("(")[0].strip() and name not in item.name:
                continue
            index_items.append(item)
            break

    quotes = []
    sparks: dict[str, str] = {}
    all_p = 0.0
    for secid in _WATCH:
        series = load_series(data, secid)
        if series is None or series.quote is None:
            continue
        q = series.quote
        quotes.append((q, secid))
        all_p += float(q.change_pct) if q.change_pct is not None else 0.0
        svg = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
        if svg:
            sparks[secid] = svg
            sparks[q.symbol.code] = svg
    if len(quotes) < 4:
        pytest.skip("自选缓存不足")

    avg_p = all_p / len(quotes)
    title_num = "11"
    for i in _DIFF_MAP:
        if avg_p >= i:
            title_num = _DIFF_MAP[i]
            break

    html_base = build_my_stock_html(quotes=quotes, index_items=index_items, title_num=title_num)
    html_spark = build_my_stock_html(
        quotes=quotes,
        index_items=index_items,
        title_num=title_num,
        sparklines=sparks,
    )
    assert 'class="bar"' in html_base
    assert 'class="spark"' not in html_base
    assert 'class="spark"' in html_spark
    width, height, _ = my_stock_canvas_size(len(quotes))
    png_base = _render(html_base, width, height)
    png_spark = _render(html_spark, width, height)
    _TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    (_TEST_OUTPUT / "my_stock.png").write_bytes(png_base)
    (_TEST_OUTPUT / "my_stock_spark.png").write_bytes(png_spark)
    assert png_base[:8] == b"\x89PNG\r\n\x1a\n"
    assert png_spark[:8] == b"\x89PNG\r\n\x1a\n"
