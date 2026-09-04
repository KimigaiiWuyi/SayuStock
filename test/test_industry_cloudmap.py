"""行业云图：申万一/二/三级名称命中后，用缓存成分名单过滤大盘 hotmap。"""

from __future__ import annotations

import asyncio
from typing import Literal

import pytest

from SayuStock.utils import sector_resolve as sector_resolve_mod
from SayuStock.stock_cloudmap import data as cloudmap_data
from SayuStock.utils.constant import ErroText
from SayuStock.utils.render_data import CloudmapRenderData, build_cloudmap_render_data
from SayuStock.utils.market.enums import BoardKind
from SayuStock.utils.market.models import BoardRow, BoardSnapshot
from SayuStock.utils.sector_resolve import (
    INDUSTRY_MEMBER_CACHE_MINUTES,
    match_sector_menu,
    normalize_sector_query,
    filter_snapshot_by_codes,
    local_industry_member_codes,
)
from SayuStock.utils.market.adapters.eastmoney.provider import _is_bk_market

_INDUSTRY_MENU = {
    "建筑材料": "BK1208",
    "水泥": "BK0424",
    "水泥制品": "BK1463",
    "水泥制造": "BK1464",
    "白酒Ⅱ": "BK0888",
    "半导体": "BK1036",
    "医药生物": "BK1216",
    "医药商业": "BK1042",
}


def test_member_cache_ttl_is_at_least_one_month() -> None:
    assert INDUSTRY_MEMBER_CACHE_MINUTES >= 30 * 24 * 60


def test_normalize_strips_user_tails() -> None:
    assert normalize_sector_query("水泥板块") == "水泥"
    assert normalize_sector_query("白酒Ⅱ") == "白酒"
    assert normalize_sector_query("水泥制造") == "水泥制造"


def test_match_prefers_exact_over_prefix() -> None:
    hit = match_sector_menu("水泥", _INDUSTRY_MENU)
    assert hit == ("水泥", "BK0424")
    hit_l3 = match_sector_menu("水泥制造", _INDUSTRY_MENU)
    assert hit_l3 == ("水泥制造", "BK1464")
    hit_l1 = match_sector_menu("建筑材料", _INDUSTRY_MENU)
    assert hit_l1 == ("建筑材料", "BK1208")


def test_match_strips_suffix_and_sw_level_mark() -> None:
    assert match_sector_menu("水泥板块", _INDUSTRY_MENU) == ("水泥", "BK0424")
    assert match_sector_menu("白酒", _INDUSTRY_MENU) == ("白酒Ⅱ", "BK0888")


def test_match_prefix_prefers_level1() -> None:
    assert match_sector_menu("医药", _INDUSTRY_MENU) == ("医药生物", "BK1216")


def test_match_unknown_returns_none() -> None:
    assert match_sector_menu("不存在的行业xyz", _INDUSTRY_MENU) is None
    assert match_sector_menu("  ", _INDUSTRY_MENU) is None


def test_is_bk_market() -> None:
    assert _is_bk_market("BK0424")
    assert _is_bk_market("b:BK1208")
    assert _is_bk_market("BK1208+f:!50")
    assert not _is_bk_market("沪深A")
    assert not _is_bk_market("水泥")


def _row(code: str, name: str, industry: str, change_pct: float) -> BoardRow:
    return BoardRow(
        code=code,
        name=name,
        price=20.0,
        change_pct=change_pct,
        amount=1e8,
        market_cap=2e11,
        industry=industry,
        lead_name=None,
        lead_change_pct=None,
    )


def _cement_snap() -> BoardSnapshot:
    return BoardSnapshot(
        kind=BoardKind.INDUSTRY,
        title="水泥",
        rows=(_row("600585", "海螺水泥", "建筑材料", 1.2),),
    )


def _hotmap_snap() -> BoardSnapshot:
    return BoardSnapshot(
        kind=BoardKind.HOTMAP,
        title="大盘云图",
        rows=(
            _row("600585", "海螺水泥", "建筑材料", 1.2),
            _row("600519", "贵州茅台", "食品饮料", 2.0),
        ),
    )


def test_local_l2_members_include_conch() -> None:
    codes = local_industry_member_codes("水泥")
    assert codes is not None
    assert "600585" in codes


def test_filter_snapshot_keeps_listed_codes_only() -> None:
    filtered = filter_snapshot_by_codes(_hotmap_snap(), ["600585"], title="水泥")
    assert isinstance(filtered, BoardSnapshot)
    assert [row.code for row in filtered.rows] == ["600585"]


def test_industry_cloudmap_keeps_l2_constituents() -> None:
    """成分股所属行业仍是一级名时，行业云图也不能再按 sector 过滤掉。"""
    data = build_cloudmap_render_data(_cement_snap(), "行业云图", "水泥", layer=1)
    assert isinstance(data, CloudmapRenderData)
    assert list(data.df["name"]) == ["海螺水泥"]


class _MenuPort:
    def __init__(self) -> None:
        self.board_codes: list[str] = []
        self.hotmap_calls = 0

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str]:
        if kind == "industry":
            return dict(_INDUSTRY_MENU)
        return {"华为欧拉": "BK0999"}

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot:
        _ = sector, limit, sort_asc
        self.board_codes.append(str(kind))
        return _cement_snap()

    async def hotmap(self) -> BoardSnapshot:
        self.hotmap_calls += 1
        return _hotmap_snap()


def _patch_menu_port(monkeypatch: pytest.MonkeyPatch, fake: _MenuPort) -> None:
    monkeypatch.setattr(sector_resolve_mod, "get_market", lambda: fake)
    monkeypatch.setattr(cloudmap_data, "get_market", lambda: fake)


def test_industry_cloudmap_l2_uses_hotmap_not_board(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MenuPort()
    _patch_menu_port(monkeypatch, fake)

    async def _run() -> None:
        result = await cloudmap_data.CLOUDMAP_DATA_SERVICE.fetch("行业云图", "水泥", None, None)
        assert result.sector == "水泥"
        assert isinstance(result.raw_data, BoardSnapshot)
        assert [row.name for row in result.raw_data.rows] == ["海螺水泥"]
        assert fake.board_codes == []
        assert fake.hotmap_calls == 1

    asyncio.run(_run())


def test_industry_cloudmap_l3_uses_cached_members_then_hotmap(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MenuPort()
    _patch_menu_port(monkeypatch, fake)

    async def _members(bk_code: str) -> list[str]:
        assert bk_code == "BK1464"
        return ["600585"]

    monkeypatch.setattr(sector_resolve_mod, "local_industry_member_codes", lambda _name: None)
    monkeypatch.setattr(sector_resolve_mod, "load_board_member_codes", _members)

    async def _run() -> None:
        result = await cloudmap_data.CLOUDMAP_DATA_SERVICE.fetch("行业云图", "水泥制造", None, None)
        assert result.sector == "水泥制造"
        assert isinstance(result.raw_data, BoardSnapshot)
        assert [row.code for row in result.raw_data.rows] == ["600585"]
        assert fake.board_codes == []
        assert fake.hotmap_calls == 1

    asyncio.run(_run())


def test_industry_cloudmap_fetch_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _MenuPort()
    _patch_menu_port(monkeypatch, fake)

    async def _run() -> None:
        result = await cloudmap_data.CLOUDMAP_DATA_SERVICE.fetch("行业云图", "火星矿业", None, None)
        assert result.raw_data == ErroText["typemap"]
        assert fake.board_codes == []
        assert fake.hotmap_calls == 0

    asyncio.run(_run())


def test_industry_cloudmap_fetch_requires_name() -> None:
    async def _run() -> None:
        result = await cloudmap_data.CLOUDMAP_DATA_SERVICE.fetch("行业云图", "", None, None)
        assert isinstance(result.raw_data, str)
        assert "水泥" in result.raw_data

    asyncio.run(_run())
