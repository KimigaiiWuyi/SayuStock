"""大盘/板块云图去重测试：验证分页抖动同代码与同分类同名去重，杜绝叶子节点 (?)。"""

from __future__ import annotations

import asyncio

from plotly.graph_objects import Figure

from SayuStock.utils.render_data import CloudmapRenderData, build_cloudmap_render_data
from SayuStock.utils.market.enums import BoardKind
from SayuStock.utils.market.models import BoardRow, BoardSnapshot
from SayuStock.stock_cloudmap.render import to_fig


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
    df = data.df
    amd_rows = df[df["code"] == "AMD"]
    assert len(amd_rows) == 1
    assert float(amd_rows.iloc[0]["diff_val"]) == 2.38
    assert float(amd_rows.iloc[0]["value"]) == 8.539e11


def test_cloudmap_dedup_same_category_same_name_diff_code() -> None:
    """测试同一分类下同名不同代码（如正股与衍生份额）去重，保留市值最大者。"""
    rows = (
        _make_row("NEE_S", "NextEra Energy", "公用事业", 1.100e11, -0.59),
        _make_row("NEE_T", "NextEra Energy", "公用事业", 1.020e11, -0.51),
        _make_row("NEE_V", "NextEra Energy", "公用事业", 9.838e10, -0.55),
        _make_row("EXC", "爱克斯龙", "公用事业", 4.715e10, -0.19),
    )
    snap = BoardSnapshot(kind=BoardKind.CUSTOM, title="美股", rows=rows)
    data = build_cloudmap_render_data(snap, "大盘云图", "美股", layer=2)
    assert isinstance(data, CloudmapRenderData)
    df = data.df
    nee_rows = df[df["name"] == "NextEra Energy"]
    assert len(nee_rows) == 1
    assert str(nee_rows.iloc[0]["code"]) == "NEE_S"
    assert float(nee_rows.iloc[0]["value"]) == 1.100e11


def test_cloudmap_to_fig_no_question_mark_in_leaf_nodes() -> None:
    """测试存在重复候选时，最终 Plotly Figure 的叶子节点绝不包含 (?)。"""
    rows = (
        _make_row("AMD", "超威半导体", "信息技术", 8.539e11, 2.38),
        _make_row("AMD", "超威半导体", "信息技术", 8.536e11, 2.34),
        _make_row("SPCX", "SpaceX", "工业", 1.929e12, 0.01),
        _make_row("SPCX", "SpaceX", "工业", 1.928e12, -0.04),
        _make_row("GS", "高盛", "金融", 2.615e11, 0.08),
        _make_row("GS", "高盛", "金融", 2.614e11, 0.05),
    )
    snap = BoardSnapshot(kind=BoardKind.CUSTOM, title="美股", rows=rows)
    fig = asyncio.run(to_fig(snap, "大盘云图", "美股", layer=2))
    assert isinstance(fig, Figure)
    trace_data = fig.to_dict()["data"][0]
    labels: list[str] = trace_data["labels"]
    parents: list[str] = trace_data["parents"]
    customdata = trace_data["customdata"]

    for label, parent, cdata in zip(labels, parents, customdata):
        # 仅针对叶子节点（有 parent 的节点为股票叶子节点）
        if parent:
            info_str = str(cdata[0]) if isinstance(cdata, (list, tuple)) else str(cdata)
            assert "(?)" not in info_str, f"叶子节点 {label} (parent={parent}) 包含 (?): {cdata}"
