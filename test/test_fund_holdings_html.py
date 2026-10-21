"""基金持仓 HTML 结构（不依赖 pytakumi / 网络）。"""

from __future__ import annotations

import re

from SayuStock.utils.market.display import DisplayItem
from SayuStock.utils.fund_holdings_html import (
    FOOT_PAD,
    CONTENT_L,
    CONTENT_W,
    FundHoldingRow,
    build_fund_holdings_html,
    fund_holdings_canvas_size,
)


def _row(
    code: str = "600519",
    name: str = "贵州茅台 (沪深A)",
    *,
    weight: str = "8.50",
    price: float | None = 1680.0,
    change: float | None = 1.23,
    failed: bool = False,
    reason: str = "",
) -> FundHoldingRow:
    return FundHoldingRow(
        code=code,
        name=name,
        weight=weight,
        price=price,
        change_pct=change,
        turnover_rate=0.56,
        amount=3.4e9,
        failed=failed,
        reason=reason,
    )


def _build(rows: list[FundHoldingRow], **kw: object) -> str:
    return build_fund_holdings_html(
        fund_name="易方达蓝筹精选",
        fund_code="1.000005",
        rows=rows,
        index_items=[DisplayItem(name="上证指数", price=3930.12, change_pct=-0.3, code="000001")],
        title_num="5",
        **kw,  # type: ignore[arg-type]
    )


def _css_px(html: str, selector: str, prop: str) -> int:
    block = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", html)
    assert block is not None
    found = re.search(rf"{prop}:\s*(-?\d+)px", block.group(1))
    assert found is not None
    return int(found.group(1))


def test_html_shows_weight_price_and_sparkline() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
    html = _build([_row()], sparklines={"600519": svg})
    assert "width: 900px" in html
    assert "易方达蓝筹精选" in html
    assert "1.000005" in html
    assert "贵州茅台" in html
    assert "8.50%" in html
    assert "1680.0" in html
    assert "+1.23%" in html
    assert 'class="spark"' in html
    assert "<svg" in html
    assert "图例" in html
    assert "基金持仓权重" in html
    assert "当日分时" in html
    assert 'class="bar"' in html


def test_failed_row_shows_failure_in_pct_slot() -> None:
    """失败写在原本涨跌幅那一格，不另挂横幅。"""
    html = _build(
        [
            _row(
                code="300750",
                name="宁德时代",
                weight="6.20",
                price=None,
                change=None,
                failed=True,
                reason="东方财富网络异常",
            )
        ]
    )
    assert 'class="warn-inline"' in html
    assert "⚠ 东方财富网络异常" in html
    assert "失败" in html
    assert "未获取" not in html
    # 没拿到数值的行不许出现破折号占位
    assert "—" not in html
    assert "价 -" not in html


def test_page_has_no_failure_banner() -> None:
    """失败横幅取消：失败只在自己那一行标出来。"""
    html = _build([_row(failed=True, reason="东方财富网络异常")])
    assert 'class="warn"' not in html
    assert "只重仓股行情获取失败" not in html


def test_canvas_size_scales_with_rows() -> None:
    _, h1 = fund_holdings_canvas_size(1)
    _, h3 = fund_holdings_canvas_size(3)
    assert h3 - h1 == 2 * 110
    assert h1 > FOOT_PAD


def _inline_style(html: str, selector: str) -> dict[str, str]:
    """取元素内联 style 里的 left / width。"""
    block = re.search(rf'<div class="{re.escape(selector.lstrip("."))}" style="([^"]*)"', html)
    assert block is not None, selector
    return dict(re.findall(r"([a-z-]+):\s*(-?\d+)px", block.group(1)))


def test_all_cards_share_one_content_column() -> None:
    """全页卡片按同一列对齐（行卡片可见区 30..869）。"""
    html = _build([_row()])
    for selector in (".fund", ".legend", ".sep", ".src"):
        assert _css_px(html, selector, "left") == CONTENT_L, selector
        assert _css_px(html, selector, "width") == CONTENT_W, selector


def test_index_cards_share_the_content_column() -> None:
    html = _build([_row()])
    assert _css_px(html, ".idx", "width") == 200
    # 4 张卡 + 3 个间隔 = 整列宽
    assert 4 * 200 + 3 * 13 == CONTENT_W


def test_rows_use_gold_asset_only_for_failed_row() -> None:
    """正常行用红/绿/灰条（和「我的自选」同一套），金色条只给取数失败的行。"""
    from base64 import b64encode

    from SayuStock.utils.fund_holdings_html import (
        TEXT_PATH,
        BAR_ASSET_UP,
        BAR_ASSET_FAILED,
    )

    def _uri(name: str) -> str:
        """按磁盘原始字节算 data URI。别用 PIL 重新编码再比——
        不同 Pillow 版本的 PNG 编码器输出不一样，会假失败。"""
        raw = (TEXT_PATH / name).read_bytes()
        return "data:image/png;base64," + b64encode(raw).decode("ascii")

    up_html = _build([_row(change=1.23)])
    assert _uri(BAR_ASSET_UP) in up_html
    assert _uri(BAR_ASSET_FAILED) not in up_html

    fail_html = _build([_row(failed=True, reason="东方财富网络异常")])
    assert _uri(BAR_ASSET_FAILED) in fail_html
    assert _uri(BAR_ASSET_UP) not in fail_html


def test_up_down_rows_keep_red_green_semantics() -> None:
    """涨跌靠红/绿，不靠别的颜色：金底只属于失败行。"""
    html = _build([_row(change=1.23), _row(code="300750", name="宁德时代", change=-2.04)])
    assert "+1.23%" in html
    assert "-2.04%" in html
    # 副标题跟着涨跌染色（与「我的自选」一致）
    assert "rgb(213,102,102)" in html
    assert "rgb(175,231,170)" in html


def test_gold_asset_keeps_source_alpha_structure() -> None:
    """金色素材是 myup 转色相来的：alpha 结构必须逐点一致。"""
    from PIL import Image

    from SayuStock.utils.fund_holdings_html import TEXT_PATH, BAR_ASSET_FAILED

    src = Image.open(TEXT_PATH / "myup.png").convert("RGBA")
    gold = Image.open(TEXT_PATH / BAR_ASSET_FAILED).convert("RGBA")
    assert gold.size == src.size
    # getdata() 在 Pillow 14 会移除，比对字节用 tobytes()
    assert gold.getchannel("A").tobytes() == src.getchannel("A").tobytes()
    r, g, b, _a = gold.getpixel((762, 55))
    assert r > g > b, "药丸应是金色系（红 > 绿 > 蓝）"
    assert 150 < r < 220, f"药丸亮度应贴近原素材，实际 {r}"


def test_source_sits_above_footer() -> None:
    html = _build([_row()], sources=("eastmoney", "sina"))
    _, height = fund_holdings_canvas_size(1)
    src_top = _css_px(html, ".src", "top")
    src_h = _css_px(html, ".src", "height")
    foot_top = _css_px(html, ".footer", "top")
    foot_h = _css_px(html, ".footer", "height")
    assert src_top >= height - FOOT_PAD
    assert src_top + src_h <= foot_top
    assert foot_top + foot_h <= height
    assert "数据来源：" in html
