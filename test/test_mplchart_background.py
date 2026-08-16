"""新版 mplchart 默认浅色；Chart(bgcolor=) 必须落到暗色 root 上。"""

from __future__ import annotations

import pytest

pytest.importorskip("mplchart")
pytest.importorskip("pandas")
pytest.importorskip("matplotlib")

import pandas as pd  # noqa: E402

from SayuStock.utils.mplchart_compat import Chart  # noqa: E402
from SayuStock.stock_stockinfo.chart_base import BG_COLOR, _paint_chart_background  # noqa: E402


def _tiny_prices() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=8, freq="B")
    close = pd.Series([10.0, 10.2, 10.1, 10.4, 10.3, 10.6, 10.5, 10.7], index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close, "volume": 1.0},
        index=idx,
    )


def _rgba(color: object) -> tuple[float, float, float, float]:
    from matplotlib.colors import to_rgba

    return to_rgba(color)


def test_setup_mpl_prefers_static_misans_over_vf() -> None:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    from SayuStock.stock_stockinfo.chart_base import _setup_mpl

    _setup_mpl()
    families = list(plt.rcParams["font.sans-serif"])
    has_static = any(entry.name == "MiSans" for entry in font_manager.fontManager.ttflist)
    if has_static:
        assert families[0] == "MiSans"
        assert "MiSans VF" not in families[:1]


def test_chart_bgcolor_paints_root_dark() -> None:
    chart = Chart(_tiny_prices(), bgcolor=BG_COLOR, figsize=(4, 3))
    fig = chart.figure
    _paint_chart_background(fig)
    fig_rgba = _rgba(fig.get_facecolor())
    assert fig_rgba[0] < 0.15 and fig_rgba[1] < 0.15 and fig_rgba[2] < 0.15
    roots = [ax for ax in fig.axes if ax.get_label() == "root"]
    if roots:
        root_rgba = _rgba(roots[0].get_facecolor())
        assert root_rgba[0] < 0.15 and root_rgba[1] < 0.15 and root_rgba[2] < 0.15
        assert roots[0].patch.get_visible()
    import matplotlib.pyplot as plt

    plt.close(fig)
