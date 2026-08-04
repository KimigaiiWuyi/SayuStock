"""末端 / 点标注避让与图例明细格式单测。"""

from __future__ import annotations

import sys
import importlib.util
from types import ModuleType
from pathlib import Path
from unittest.mock import MagicMock

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_end_label_dodge_test"


def _ensure_pkg() -> None:
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        PKG_ROOT / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT)],
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg

    for sub in ("utils", "stock_stockinfo"):
        mod = ModuleType(f"{PKG_NAME}.{sub}")
        mod.__path__ = [str(PKG_ROOT / sub)]
        sys.modules[f"{PKG_NAME}.{sub}"] = mod

    # chart_base 需要 FONT_ORIGIN_PATH。优先真实 gsuid_core（全量 CI 嵌套布局
    # 下可用）；失败时再注入占位。切勿用无 __path__ 的 ModuleType 覆盖父包，
    # 也切勿覆盖已加载的 fonts（会丢掉 core_font，拖垮同进程其它用例收集）。
    if "gsuid_core.utils.fonts.fonts" not in sys.modules:
        try:
            import gsuid_core.utils.fonts.fonts  # noqa: F401
        except Exception:
            fonts_mod = ModuleType("gsuid_core.utils.fonts.fonts")
            fonts_mod.FONT_ORIGIN_PATH = Path("/nonexistent")
            fonts_mod.core_font = lambda size: __import__(
                "PIL.ImageFont", fromlist=["ImageFont"]
            ).ImageFont.load_default()

            def _ensure_ns_pkg(name: str) -> None:
                m = sys.modules.get(name)
                if m is not None and getattr(m, "__path__", None) is not None:
                    return
                pkg = ModuleType(name)
                pkg.__path__ = []  # type: ignore[attr-defined]
                pkg.__package__ = name
                sys.modules[name] = pkg

            _ensure_ns_pkg("gsuid_core")
            _ensure_ns_pkg("gsuid_core.utils")
            _ensure_ns_pkg("gsuid_core.utils.fonts")
            sys.modules["gsuid_core.utils.fonts.fonts"] = fonts_mod

    compat = ModuleType(f"{PKG_NAME}.utils.mplchart_compat")
    for name in (
        "SMA",
        "Pane",
        "Chart",
        "HLine",
        "Price",
        "Volume",
        "BarPlot",
        "LinePlot",
        "Indicator",
        "Candlesticks",
    ):
        setattr(compat, name, MagicMock(name=name))
    sys.modules[f"{PKG_NAME}.utils.mplchart_compat"] = compat


def _load_chart_base():
    _ensure_pkg()
    name = f"{PKG_NAME}.stock_stockinfo.chart_base"
    if name in sys.modules and hasattr(sys.modules[name], "_dodge_end_label_offsets"):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        PKG_ROOT / "stock_stockinfo" / "chart_base.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


chart_base = _load_chart_base()
_dodge_end_label_offsets = chart_base._dodge_end_label_offsets
_dodge_label_point_offsets = chart_base._dodge_label_point_offsets
_estimate_text_box_pts = chart_base._estimate_text_box_pts
_leader_arrowprops = chart_base._leader_arrowprops
_format_detail_legend_label = chart_base._format_detail_legend_label
_pct_change = chart_base._pct_change


def _make_ax(y_min: float = -10.0, y_max: float = 10.0):
    fig, ax = plt.subplots(figsize=(8, 6), dpi=100)
    ax.set_xlim(0, 10)
    ax.set_ylim(y_min, y_max)
    return fig, ax


def test_dodge_identical_ys_are_spread():
    fig, ax = _make_ax()
    try:
        ys = [1.0, 1.0, 1.0]
        offsets = _dodge_end_label_offsets(ys, ax, min_sep_pts=20.0)
        assert len(offsets) == 3
        y_offs = sorted(o[1] for o in offsets)
        assert y_offs[1] - y_offs[0] >= 18.0
        assert y_offs[2] - y_offs[1] >= 18.0
        assert all(o[0] > 0 for o in offsets)
    finally:
        plt.close(fig)


def test_dodge_with_explicit_heights_spreads_more():
    """高多行标签应按框高拉开，而不是固定 20pt 中心距。"""
    fig, ax = _make_ax(y_min=-5, y_max=5)
    try:
        ys = [0.0, 0.0, 0.0]
        heights = [40.0, 40.0, 40.0]
        offsets = _dodge_end_label_offsets(ys, ax, min_sep_pts=12.0, heights_pts=heights)
        y_offs = sorted(o[1] for o in offsets)
        # 半高 20 + gap → 中心距至少约 40pt 量级
        assert y_offs[1] - y_offs[0] >= 28.0
        assert y_offs[2] - y_offs[1] >= 28.0
    finally:
        plt.close(fig)


def test_dodge_well_separated_ys_keep_near_zero_offset():
    fig, ax = _make_ax(y_min=-50, y_max=50)
    try:
        ys = [-30.0, 0.0, 30.0]
        offsets = _dodge_end_label_offsets(ys, ax, min_sep_pts=20.0)
        assert len(offsets) == 3
        for _, y_off in offsets:
            assert abs(y_off) < 5.0
    finally:
        plt.close(fig)


def test_dodge_close_but_not_equal_ys_still_separate():
    fig, ax = _make_ax()
    try:
        ys = [2.1, 2.3, 2.5]
        offsets = _dodge_end_label_offsets(ys, ax, min_sep_pts=20.0)
        y_offs = [o[1] for o in offsets]
        assert max(abs(o) for o in y_offs) > 5.0
        assert (max(y_offs) - min(y_offs)) >= 18.0
    finally:
        plt.close(fig)


def test_dodge_empty_and_single():
    fig, ax = _make_ax()
    try:
        assert _dodge_end_label_offsets([], ax) == []
        single = _dodge_end_label_offsets([3.5], ax)
        assert len(single) == 1
        assert single[0][1] == 0.0
        assert single[0][0] > 0
    finally:
        plt.close(fig)


def test_point_offsets_repel_overlapping_preferred():
    fig, ax = _make_ax()
    try:
        xy = [(1.0, 1.0), (1.0, 1.05), (1.0, 1.1)]
        preferred = [(-14.0, 14.0), (-14.0, 14.0), (-14.0, 14.0)]
        sizes = [(80.0, 36.0), (80.0, 36.0), (80.0, 36.0)]
        offsets = _dodge_label_point_offsets(xy, preferred, ax, min_sep_pts=6.0, sizes_pts=sizes)
        assert len(offsets) == 3
        assert len({(round(o[0], 2), round(o[1], 2)) for o in offsets}) >= 2
        y_offs = [o[1] for o in offsets]
        assert max(y_offs) - min(y_offs) >= 12.0
    finally:
        plt.close(fig)


def test_point_offsets_aabb_for_multiline_near_same_point():
    """同点附近的多行大标签应被推开到框不重叠。"""
    fig, ax = _make_ax(y_min=-20, y_max=20)
    try:
        xy = [(5.0, 0.0), (5.0, 0.2), (5.0, -0.2), (5.1, 0.0)]
        preferred = [(-20.0, 20.0)] * 4
        sizes = [(90.0, 48.0)] * 4
        offsets = _dodge_label_point_offsets(xy, preferred, ax, min_sep_pts=6.0, sizes_pts=sizes, max_iter=150)
        # 四个 offset 不应全相同
        uniq = {(round(o[0], 1), round(o[1], 1)) for o in offsets}
        assert len(uniq) >= 3
        # 至少一对在 y 上拉开
        ys = [o[1] for o in offsets]
        assert max(ys) - min(ys) >= 20.0
    finally:
        plt.close(fig)


def test_estimate_text_box_grows_with_lines():
    w1, h1 = _estimate_text_box_pts("甲", 10.0)
    w2, h2 = _estimate_text_box_pts("甲\n涨幅 +1.2%\n区间最大涨幅 +3%", 10.0)
    assert h2 > h1 * 2
    assert w2 >= w1


def test_leader_arrow_is_dashed():
    props = _leader_arrowprops("#fff")
    assert "linestyle" in props
    ls = props["linestyle"]
    assert ls == "-" or (isinstance(ls, tuple) and len(ls) == 2)


def test_detail_legend_label_format():
    label = _format_detail_legend_label("贵州茅台", 1600.0, 1680.0)
    assert "贵州茅台" in label
    assert "起" in label and "末" in label
    assert "1600" in label or "1600.0" in label
    assert "+" in label or "5.00%" in label
    assert abs(_pct_change(100.0, 110.0) - 10.0) < 1e-9
    assert _pct_change(0.0, 10.0) == 0.0
