"""Worker lifecycle tests and a real native-render smoke used by CI."""

from __future__ import annotations

import os
import sys
import struct
import asyncio
from pathlib import Path

import pytest

from SayuStock import render
from SayuStock.render.runtime import Runtime, RenderError, resolve_runtime
from SayuStock.render.templates import _ENV


def test_missing_runtime_has_install_instructions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAYUSTOCK_RENDER_RUNTIME", str(tmp_path))
    with pytest.raises(RenderError, match="docs/rendering.md"):
        resolve_runtime()


def test_templates_escape_data_and_reject_missing_context() -> None:
    from jinja2 import UndefinedError

    template = _ENV.from_string("<p>{{ title }}</p>")
    assert template.render(title="<script>x</script>") == "<p>&lt;script&gt;x&lt;/script&gt;</p>"
    with pytest.raises(UndefinedError):
        template.render()


@pytest.mark.asyncio
async def test_queue_time_is_part_of_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    semaphore = asyncio.Semaphore(0)
    monkeypatch.setattr(render, "_slot", lambda: semaphore)
    monkeypatch.setattr(render, "_TIMEOUT", 0.03)
    with pytest.raises(RenderError, match="超时"):
        await render.render_html("<div>queued</div>", width=100)


def _python_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> Path:
    worker = tmp_path / "worker.py"
    worker.write_text(script, encoding="utf-8")
    monkeypatch.setattr(render, "_WORKER", worker)
    executable = Path(sys.base_prefix) / "python.exe" if os.name == "nt" else Path(sys.executable)
    monkeypatch.setattr(render, "resolve_runtime", lambda: Runtime(executable, tmp_path))
    monkeypatch.setattr(render, "resource_dir", lambda: tmp_path)
    monkeypatch.setattr(render, "font_paths", lambda: [])
    return worker


@pytest.mark.asyncio
async def test_worker_failure_and_invalid_output_are_not_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = _python_worker(monkeypatch, tmp_path, "import sys\nsys.stderr.write('native failed')\nsys.exit(2)\n")
    with pytest.raises(RenderError, match="native failed"):
        await render.render_html("<div>test</div>", width=100)
    worker.write_text("import sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_bytes(b'not png')\n")
    with pytest.raises(RenderError, match="PNG"):
        await render.render_html("<div>test</div>", width=100)
    assert not list((tmp_path / "render-tmp").iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_reap_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cancel: bool
) -> None:
    import psutil

    marker = tmp_path / "pid.txt"
    _python_worker(
        monkeypatch,
        tmp_path,
        "import os,time\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n",
    )
    monkeypatch.setattr(render, "_TIMEOUT", 3 if cancel else 0.6)
    task = asyncio.create_task(render.render_html("<div>test</div>", width=100))
    async with asyncio.timeout(2):
        while not marker.exists() and not task.done():
            await asyncio.sleep(0.01)
    assert marker.exists(), "Test worker did not start"
    pid = int(marker.read_text())
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RenderError, match="超时"):
            await task
    assert not psutil.pid_exists(pid)
    assert not list((tmp_path / "render-tmp").iterdir())


@pytest.mark.asyncio
async def test_native_takumi_renders_cjk_and_svg() -> None:
    if "SAYUSTOCK_RENDER_RUNTIME" not in os.environ and os.environ.get("SAYUSTOCK_REQUIRE_RENDER_RUNTIME") != "1":
        pytest.skip("Install Node/Takumi and set SAYUSTOCK_RENDER_RUNTIME to run native rendering")
    html = """<div style="display:flex;flex-direction:column;width:320px;background:#10151d;
    color:#e6edf3;font-family:MiSans;font-size:24px;padding:20px">
    <div>早柚股票 +0.002</div><svg width="280" height="60"><path d="M0,50 L100,30 L280,10"
    fill="none" stroke="#f6465d" stroke-width="3"/></svg></div>"""
    png = await render.render_html(html, width=320, scale=1.5, label="native smoke")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert width == 480 and height >= 150
