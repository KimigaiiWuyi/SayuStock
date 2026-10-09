"""底栏几何：来源行在 footer 图上方，两段不共用像素。"""

from __future__ import annotations

from dataclasses import dataclass

FOOTER_W = 850
FOOTER_H = 40

# 来源字号之外，带内上下各留的空隙。
SOURCE_PAD_Y = 8
SOURCE_GAP_TOP = 10
SOURCE_GAP_BOTTOM = 8
FOOTER_GAP_BOTTOM = 10


def source_band_h(font_size: int) -> int:
    return font_size + SOURCE_PAD_Y * 2


def chrome_height(*, font_size: int, footer_h: int = FOOTER_H) -> int:
    return SOURCE_GAP_TOP + source_band_h(font_size) + SOURCE_GAP_BOTTOM + footer_h + FOOTER_GAP_BOTTOM


def source_top(canvas_h: int, *, font_size: int, footer_h: int = FOOTER_H) -> int:
    return canvas_h - chrome_height(font_size=font_size, footer_h=footer_h) + SOURCE_GAP_TOP


def footer_top(canvas_h: int, *, footer_h: int = FOOTER_H) -> int:
    return canvas_h - FOOTER_GAP_BOTTOM - footer_h


def footer_left(canvas_w: int, *, footer_w: int = FOOTER_W) -> int:
    return max((canvas_w - footer_w) // 2, 0)


@dataclass(frozen=True, slots=True)
class BottomChrome:
    canvas_h: int
    source_y: int
    footer_x: int
    footer_y: int


def layout_bottom_chrome(
    width: int,
    content_bottom: int,
    *,
    font_size: int,
    footer_w: int = FOOTER_W,
    footer_h: int = FOOTER_H,
) -> BottomChrome:
    """content_bottom 是正文下沿。source_y 给锚点 ``lm`` 用。"""
    band = source_band_h(font_size)
    foot_y = content_bottom + SOURCE_GAP_TOP + band + SOURCE_GAP_BOTTOM
    return BottomChrome(
        canvas_h=foot_y + footer_h + FOOTER_GAP_BOTTOM,
        source_y=content_bottom + SOURCE_GAP_TOP + band // 2,
        footer_x=footer_left(width, footer_w=footer_w),
        footer_y=foot_y,
    )
