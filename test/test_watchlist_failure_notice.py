"""取数失败不许静默：我的自选 / 基金持仓都要出图 + 黄色警告。

这两条命令以前一个「悄悄少画几张卡」、一个「回一句获取失败」，
这里钉死：失败 → 警告框 + 警告文字，AI 也要知道哪只没取到。
"""

from __future__ import annotations

import asyncio
import importlib
from types import ModuleType, SimpleNamespace
from datetime import datetime

import pytest

from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.errors import not_found
from SayuStock.utils.market.models import Quote, SymbolRef, IntradayPoint, IntradaySeries


def _module(name: str) -> ModuleType:
    """``stock_info/__init__`` 把 ``draw_fund_info`` 名字让给了函数，这里按模块名取。"""
    return importlib.import_module(name)


def _quote(code: str, name: str) -> Quote:
    return Quote(
        symbol=SymbolRef(
            code=code,
            name=name,
            asset_class=AssetClass.EQUITY,
            exchange="SSE",
            provider_symbol=f"1.{code}",
            sec_type="沪A",
        ),
        price=1680.0,
        open=1670.0,
        high=1690.0,
        low=1660.0,
        prev_close=1660.0,
        change_pct=1.2,
        change_amount=20.0,
        volume=1.0,
        amount=3.4e9,
        turnover_rate=0.56,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=datetime(2026, 10, 20, 15, 0),
    )


class _FakeMarket:
    """只对 ok 里的代码有数据，其余一律 not_found。"""

    def __init__(self, ok: set[str]) -> None:
        self._ok = ok

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | object:
        if query not in self._ok:
            return not_found("标的不存在", provider="eastmoney")
        q = _quote(query, f"名称{query}")
        return IntradaySeries(
            symbol=q.symbol,
            points=(
                IntradayPoint(
                    ts=datetime(2026, 10, 20, 9, 30),
                    price=q.price,
                    open=q.price,
                    high=q.price,
                    low=q.price,
                    volume=1.0,
                    amount=1.0,
                    avg_price=q.price,
                ),
            ),
            quote=q,
            provider="eastmoney",
        )

    async def quote(self, query: str) -> Quote | object:
        if query not in self._ok:
            return not_found("标的不存在", provider="eastmoney")
        return _quote(query, f"名称{query}")


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    """返回 (AI 文字, 渲染出的 HTML)。渲染被替换成假实现，不依赖 pytakumi。"""
    ai_texts: list[str] = []
    htmls: list[str] = []

    async def fake_render(html: str, **kwargs: object) -> bytes:
        htmls.append(html)
        return b"\x89PNG"

    async def no_index(names: list[str]) -> list[object]:
        return []

    df = _module("SayuStock.stock_info.draw_fund_info")
    dmi = _module("SayuStock.stock_info.draw_my_info")

    for mod in (df, dmi):
        monkeypatch.setattr(mod, "ai_return", ai_texts.append)
        monkeypatch.setattr(mod, "render_html_to_bytes", fake_render)
        monkeypatch.setattr(mod, "quote_index_items", no_index)
    return ai_texts, htmls


def test_fund_partial_failure_renders_image_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    probe: tuple[list[str], list[str]],
) -> None:
    ai_texts, htmls = probe
    df = _module("SayuStock.stock_info.draw_fund_info")

    async def fake_code_id(code: str) -> tuple[str, str, str]:
        return ("1.000005", "易方达蓝筹精选", "基金")

    async def fake_pos(fcode: str) -> dict[str, object]:
        return {
            "Datas": [
                {"ShareCode": "600519", "ShareName": "贵州茅台", "ShareProportion": "8.50"},
                {"ShareCode": "300750", "ShareName": "宁德时代", "ShareProportion": "6.20"},
            ]
        }

    monkeypatch.setattr(df, "get_code_id", fake_code_id)
    monkeypatch.setattr(df, "get_fund_pos_list", fake_pos)
    monkeypatch.setattr(df, "get_market", lambda: _FakeMarket({"600519"}))

    out = asyncio.run(df.draw_fund_info("000005"))
    assert out == b"\x89PNG", "部分失败也必须出图，不许回一句文字"
    html = htmls[-1]
    # 失败写在自己那一行：涨跌幅格写「失败」，原因写在那行折线位置，不另挂横幅
    assert 'class="warn"' not in html
    assert "失败" in html
    assert "⚠ 东方财富未找到标的" in html
    # 成功的行照常画
    assert "名称600519" in html
    assert ai_texts, "失败必须写进 AI 文字"
    assert "宁德时代" in ai_texts[-1]


def test_fund_api_failure_returns_explicit_message(
    monkeypatch: pytest.MonkeyPatch,
    probe: tuple[list[str], list[str]],
) -> None:
    ai_texts, htmls = probe
    df = _module("SayuStock.stock_info.draw_fund_info")

    async def fake_code_id(code: str) -> tuple[str, str, str]:
        return ("1.000005", "易方达蓝筹精选", "基金")

    async def fake_pos(fcode: str) -> None:
        return None

    monkeypatch.setattr(df, "get_code_id", fake_code_id)
    monkeypatch.setattr(df, "get_fund_pos_list", fake_pos)

    out = asyncio.run(df.draw_fund_info("000005"))
    # 没有持仓行可标「失败」，就回一句明确的话，不静默
    assert isinstance(out, str), out
    assert "获取基金持仓数据失败" in out
    assert ai_texts and "获取基金持仓数据失败" in ai_texts[-1]


def test_my_stock_partial_failure_renders_image_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    probe: tuple[list[str], list[str]],
) -> None:
    ai_texts, htmls = probe
    dmi = _module("SayuStock.stock_info.draw_my_info")

    async def fake_uid_list(user_id: str, bot_id: str, game_name: str | None = None) -> list[str]:
        return ["600519", "300750", "000858"]

    monkeypatch.setattr(dmi.SsBind, "get_uid_list_by_game", staticmethod(fake_uid_list))
    monkeypatch.setattr(dmi, "get_market", lambda: _FakeMarket({"600519"}))

    ev = SimpleNamespace(at=None, user_id="u1", bot_id="b1")
    out = asyncio.run(dmi.draw_my_stock_img(ev))
    assert out == b"\x89PNG", "部分失败也必须出图，不许悄悄少画一张卡"
    html = htmls[-1]
    assert 'class="warn"' in html
    assert "300750" in html
    assert "000858" in html
    assert "600519" in html
    assert ai_texts and "行情获取失败" in ai_texts[-1]


def test_my_stock_all_failed_still_renders_warning(
    monkeypatch: pytest.MonkeyPatch,
    probe: tuple[list[str], list[str]],
) -> None:
    _ai_texts, htmls = probe
    dmi = _module("SayuStock.stock_info.draw_my_info")

    async def fake_uid_list(user_id: str, bot_id: str, game_name: str | None = None) -> list[str]:
        return ["600519", "300750"]

    monkeypatch.setattr(dmi.SsBind, "get_uid_list_by_game", staticmethod(fake_uid_list))
    monkeypatch.setattr(dmi, "get_market", lambda: _FakeMarket(set()))

    ev = SimpleNamespace(at=None, user_id="u1", bot_id="b1")
    out = asyncio.run(dmi.draw_my_stock_img(ev))
    assert out == b"\x89PNG", "全失败也要出图（警告页），不许回「暂无自选行情」"
    html = htmls[-1]
    assert 'class="warn"' in html
    assert "600519" in html
    assert "300750" in html
