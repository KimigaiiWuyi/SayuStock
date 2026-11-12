"""Namespaced image and HTML artifacts; separate from provider JSON caches."""

import json
import asyncio
from hashlib import blake2s
from pathlib import Path
from weakref import WeakValueDictionary
from datetime import datetime, timedelta
from tempfile import NamedTemporaryFile
from collections.abc import Callable, Awaitable

from gsuid_core.logger import logger

from ..resource_path import DATA_PATH

_RENDER_LOCKS: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, Path], asyncio.Lock] = WeakValueDictionary()
_PNG = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"IEND\xaeB\x60\x82"


def get_render_file(
    market: str, suffix: str, sector: str | None = None, sp: str | None = None, *, namespace: str
) -> Path:
    """Separate Takumi artifacts from provider data and legacy drawing caches."""
    key = json.dumps([market, sector, sp], ensure_ascii=False)
    digest = blake2s(key.encode("utf-8"), digest_size=12).hexdigest()
    name = "".join(char for char in market if char.isalnum() or char in "-_")[:30]
    return DATA_PATH / f"{namespace}-{name}-{digest}_data.{suffix}"


def _current_render(path: Path, minutes: int) -> bool:
    try:
        with path.open("rb") as file:
            if datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) >= timedelta(minutes=minutes):
                return False
            if path.suffix == ".png":
                if file.read(8) != _PNG:
                    return False
                file.seek(-8, 2)
                return file.read(8) == _PNG_END
            return file.read(15) == b"<!doctype html>"
    except (OSError, ValueError):
        return False


def _write_render(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=".render-", delete=False) as file:
        staged = Path(file.name)
        try:
            file.write(content)
            file.close()
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


async def cached_render(path: Path, minutes: int, render: Callable[[], Awaitable[bytes | str]]) -> Path | str:
    """Render once per cache key and replace its artifact only after a successful result."""
    key = (asyncio.get_running_loop(), path)
    lock = _RENDER_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        if await asyncio.to_thread(_current_render, path, minutes):
            return path
        content = await render()
        if isinstance(content, str):
            return content
        if path.suffix == ".png" and (not content.startswith(_PNG) or not content.endswith(_PNG_END)):
            return "图片生成失败：未获得有效的 PNG。"
        try:
            await asyncio.to_thread(_write_render, path, content)
        except OSError as error:
            logger.error(f"[SayuStock] 无法写入图表缓存 {path.name}: {error}")
            return "图片缓存写入失败，请稍后重试。"
        return path
