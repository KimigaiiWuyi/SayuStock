"""Technical evidence stays ahead of attribution in the natural-height page."""

from __future__ import annotations

from extras_samples import technical_report

from SayuStock.stock_analysis.render import build_technical_html


def test_technical_summary_stays_above_source() -> None:
    report = technical_report()
    html = build_technical_html(report)
    for line in (*report.signals, *report.risk_flags, report.summary):
        assert line in html
    assert "参考止损" in html and "75.20" in html
    assert "MA60" in html and "—" in html
    assert "数据来源：腾讯财经 | SayuStock 技术分析" in html
    assert html.index(report.summary) < html.index('<footer class="foot extra-foot">')
