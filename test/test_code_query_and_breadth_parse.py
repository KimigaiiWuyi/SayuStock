"""search_stock 查询规范化 + 大盘 breadth 解析烟测（无网络）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parent.parent
_REPO = _PLUGIN.parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PLUGIN))

from SayuStock.utils.stock.request_utils import (  # noqa: E402
    _code_query_candidates,
)


def test_candidates_code_first() -> None:
    c = _code_query_candidates("600519 贵州茅台")
    assert c[0] == "600519"
    assert any("茅台" in x for x in c)


def test_candidates_index() -> None:
    c = _code_query_candidates("399997 中证白酒")
    assert "399997" in c
    assert c[0] == "399997"


def test_candidates_secid() -> None:
    c = _code_query_candidates("1.600519")
    assert any(x.startswith("1.") for x in c) or "600519" in c


def test_trade_detail_has_up_down_fields() -> None:
    from SayuStock.utils.constant import trade_detail_dict

    assert "f104" in trade_detail_dict
    assert "f105" in trade_detail_dict
    assert "f140" in trade_detail_dict


def test_get_code_id_compound_uses_digit(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from SayuStock.utils.stock import request_utils as ru

    calls: list[str] = []

    async def fake_one(code: str, priority: str | None = None) -> tuple[str, str, str] | None:
        calls.append(code)
        if code == "600519":
            return ("1.600519", "贵州茅台", "沪A")
        return None

    monkeypatch.setattr(ru, "_get_code_id_one", fake_one)

    async def _run() -> None:
        hit = await ru.get_code_id("600519 贵州茅台")
        assert hit is not None
        assert hit[0] == "1.600519"
        assert calls[0] == "600519"

    asyncio.run(_run())
