"""Discover the plugin-owned Node runtime without installing during a request."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from dataclasses import dataclass


class RenderError(RuntimeError):
    """An unavailable or failed image worker; callers may return their text result."""


@dataclass(frozen=True)
class Runtime:
    node: Path
    modules: Path


def resource_dir() -> Path:
    from gsuid_core.data_store import get_res_path

    return get_res_path("SayuStock")


def resolve_runtime() -> Runtime:
    root = (
        Path(os.environ["SAYUSTOCK_RENDER_RUNTIME"]).expanduser().resolve()
        if "SAYUSTOCK_RENDER_RUNTIME" in os.environ
        else resource_dir() / "render-runtime" / "current"
    )
    modules = root / "node_modules"
    for package in ("core", "helpers"):
        if not (modules / "@takumi-rs" / package / "package.json").is_file():
            raise RenderError("绘图运行时未安装，请按 docs/rendering.md 安装 Node 24 和 Takumi。")
    if "SAYUSTOCK_RENDER_NODE" in os.environ:
        node = Path(os.environ["SAYUSTOCK_RENDER_NODE"]).expanduser().resolve()
    elif (root / "node" / "node.exe").is_file():
        node = root / "node" / "node.exe"
    elif (root / "node" / "bin" / "node").is_file():
        node = root / "node" / "bin" / "node"
    else:
        executable = shutil.which("node")
        if executable is None:
            raise RenderError("未找到 Node 24，请安装后重启 Core，或设置 SAYUSTOCK_RENDER_NODE。")
        node = Path(executable)
    if not node.is_file():
        raise RenderError("绘图 Node 路径不存在，请检查 SAYUSTOCK_RENDER_NODE。")
    return Runtime(node, modules)


def font_paths() -> list[tuple[str, Path]]:
    from gsuid_core.utils.fonts.fonts import FONT_ORIGIN_PATH

    if not FONT_ORIGIN_PATH.is_file():
        raise RenderError("Core 的 MiSans 字体缺失，请恢复 Core 字体资源。")
    fonts = [("MiSans", FONT_ORIGIN_PATH)]
    emoji = FONT_ORIGIN_PATH.with_name("TwemojiMozilla-colr.woff2")
    if emoji.is_file():
        fonts.append(("Twemoji", emoji))
    return fonts
