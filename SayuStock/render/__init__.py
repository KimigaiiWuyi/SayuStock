"""Bounded, cancellable Takumi rendering shared by all SayuStock pages."""

from __future__ import annotations

import os
import json
import math
import signal
import asyncio
import tempfile
from pathlib import Path
from weakref import WeakKeyDictionary

from .runtime import Runtime, RenderError, font_paths, resource_dir, resolve_runtime

__all__ = ["RenderError", "render_html"]

_WORKER = Path(__file__).with_name("node") / "render.mjs"
_TIMEOUT = 45.0
_CONCURRENCY = 2
_SLOTS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _SLOTS:
        _SLOTS[loop] = asyncio.Semaphore(_CONCURRENCY)
    return _SLOTS[loop]


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
    await process.wait()


async def _worker(runtime: Runtime, request: Path, output: Path) -> None:
    env = dict(os.environ, SAYUSTOCK_RENDER_MODULES=str(runtime.modules))
    args = (str(runtime.node), str(_WORKER), str(request), str(output))
    if os.name == "nt":
        process = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    try:
        _, error = await process.communicate()
    except BaseException:
        await _stop(process)
        raise
    if process.returncode:
        detail = error.decode("utf-8", errors="replace")[-1200:]
        raise RenderError(f"Takumi 绘图失败（退出码 {process.returncode}）：{detail}")


async def render_html(html: str, *, width: int, scale: float = 1.5, label: str = "") -> bytes:
    """Render a trusted local page. The deadline includes queue time and worker cleanup."""
    if width <= 0 or not math.isfinite(scale) or scale <= 0:
        raise ValueError("width and scale must be positive finite values")
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with _slot():
                runtime = resolve_runtime()
                fonts = font_paths()
                temp_root = resource_dir() / "render-tmp"
                temp_root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="image-", dir=temp_root) as temporary:
                    directory = Path(temporary)
                    request, output = directory / "request.json", directory / "output.png"
                    payload = {
                        "html": html,
                        "width": width,
                        "scale": scale,
                        "format": "png",
                        "lang": "zh-CN",
                        "fonts": [{"name": name, "path": str(path)} for name, path in fonts],
                    }
                    request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    await _worker(runtime, request, output)
                    if not output.is_file():
                        raise RenderError("绘图进程未生成图片。")
                    result = output.read_bytes()
                    if len(result) < 24 or result[:8] != b"\x89PNG\r\n\x1a\n":
                        raise RenderError("绘图进程未生成有效的 PNG 图片。")
                    return result
    except TimeoutError as error:
        raise RenderError(f"{label or '图片'}生成超时，请稍后重试。") from error
    except OSError as error:
        raise RenderError(f"绘图运行时无法启动或读写图片：{error}") from error
