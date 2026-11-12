"""Adapt SayuStock's CSS dimensions to the Core HTML renderer."""

from __future__ import annotations

from math import isfinite
from typing import Literal

__all__ = ["RenderError", "render_html"]


class RenderError(RuntimeError):
    """Core could not render a page; callers may return their text result."""


async def render_html(
    html: str,
    *,
    width: int,
    scale: float = 1.5,
    label: str = "",
    image_format: Literal["png", "jpeg"] = "png",
    jpeg_quality: int = 100,
) -> bytes:
    """Render a local Jinja page with the Core's shared renderer and fonts."""
    if width <= 0 or not isfinite(scale) or scale <= 0:
        raise ValueError("width and scale must be positive finite values")

    from gsuid_core.utils.html_render import render_html_to_bytes

    try:
        return await render_html_to_bytes(
            html,
            max_width=width * scale,
            dpi=96 * scale,
            root_max_width=width,
            allow_refit=True,
            default_font_size=0,
            image_format=image_format,
            jpeg_quality=jpeg_quality,
            lang="zh-CN",
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise RenderError(f"{label or '图片'}生成失败：{error}") from error
