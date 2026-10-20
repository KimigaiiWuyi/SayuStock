"""失败提示框：分组、折行、高度。取数失败必须显式画出来。"""

from __future__ import annotations

from SayuStock.utils.warn_box import (
    WARN_MIN_H,
    WARN_PAD_Y,
    WARN_LINE_H,
    WARN_TITLE_H,
    Failure,
    failure_lines,
    warn_box_html,
    warn_box_height,
)


def test_failure_lines_group_by_reason() -> None:
    failures = [
        Failure(label="600519", reason="东方财富网络异常"),
        Failure(label="000858", reason="东方财富网络异常"),
        Failure(label="300750", reason="未找到标的"),
    ]
    assert failure_lines(failures) == [
        "东方财富网络异常：600519、000858",
        "未找到标的：300750",
    ]


def test_failure_lines_keep_every_item() -> None:
    """失败一条都不能少：只显示前 N 条等于静默。"""
    failures = [Failure(label=f"60051{i}", reason="数据源返回为空") for i in range(7)]
    lines = failure_lines(failures)
    assert len(lines) == 3
    joined = "".join(lines)
    for i in range(7):
        assert f"60051{i}" in joined


def test_warn_box_height_zero_without_failures() -> None:
    assert warn_box_height(0) == 0
    # 一行失败时也占满一行卡片的视觉重量，不在卡片之间夹成细缝
    assert warn_box_height(1) == WARN_MIN_H
    assert warn_box_height(2) == WARN_MIN_H
    # 行数多了按内容长
    assert warn_box_height(4) == WARN_PAD_Y * 2 + WARN_TITLE_H + WARN_LINE_H * 4


def test_warn_box_html_escapes_text() -> None:
    html = warn_box_html(
        title="⚠ 行情获取失败",
        lines=["<b>600519</b>"],
        top=10,
        left=24,
        width=852,
        height=48,
    )
    assert 'class="warn"' in html
    assert 'class="warn-t"' in html
    assert "&lt;b&gt;600519&lt;/b&gt;" in html
    assert "top:10px" in html
    assert "width:852px" in html
    assert "行情获取失败" in html


def test_warn_box_html_empty_when_no_lines() -> None:
    assert warn_box_html(title="x", lines=[], top=0, left=0, width=10, height=0) == ""
