"""行情API 逐接口路由与回落（不打真网，注入假供应商）。"""

from __future__ import annotations

import asyncio
from typing import Literal
from datetime import date
from collections.abc import Sequence

import pytest

from SayuStock.utils.market import provider_registry as pr
from SayuStock.utils.market.enums import RankBy, BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.errors import MarketError, unsupported, is_market_error
from SayuStock.utils.market.models import (
    Quote,
    SymbolRef,
    KlineSeries,
    BoardSnapshot,
    IntradaySeries,
)
from SayuStock.utils.market.provider_registry import (
    PROVIDER_LABELS,
    ConfigurableEquityMarket,
    normalize_provider_id,
)


class _StubPort:
    """记录调用并在返回值上打供应商标记；可声明 unsupported 接口。"""

    def __init__(self, tag: str, unsupported_ifaces: frozenset[str] = frozenset()) -> None:
        self.tag = tag
        self.unsupported_ifaces = unsupported_ifaces
        self.calls: list[str] = []

    def _ret(self, iface: str) -> object:
        self.calls.append(iface)
        if iface in self.unsupported_ifaces:
            return unsupported(f"{iface} 未实现", provider=self.tag)
        return f"{self.tag}:{iface}"

    async def resolve(self, query: str) -> SymbolRef | None:
        self.calls.append("resolve")
        return SymbolRef(
            code=query,
            name=self.tag,
            asset_class=AssetClass.OTHER,
            exchange=self.tag,
            provider_symbol=query,
        )

    async def quote(self, query: str) -> Quote | MarketError:
        result = self._ret("quote")
        return result if isinstance(result, MarketError) else Quote(
            symbol=SymbolRef(
                code=query,
                name=str(result),
                asset_class=AssetClass.OTHER,
                exchange=self.tag,
                provider_symbol=query,
            ),
            price=1.0,
            open=None,
            high=None,
            low=None,
            prev_close=None,
            change_pct=None,
            change_amount=None,
            volume=None,
            amount=None,
            turnover_rate=None,
            pe=None,
            pb=None,
            market_cap=None,
            float_market_cap=None,
            industry=None,
            limit_up=None,
            limit_down=None,
            as_of=None,
        )

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return [await self.quote(q) for q in queries]

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        return self._ret("intraday")  # type: ignore[return-value]

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        return self._ret("kline")  # type: ignore[return-value]

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        return self._ret("board")  # type: ignore[return-value]

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> MarketError:
        return self._ret("rank_list")  # type: ignore[return-value]

    async def hotmap(self) -> BoardSnapshot | MarketError:
        return self._ret("hotmap")  # type: ignore[return-value]

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        return self._ret("sector_menu")  # type: ignore[return-value]

    async def breadth(self) -> MarketError:
        return self._ret("breadth")  # type: ignore[return-value]

    async def market_turnover(self) -> MarketError:
        return self._ret("market_turnover")  # type: ignore[return-value]

    async def northbound(self) -> MarketError:
        return self._ret("northbound")  # type: ignore[return-value]

    async def valuation_series(self, query: str, kind: object) -> MarketError:
        return self._ret("valuation_series")  # type: ignore[return-value]

    async def financial_snapshot(self, code: str) -> MarketError:
        return self._ret("financial_snapshot")  # type: ignore[return-value]


@pytest.fixture()
def stub_registry(monkeypatch: pytest.MonkeyPatch):
    em = _StubPort("eastmoney")
    sina = _StubPort("sina", unsupported_ifaces=frozenset({"hotmap", "northbound"}))
    tencent = _StubPort("tencent", unsupported_ifaces=frozenset({"board", "rank_list", "hotmap"}))
    monkeypatch.setattr(
        pr,
        "_PROVIDER_FACTORIES",
        {"eastmoney": lambda: em, "sina": lambda: sina, "tencent": lambda: tencent},
    )
    return {"eastmoney": em, "sina": sina, "tencent": tencent}


def _market(cfg: dict[str, str]) -> ConfigurableEquityMarket:
    return ConfigurableEquityMarket(config_reader=lambda key, fallback: cfg.get(key, fallback))


def test_normalize_provider_id() -> None:
    assert normalize_provider_id("东方财富") == "eastmoney"
    assert normalize_provider_id("新浪财经") == "sina"
    assert normalize_provider_id("腾讯财经") == "tencent"
    assert normalize_provider_id("eastmoney") == "eastmoney"
    assert normalize_provider_id("跟随默认") is None
    assert normalize_provider_id("") is None
    assert normalize_provider_id("不认识") is None
    assert PROVIDER_LABELS["eastmoney"] == "东方财富"


def test_default_routes_to_eastmoney(stub_registry) -> None:
    async def _run() -> None:
        m = _market({})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "eastmoney:quote"
        # 未配置时默认回落值 = 东方财富
        assert "quote" in stub_registry["eastmoney"].calls
        assert stub_registry["sina"].calls == []

    asyncio.run(_run())


def test_per_interface_override(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_quote": "新浪财经", "market_api_kline": "腾讯财经"})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "sina:quote"
        kl = await m.kline("600519", KlinePeriod.D1)
        assert kl == "tencent:kline"
        # 未覆盖的接口仍走默认（东财）
        hm = await m.hotmap()
        assert hm == "eastmoney:hotmap"

    asyncio.run(_run())


def test_follow_default_and_unknown_values(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_quote": "跟随默认", "market_api_default": "腾讯财经"})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        # 未知值视同跟随默认
        m2 = _market({"market_api_quote": " nonsense ", "market_api_default": "腾讯财经"})
        q2 = await m2.quote("600519")
        assert isinstance(q2, Quote)
        assert q2.symbol.name == "tencent:quote"

    asyncio.run(_run())


def test_unsupported_falls_back_to_default_then_eastmoney(stub_registry) -> None:
    async def _run() -> None:
        # 逐接口选腾讯 + 默认腾讯：board 腾讯不支持 → 默认(腾讯)跳过 → 回落东财
        m = _market({"market_api_board": "腾讯财经", "market_api_default": "腾讯财经"})
        board = await m.board(BoardKind.A_SHARE)
        assert board == "eastmoney:board"

        # 默认切新浪：northbound 新浪不支持 → 回落东财
        m2 = _market({"market_api_default": "新浪财经"})
        nb = await m2.northbound()
        assert nb == "eastmoney:northbound"
        # sina 收到了调用（先试过）
        assert "northbound" in stub_registry["sina"].calls

        # 所选源支持时不回落
        m3 = _market({"market_api_default": "新浪财经"})
        q = await m3.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "sina:quote"

    asyncio.run(_run())


def test_non_unsupported_error_passthrough(stub_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async def _boom(self: _StubPort, query: str) -> MarketError:
            self.calls.append("quote")
            return MarketError(code="network", message="boom", provider="eastmoney")

        monkeypatch.setattr(_StubPort, "quote", _boom)
        # quote 固定走东财（默认），网络错误原样上抛，不回落
        m = _market({"market_api_quote": "东方财富"})
        q = await m.quote("600519")
        assert is_market_error(q) and q.code == "network"
        assert stub_registry["sina"].calls == []

    asyncio.run(_run())


def test_quotes_follow_quote_routing(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_quote": "新浪财经"})
        results = await m.quotes(["600519", "000001"])
        assert len(results) == 2
        first = results[0]
        assert isinstance(first, Quote)
        assert first.symbol.name == "sina:quote"
        assert "quote" in stub_registry["sina"].calls

    asyncio.run(_run())


def test_resolve_uses_default_provider(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_default": "新浪财经"})
        ref = await m.resolve("600519")
        assert ref is not None
        assert ref.exchange == "sina"

    asyncio.run(_run())
