"""Core delegation and real image output of the SayuStock rendering adapter."""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Literal

import pytest
from PIL import Image

from SayuStock import render
from gsuid_core.utils import html_render as core_render
from SayuStock.render.templates import _ENV


def test_templates_escape_data_and_reject_missing_context() -> None:
    from jinja2 import UndefinedError

    template = _ENV.from_string("<p>{{ title }}</p>")
    assert template.render(title="<script>x</script>") == "<p>&lt;script&gt;x&lt;/script&gt;</p>"
    with pytest.raises(UndefinedError):
        template.render()


@pytest.mark.asyncio
async def test_render_html_delegates_css_dimensions_and_output_options(monkeypatch: pytest.MonkeyPatch) -> None:
    async def core(
        html: str,
        *,
        max_width: float,
        dpi: float,
        root_max_width: float,
        allow_refit: bool,
        default_font_size: float,
        image_format: str,
        jpeg_quality: int,
        lang: str,
    ) -> bytes:
        assert html == "<div>早柚</div>"
        assert (max_width, dpi, root_max_width) == (478.5, 144, 319)
        assert allow_refit and default_font_size == 0
        assert (image_format, jpeg_quality, lang) == ("jpeg", 82, "zh-CN")
        return b"core-rendered-bytes"

    monkeypatch.setattr(core_render, "render_html_to_bytes", core)
    assert (
        await render.render_html("<div>早柚</div>", width=319, image_format="jpeg", jpeg_quality=82)
        == b"core-rendered-bytes"
    )


@pytest.mark.asyncio
async def test_core_failure_is_reported_with_page_label(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("Core renderer unavailable")

    async def core(html: str, **options: object) -> bytes:
        raise failure

    monkeypatch.setattr(core_render, "render_html_to_bytes", core)
    with pytest.raises(render.RenderError, match="自选生成失败：Core renderer unavailable") as caught:
        await render.render_html("<div>test</div>", width=320, label="自选")
    assert caught.value.__cause__ is failure


@pytest.mark.asyncio
async def test_render_html_propagates_cancellation_to_core_await(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def core(html: str, **options: object) -> bytes:
        entered.set()
        try:
            await asyncio.Future[None]()
        finally:
            exited.set()
        return b""

    monkeypatch.setattr(core_render, "render_html_to_bytes", core)
    task = asyncio.create_task(render.render_html("<div>test</div>", width=320))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


@pytest.mark.asyncio
@pytest.mark.usefixtures("require_render_runtime")
async def test_render_html_reuses_cores_shared_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def encode(html: str, *, renderer: object, **options: object) -> bytes:
        seen.append(renderer)
        return b"shared-renderer"

    monkeypatch.setattr(core_render, "html_to_pic", encode)
    assert await core_render.render_html_to_bytes("<p>Core</p>", max_width=320) == b"shared-renderer"
    assert await render.render_html("<p>SayuStock</p>", width=320) == b"shared-renderer"
    assert await render.render_html("<p>SayuStock again</p>", width=320) == b"shared-renderer"
    first, second, third = seen
    assert first is not None and first is second is third


@pytest.mark.asyncio
@pytest.mark.usefixtures("require_render_runtime")
@pytest.mark.parametrize(("image_format", "pillow_format"), [("png", "PNG"), ("jpeg", "JPEG")])
async def test_core_renders_css_cjk_and_svg(image_format: Literal["png", "jpeg"], pillow_format: str) -> None:
    html = """<!doctype html><html><head><style>
    html,body{margin:0}.card{display:flex;flex-direction:column;width:320px;background:#10151d;
    color:#e6edf3;font-family:MiSans;font-size:24px;padding:20px;box-sizing:border-box}
    </style></head><body><div class="card">
    <div>早柚股票 +0.002</div><svg width="280" height="60"><path d="M0,50 L100,30 L280,10"
    fill="none" stroke="#f6465d" stroke-width="3"/></svg></div></body></html>"""
    payload = await render.render_html(html, width=320, image_format=image_format, jpeg_quality=88)
    with Image.open(BytesIO(payload)) as image:
        assert image.format == pillow_format
        assert image.width == 480 and image.height >= 150
        rgb = image.convert("RGB")
    assert all(channel < 40 for channel in rgb.getpixel((5, 5)))
    if image_format == "png":
        colors = rgb.getcolors(rgb.width * rgb.height)
        assert colors is not None and any(color == (246, 70, 93) for _, color in colors)
