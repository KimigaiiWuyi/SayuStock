"""Verify old zero/negative cloudmap config values fall back safely."""

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable
from pathlib import Path
from datetime import datetime

import pytest

import SayuStock.stock_cloudmap.render as cloudmap_render
from SayuStock.utils.market.enums import BoardKind
from SayuStock.stock_cloudmap.data import CloudMapDataResult
from SayuStock.utils.market.models import BoardRow, BoardSnapshot
from gsuid_core.utils.plugins_config.models import GsIntConfig


def _sample_snapshot() -> BoardSnapshot:
    rows = (
        BoardRow(
            code="600519",
            name="贵州茅台",
            price=1500.0,
            change_pct=1.2,
            amount=1e9,
            market_cap=2e12,
            industry="食品饮料",
            lead_name=None,
            lead_change_pct=None,
        ),
    )
    return BoardSnapshot(kind=BoardKind.CUSTOM, title="沪深A", rows=rows)


def test_zero_or_negative_cloudmap_config_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_values = {
        "mapcloud_viewport": 0,
        "mapcloud_scale": -1,
        "mapcloud_refresh_minutes": 0,
    }
    captured: list[tuple[int, int]] = []
    out_file = tmp_path / "cloudmap.png"

    monkeypatch.setattr(
        cloudmap_render.STOCK_CONFIG,
        "get_config",
        lambda key: GsIntConfig(key, key, config_values[key]),
    )

    async def fake_fetch(
        market: str,
        sector: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> CloudMapDataResult:
        _ = market, sector, start_time, end_time
        return CloudMapDataResult(
            raw_data=_sample_snapshot(),
            raw_datas=[],
            sector="美股",
            special_cache_key="test",
        )

    async def fake_render_treemap(
        snap: BoardSnapshot,
        market: str,
        sector: str | None = None,
        layer: int = 2,
        *,
        width: int = 2500,
        scale: int = 2,
    ) -> bytes:
        _ = snap, market, sector, layer
        captured.append((width, scale))
        return b"png-bytes"

    async def fake_cached_render(
        file: Path,
        minutes: int,
        draw: Callable[[], Awaitable[str | bytes]],
    ) -> str | Path:
        _ = minutes
        result = await draw()
        assert isinstance(result, bytes)
        file.write_bytes(result)
        return file

    monkeypatch.setattr(cloudmap_render.CLOUDMAP_DATA_SERVICE, "fetch", fake_fetch)
    monkeypatch.setattr(cloudmap_render, "render_treemap", fake_render_treemap)
    monkeypatch.setattr(cloudmap_render, "get_render_file", lambda *args, **kwargs: out_file)
    monkeypatch.setattr(cloudmap_render, "cached_render", fake_cached_render)

    result = asyncio.run(cloudmap_render.render_html("大盘云图", "美股"))
    assert isinstance(result, Path)
    assert "<svg" in result.read_text(encoding="utf-8")

    png_result = asyncio.run(cloudmap_render._artifact("大盘云图", "美股", None, None, "png"))
    assert png_result == out_file
    assert captured == [(2500, 2)]
