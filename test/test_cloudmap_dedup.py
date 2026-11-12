"""大盘/板块云图去重测试：同代码去重、同名不同代码标签消歧。"""

from __future__ import annotations

from SayuStock.render.treemap import treemap_html, prepare_treemap
from SayuStock.utils.render_data import CloudmapRenderData, build_cloudmap_render_data
from SayuStock.utils.market.enums import BoardKind
from SayuStock.utils.market.models import BoardRow, BoardSnapshot


def _make_row(code: str, name: str, industry: str, market_cap: float, change_pct: float) -> BoardRow:
    return BoardRow(
        code=code,
        name=name,
        price=100.0,
        change_pct=change_pct,
        amount=1e8,
        market_cap=market_cap,
        industry=industry,
        lead_name=None,
        lead_change_pct=None,
    )


def test_cloudmap_dedup_cross_page_same_code() -> None:
    """测试分页抖动导致同一股票代码重复出现时，保留市值较大的一条且不重复。"""
    rows = (
        _make_row("AMD", "超威半导体", "信息技术", 8.539e11, 2.38),
        _make_row("AMD", "超威半导体", "信息技术", 8.536e11, 2.34),
        _make_row("NVDA", "英伟达", "信息技术", 4.074e12, -5.39),
    )
    snap = BoardSnapshot(kind=BoardKind.CUSTOM, title="美股", rows=rows)
    data = build_cloudmap_render_data(snap, "大盘云图", "美股", layer=2)
    assert isinstance(data, CloudmapRenderData)
    amd_rows = [leaf for leaf in data.leaves if leaf.code == "AMD"]
    assert len(amd_rows) == 1
    assert amd_rows[0].diff_val == 2.38
    assert amd_rows[0].value == 8.539e11


def test_cloudmap_dedup_same_category_same_name_diff_code() -> None:
    """同名不同代码是不同标的，在树图中明确区分。"""
    rows = (
        _make_row("NEE_S", "NextEra Energy", "公用事业", 1.100e11, -0.59),
        _make_row("NEE_T", "NextEra Energy", "公用事业", 1.020e11, -0.51),
        _make_row("NEE_V", "NextEra Energy", "公用事业", 9.838e10, -0.55),
        _make_row("EXC", "爱克斯龙", "公用事业", 4.715e10, -0.19),
    )
    snap = BoardSnapshot(kind=BoardKind.CUSTOM, title="美股", rows=rows)
    data = build_cloudmap_render_data(snap, "大盘云图", "美股", layer=2)
    assert isinstance(data, CloudmapRenderData)
    nee_rows = [leaf for leaf in data.leaves if leaf.name == "NextEra Energy"]
    assert len(nee_rows) == 3
    assert {leaf.code for leaf in nee_rows} == {"NEE_S", "NEE_T", "NEE_V"}
    page = prepare_treemap(snap, "大盘云图", "美股", layer=2)
    assert not isinstance(page, str)
    assert {tile.name for tile in page.tiles} >= {
        "NextEra Energy · NEE_S",
        "NextEra Energy · NEE_T",
        "NextEra Energy · NEE_V",
    }


def test_cloudmap_to_fig_no_question_mark_in_leaf_nodes() -> None:
    """测试存在重复候选时，最终叶子节点不出现无法归属的占位。"""
    rows = (
        _make_row("AMD", "超威半导体", "信息技术", 8.539e11, 2.38),
        _make_row("AMD", "超威半导体", "信息技术", 8.536e11, 2.34),
        _make_row("SPCX", "SpaceX", "工业", 1.929e12, 0.01),
        _make_row("SPCX", "SpaceX", "工业", 1.928e12, -0.04),
        _make_row("GS", "高盛", "金融", 2.615e11, 0.08),
        _make_row("GS", "高盛", "金融", 2.614e11, 0.05),
    )
    snap = BoardSnapshot(kind=BoardKind.CUSTOM, title="美股", rows=rows)
    page = prepare_treemap(snap, "大盘云图", "美股", layer=2)
    assert not isinstance(page, str)
    assert len(page.tiles) == 3
    assert "(?)" not in treemap_html(page)
