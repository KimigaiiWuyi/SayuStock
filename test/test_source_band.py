"""来源行不压刻度，也不压技术分析摘要。"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt

from SayuStock.stock_analysis.render import render_technical_image
from SayuStock.stock_analysis.technical import TechnicalReport
from SayuStock.stock_stockinfo.chart_base import _setup_mpl, _fig_to_image, _reserve_source_band


def test_rotated_ticks_stay_above_source_line() -> None:
    _setup_mpl()
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.set_layout_engine("tight")
    ax.plot([0, 1, 2], [1, 2, 1.4])
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["2024-09", "2025-06", "2026-10"], rotation=20)
    for label in ax.get_xticklabels():
        label.set_color("#ff3030")
    fig.text(0.016, 0.012, "数据来源：腾讯财经 | SayuStock", color="#39ff14", fontsize=9)
    _reserve_source_band(fig)
    image = _fig_to_image(fig, dpi=100)
    arr = np.asarray(image.convert("RGB"))
    red = np.where(((arr[:, :, 0] > 180) & (arr[:, :, 1] < 90)).any(axis=1))[0]
    green = np.where(((arr[:, :, 1] > 180) & (arr[:, :, 0] < 90)).any(axis=1))[0]
    assert len(red) > 0 and len(green) > 0
    assert int(red.max()) < int(green.min())


def _yellow(arr: np.ndarray) -> np.ndarray:
    return (arr[:, :, 0] > 200) & (arr[:, :, 1] > 150) & (arr[:, :, 1] < 230) & (arr[:, :, 2] < 80)


def test_technical_summary_stays_above_source() -> None:
    report = TechnicalReport(
        name="贵州茅台",
        code="600519",
        period_code="101",
        period_label="日K",
        last_close=1263.0,
        score=42,
        trend="空头",
        momentum="弱",
        volume="缩量",
        position="低位",
        signals=[f"信号{i}" for i in range(6)],
        risk_flags=[f"风险{i}" for i in range(5)],
        levels={
            "support": 1200.0,
            "resistance": 1400.0,
            "ma20": 1300.0,
            "ma60": 1280.0,
            "stop_ref": 1180.0,
            "target_ref": 1450.0,
        },
        summary="摘要正文应该完整留在来源行上方",
        source_ids=("tencent",),
    )
    image = render_technical_image(report)
    arr = np.asarray(image.convert("RGB"))
    cut = int(arr.shape[0] * 0.045)
    assert int(_yellow(arr[-cut:]).sum()) < 20
    assert int(_yellow(arr[:-cut]).sum()) > 20
