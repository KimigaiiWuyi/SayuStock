"""取数失败提示框：拿不到数据必须显式画出来，不许静默。

三个出图页（我的自选 / 基金持仓 / 模拟盘自选）共用同一套黄色警告样式，
避免每页各写一份、各自跑偏。失败条目按「原因」分组，同原因的合并成一行。

框的高度跟着**持仓条**走（``WARN_MIN_H``）：一行失败时也占满一行的视觉重量，
不会在两行卡片之间夹成一条细缝。
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass
from collections.abc import Sequence

# 与 paper_holdings_html 的金色主题一致
WARN_TEXT = "#ffd278"
WARN_SUB_TEXT = "#ffe6ad"
WARN_BG = "rgba(212,160,23,0.14)"
WARN_BORDER = "rgba(212,160,23,0.55)"

WARN_PAD_X = 14
WARN_PAD_Y = 10
WARN_TITLE_H = 26
WARN_LINE_H = 22
# 与持仓条可见高度一致（素材 110 高、上下各留 7/8 透明边）
WARN_MIN_H = 95

# 一行放几条失败。3 条 × 约 17 字 ≈ 500px，850px 宽的框放得下且不截断。
WARN_PER_LINE = 3

WARN_CSS = f"""
.warn {{
  position: absolute; box-sizing: border-box;
  padding: {WARN_PAD_Y}px {WARN_PAD_X}px;
  display: flex; flex-direction: column; justify-content: center;
  background: {WARN_BG};
  border: 1px solid {WARN_BORDER};
  border-left: 4px solid {WARN_TEXT};
  border-radius: 10px;
}}
.warn-t {{
  font-size: 19px; font-weight: 700; line-height: {WARN_TITLE_H}px;
  color: {WARN_TEXT}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.warn-l {{
  font-size: 14px; line-height: {WARN_LINE_H}px; color: {WARN_SUB_TEXT};
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
"""


@dataclass(frozen=True, slots=True)
class Failure:
    """一条取数失败。

    label 是标的（代码或「名称(代码)」），reason 是给人看的原因短语。
    """

    label: str
    reason: str


def failure_lines(failures: Sequence[Failure], *, per_line: int = WARN_PER_LINE) -> list[str]:
    """失败清单 → 按原因分组的文本行。同原因合并，条目多时按 per_line 折行。

    失败的每一条都要出现在结果里：不允许「只显示前 N 条」这种静默截断。
    """
    grouped: dict[str, list[str]] = {}
    for item in failures:
        grouped.setdefault(item.reason, []).append(item.label)
    lines: list[str] = []
    for reason, labels in grouped.items():
        for i in range(0, len(labels), per_line):
            lines.append(f"{reason}：{'、'.join(labels[i : i + per_line])}")
    return lines


def warn_box_height(n_lines: int) -> int:
    """警告框高度（无失败时为 0，不占位）。至少有一行卡片那么高。"""
    if n_lines <= 0:
        return 0
    content = WARN_PAD_Y * 2 + WARN_TITLE_H + WARN_LINE_H * n_lines
    return max(WARN_MIN_H, content)


def warn_box_html(
    *,
    title: str,
    lines: Sequence[str],
    top: int,
    left: int,
    width: int,
    height: int,
) -> str:
    """警告框 HTML 片段。``lines`` 为空时返回空串（不画空框）。"""
    if not lines:
        return ""
    body = "".join(f'<div class="warn-l">{html_lib.escape(line, quote=True)}</div>' for line in lines)
    return (
        f'<div class="warn" style="left:{left}px;top:{top}px;width:{width}px;height:{height}px">'
        f'<div class="warn-t">{html_lib.escape(title, quote=True)}</div>'
        f"{body}"
        f"</div>"
    )
