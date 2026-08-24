"""全天候 HTML：每格有国旗/资源 emoji，加密货币仍是 4 个不重复。"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

import pytest

from SayuStock.utils.constant import bond, whsc, crypto, i_code, commodity
from SayuStock.utils.time_range import has_session_started_today
from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.all_weather_html import (
    _STALE_PRICE,
    _FALLBACK_EMOJI,
    format_price,
    display_label,
    format_change,
    resolve_emoji,
    build_all_weather_html,
)

_TEST_OUTPUT = Path(__file__).resolve().parent.parent / "test_output"


def _item(name: str, price: float = 1.0, chg: float = 0.5) -> DisplayItem:
    return DisplayItem(name=name, price=price, change_pct=chg)


def test_every_configured_slot_has_resource_emoji() -> None:
    tables = (i_code, commodity, bond, whsc, crypto)
    missing: list[str] = []
    for table in tables:
        for name in table:
            if resolve_emoji(name) == _FALLBACK_EMOJI:
                missing.append(name)
    assert missing == []


def test_html_puts_emoji_left_of_names() -> None:
    sections = [
        ("国际市场", [_item("上证指数", 3905.2, 0.04), _item("日经225", 66016.36, -0.3)]),
        ("大宗商品", [_item("NYMEX原油", 86.64, -0.22), _item("生猪主连", 12250.0, 0.25)]),
        ("加密货币", [_item("BTC", 76617.0, -0.49), _item("ETH", 2415.33, 0.18)]),
    ]
    html = build_all_weather_html(sections, now=datetime(2026, 8, 24, 10, 0))
    assert "🇨🇳" in html
    assert "🇯🇵" in html
    assert "🛢️" in html
    assert "🐷" in html
    assert "🟠" in html
    assert 'class="nm"' in html
    assert html.index("🇨🇳") < html.index("上证指数")
    assert html.index("🛢️") < html.index("NYMEX原油")
    assert "+0.04%" in html
    assert "-0.30%" in html
    assert "2026-08-24 10:00" in html
    assert "width: 1000px" in html
    assert "height: 2200px" in html
    assert "data:image/jpeg;base64," in html
    assert "data:image/png;base64," in html
    assert "now-line" in html
    assert "日股" in html
    assert "韩股" in html
    assert "加密" in html
    assert "10:00" in html
    assert "tname on" in html
    assert "track off" in html
    assert html.count("track on") >= 3
    assert html.count("track off") >= 2
    assert "#ef4444" in html
    assert "box-shadow:" in html
    assert "border:1px solid #ffffff" in html


def test_crypto_section_stays_four_unique() -> None:
    items = [_item(n) for n in crypto]
    html = build_all_weather_html([("加密货币", items)])
    for name in ("BTC", "ETH", "SOL", "XRP"):
        assert name in html
    assert html.count('class="tile') == 4


def test_long_names_are_shortened() -> None:
    assert display_label("罗素2000价值股ETF-iShares") == "罗素2000"
    assert "国债" in display_label("美国30年期国债收益率")


def test_price_and_change_format() -> None:
    assert format_price(3905.20) == "3905.2"
    assert format_price(2.1931) == "2.1931"
    assert format_change(0.04) == "+0.04%"
    assert format_change(-0.3) == "-0.30%"


_SAMPLE: dict[str, tuple[float, float]] = {
    "上证指数": (3905.2, 0.04),
    "恒生指数": (26009.46, 1.21),
    "日经225": (66016.36, -0.3),
    "韩国KOSPI200": (1096.25, 1.41),
    "纳斯达克": (26180.45, 0.43),
    "道琼斯": (53277.01, 0.98),
    "标普500": (7674.37, 0.43),
    "罗素2000价值股ETF-iShares": (224.81, 0.45),
    "欧洲斯托克600": (654.18, 0.59),
    "英国富时100": (10816.56, 0.64),
    "法国CAC40": (8484.43, 0.37),
    "德国DAX30": (26136.56, 0.59),
    "XAU": (4604.09, 1.88),
    "XAG": (68.99, 1.32),
    "综合铜03": (14185.0, 1.02),
    "NYMEX原油": (86.64, -0.22),
    "螺纹钢主连": (3061.0, 0.76),
    "豆粕主连": (3246.0, 0.06),
    "焦煤主连": (1609.0, 2.42),
    "生猪主连": (12250.0, 0.25),
    "中国30年期国债": (2.1931, 0.16),
    "中国10年期国债": (1.7085, 0.26),
    "中国2年期国债": (1.2566, 0.65),
    "美国30年期国债收益率": (5.272, 0.4),
    "美国10年期国债收益率": (4.7339, 0.51),
    "美国2年期国债收益率": (4.238, 1.05),
    "JP 30Y": (4.06, 1.3),
    "JP 10Y": (2.87, 0.77),
    "美元兑离岸人民币": (6.7214, -0.06),
    "美元兑瑞郎": (0.8013, 0.11),
    "美元兑日元": (158.9803, -0.06),
    "美元指数": (98.85, -0.02),
    "BTC": (76617.0, -0.49),
    "ETH": (2415.33, 0.18),
    "SOL": (93.4, 0.35),
    "XRP": (1.484, 3.09),
}


def _sample_items(table: dict[str, str]) -> list[DisplayItem]:
    out: list[DisplayItem] = []
    for name in table:
        price, chg = _SAMPLE.get(name, (1.0, 0.0))
        out.append(DisplayItem(name=name, price=price, change_pct=chg, code=table[name]))
    return out


def test_has_session_started_today_asia_vs_us() -> None:
    morning = datetime(2026, 8, 24, 10, 0)
    assert has_session_started_today("1.000001", morning) is True
    assert has_session_started_today("100.HSI", morning) is True
    assert has_session_started_today("100.NDX", morning) is False
    assert has_session_started_today("100.SXXP", morning) is False
    assert has_session_started_today("BTC", morning) is True
    assert has_session_started_today("171.US10Y", morning) is True
    assert has_session_started_today("171.CN10Y", morning) is True
    assert has_session_started_today("JP.BOND", morning) is True
    evening = datetime(2026, 8, 24, 23, 0)
    assert has_session_started_today("100.NDX", evening) is True
    assert has_session_started_today("171.US10Y", evening) is True


def test_sunday_all_regular_markets_are_closed() -> None:
    sunday = datetime(2026, 8, 23, 15, 0)
    for code in (
        "1.000001",
        "100.HSI",
        "100.N225",
        "100.KOSPI200",
        "100.NDX",
        "100.SXXP",
        "122.XAU",
        "102.CL00Y",
        "113.rbm",
        "171.CN10Y",
        "171.US10Y",
    ):
        assert has_session_started_today(code, sunday) is False, code
    for code in ("BTC", "ETH"):
        assert has_session_started_today(code, sunday) is True, code
    for code in ("119.USDJPY", "133.USDCNH", "100.UDI"):
        assert has_session_started_today(code, sunday) is False, code

    html = build_all_weather_html(
        [
            ("国际市场", _sample_items(i_code)),
            ("大宗商品", _sample_items(commodity)),
            ("债券市场", _sample_items(bond)),
            ("外汇市场", _sample_items(whsc)),
            ("加密货币", _sample_items(crypto)),
        ],
        now=sunday,
    )
    assert html.count("[休]") == len(i_code) + len(commodity) + len(bond) + len(whsc)


def test_cn_and_us_holidays_close_session() -> None:
    pytest.importorskip("holidays")
    assert has_session_started_today("1.000001", datetime(2026, 10, 1, 10, 0)) is False
    assert has_session_started_today("100.NDX", datetime(2026, 5, 25, 23, 0)) is False
    assert has_session_started_today("1.000001", datetime(2026, 8, 24, 10, 0)) is True
    assert has_session_started_today("119.USDJPY", datetime(2026, 8, 24, 10, 0)) is True
    assert has_session_started_today("119.USDJPY", datetime(2026, 1, 1, 20, 0)) is False
    assert has_session_started_today("119.USDJPY", datetime(2026, 1, 1, 10, 0)) is False
    # 退伍军人日：美债联邦假休；NYSE 开市
    assert has_session_started_today("171.US10Y", datetime(2026, 11, 11, 10, 0)) is False
    assert has_session_started_today("171.US10Y", datetime(2026, 11, 11, 23, 0)) is False
    assert has_session_started_today("100.NDX", datetime(2026, 11, 11, 23, 0)) is True


def test_holidays_generated_for_future_years() -> None:
    pytest.importorskip("holidays")
    """不靠手写年份表：2027 元旦、美股马丁·路德·金日也应休市。"""
    assert has_session_started_today("1.000001", datetime(2027, 1, 1, 10, 0)) is False
    assert has_session_started_today("100.NDX", datetime(2027, 1, 18, 23, 0)) is False
    assert has_session_started_today("1.000001", datetime(2027, 8, 23, 10, 0)) is True
    assert has_session_started_today("BTC", datetime(2027, 1, 1, 10, 0)) is True


def test_weekday_morning_us_treasury_live_equity_rest() -> None:
    """周一 10:00 BJT：美股未开；美债近 23h 已开；中债/日债已开。"""
    now = datetime(2026, 8, 24, 10, 0)
    html = build_all_weather_html(
        [
            (
                "国际市场",
                [
                    DisplayItem(name="上证指数", price=3905.2, change_pct=0.04, code="1.000001"),
                    DisplayItem(name="纳斯达克", price=26180.45, change_pct=0.43, code="100.NDX"),
                ],
            ),
            (
                "债券市场",
                [
                    DisplayItem(name="中国10年期国债", price=1.7085, change_pct=0.26, code="171.CN10Y"),
                    DisplayItem(name="美国10年期国债收益率", price=4.7339, change_pct=0.51, code="171.US10Y"),
                    DisplayItem(name="JP 10Y", price=2.87, change_pct=0.77, code=""),
                ],
            ),
        ],
        now=now,
    )
    assert html.count("[休]") == 1
    assert "tile stale" in html
    assert html.count("tile stale") == 1


def test_preopen_asia_us_treasury_already_live() -> None:
    """周一 07:00 BJT：A 股/日债未开，美债电子盘已开。"""
    now = datetime(2026, 8, 24, 7, 0)
    assert has_session_started_today("171.US10Y", now) is True
    assert has_session_started_today("1.000001", now) is False
    assert has_session_started_today("JP.BOND", now) is False
    html = build_all_weather_html(
        [
            (
                "债券市场",
                [
                    DisplayItem(name="美国10年期国债收益率", price=4.73, change_pct=0.5, code="171.US10Y"),
                    DisplayItem(name="JP 10Y", price=2.87, change_pct=0.77, code=""),
                ],
            )
        ],
        now=now,
    )
    assert html.count("[休]") == 1
    assert html.count("tile stale") == 1


def test_preopen_us_price_is_gray_asia_is_live() -> None:
    """周一 10:00 BJT：美股未开盘，A 股已开盘。"""
    now = datetime(2026, 8, 24, 10, 0)
    html = build_all_weather_html(
        [
            (
                "国际市场",
                [
                    DisplayItem(name="上证指数", price=3905.2, change_pct=0.04, code="1.000001"),
                    DisplayItem(name="纳斯达克", price=26180.45, change_pct=0.43, code="100.NDX"),
                ],
            )
        ],
        now=now,
    )
    assert "tile stale" in html
    assert _STALE_PRICE in html
    assert html.count("tile stale") == 1
    assert "[休]" in html
    assert html.count("[休]") == 1
    assert "opacity: 0.6" in html
    assert "+0.43%" in html
    assert "+0.04%" in html


def test_pytakumi_renders_png_header() -> None:
    pytest.importorskip("pytakumi")
    import asyncio

    from gsuid_core.utils.html_render import render_html_to_bytes
    from SayuStock.utils.all_weather_html import CSS_WIDTH, CSS_HEIGHT

    sections = [
        ("国际市场", _sample_items(i_code)),
        ("大宗商品", _sample_items(commodity)),
        ("债券市场", _sample_items(bond)),
        ("外汇市场", _sample_items(whsc)),
        ("加密货币", _sample_items(crypto)),
    ]
    html = build_all_weather_html(sections, now=datetime(2026, 8, 24, 10, 0))
    assert "-0.30%" in html

    async def _go() -> bytes:
        return await render_html_to_bytes(
            html,
            max_width=float(CSS_WIDTH * 2),
            dpi=192.0,
            device_height=float(CSS_HEIGHT * 2),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(CSS_WIDTH),
        )

    png = asyncio.run(_go())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 8000
    _TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    (_TEST_OUTPUT / "all_weather.png").write_bytes(png)
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(png))
    ratio = im.size[1] / im.size[0]
    assert abs(ratio - 2.2) < 0.05, im.size
