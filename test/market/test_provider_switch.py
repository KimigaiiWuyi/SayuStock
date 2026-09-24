"""行情API 全局优先级链：解析 / 顺延 / 跳过 / 短路语义（不打真网）。"""

from __future__ import annotations

import asyncio
from typing import Literal
from datetime import date
from collections.abc import Sequence

import pytest

from SayuStock.utils.market import provider_registry as pr
from SayuStock.utils.market.enums import RankBy, BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.errors import (
    MarketError,
    not_found,
    unsupported,
    network_error,
    is_market_error,
)
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
    build_priority_chain,
    parse_priority_chain,
    normalize_provider_id,
)


class _StubPort:
    """记录调用；按接口可声明 unsupported / 失败错误码，成功返回 tag 标记。"""

    def __init__(
        self,
        tag: str,
        unsupported_ifaces: frozenset[str] = frozenset(),
        fail_ifaces: dict[str, str] | None = None,
    ) -> None:
        self.tag = tag
        self.unsupported_ifaces = unsupported_ifaces
        self.fail_ifaces: dict[str, str] = fail_ifaces or {}
        self.calls: list[str] = []

    def _ret(self, iface: str) -> object:
        self.calls.append(iface)
        if iface in self.unsupported_ifaces:
            return unsupported(f"{iface} 未实现", provider=self.tag)
        if iface in self.fail_ifaces:
            code = self.fail_ifaces[iface]
            if code == "not_found":
                return not_found("未找到标的", provider=self.tag)
            if code == "raise":
                raise RuntimeError("源内部异常")
            return network_error("boom", provider=self.tag)
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
    # 东财独占接口：云图/宽度/成交额/北向/估值/财报
    em_only = frozenset(
        {"hotmap", "breadth", "market_turnover", "northbound", "valuation_series", "financial_snapshot"}
    )
    em = _StubPort("eastmoney")
    sina = _StubPort("sina", unsupported_ifaces=em_only)
    tencent = _StubPort("tencent", unsupported_ifaces=em_only | frozenset({"board", "rank_list"}))
    monkeypatch.setattr(
        pr,
        "_PROVIDER_FACTORIES",
        {"eastmoney": lambda: em, "sina": lambda: sina, "tencent": lambda: tencent},
    )
    return {"eastmoney": em, "sina": sina, "tencent": tencent}


def _market(cfg: dict[str, str]) -> ConfigurableEquityMarket:
    return ConfigurableEquityMarket(config_reader=lambda key, fallback: cfg.get(key, fallback))


# -- 链解析 ---------------------------------------------------------------


def test_parse_priority_chain_separators_and_aliases() -> None:
    # 下拉默认值（箭头）
    assert parse_priority_chain("东方财富 → 腾讯财经 → 新浪财经") == ["eastmoney", "tencent", "sina"]
    # 英文逗号 / 中文逗号 / 大于号 / 简称 / 裸 id 混用
    assert parse_priority_chain("东财,腾讯,新浪") == ["eastmoney", "tencent", "sina"]
    assert parse_priority_chain("tencent，sina>eastmoney") == ["tencent", "sina", "eastmoney"]
    # 去重保序
    assert parse_priority_chain("新浪,新浪,东财,东财") == ["sina", "eastmoney"]
    # 未知片段忽略
    assert parse_priority_chain("东方财富,不认识,腾讯") == ["eastmoney", "tencent"]
    # 空值
    assert parse_priority_chain("") == []
    assert parse_priority_chain(None) == []


def test_normalize_provider_id() -> None:
    assert normalize_provider_id("东方财富") == "eastmoney"
    assert normalize_provider_id("东财") == "eastmoney"
    assert normalize_provider_id("新浪") == "sina"
    assert normalize_provider_id("tencent") == "tencent"
    assert normalize_provider_id("") is None
    assert normalize_provider_id("不认识") is None
    assert PROVIDER_LABELS["eastmoney"] == "东方财富"


def test_build_priority_chain_appends_eastmoney_fallback() -> None:
    def reader(key: str, fallback: str) -> str:
        return "腾讯财经 → 新浪财经"

    assert build_priority_chain(reader) == ["tencent", "sina", "eastmoney"]

    def reader2(key: str, fallback: str) -> str:
        return "东方财富 → 腾讯财经 → 新浪财经"

    assert build_priority_chain(reader2) == ["eastmoney", "tencent", "sina"]

    # 配置损坏（全未知）退回东财
    def reader3(key: str, fallback: str) -> str:
        return "乱写的"

    assert build_priority_chain(reader3) == ["eastmoney"]

    # 读取失败用 fallback 默认链
    def reader4(key: str, fallback: str) -> str:
        return fallback

    assert build_priority_chain(reader4) == ["eastmoney", "tencent", "sina"]


# -- 路由语义 -------------------------------------------------------------


def test_default_chain_routes_to_eastmoney_first(stub_registry) -> None:
    async def _run() -> None:
        m = _market({})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "eastmoney:quote"
        assert "quote" in stub_registry["eastmoney"].calls
        assert stub_registry["sina"].calls == []
        assert stub_registry["tencent"].calls == []

    asyncio.run(_run())


def test_custom_priority_order(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority": "腾讯财经 → 东方财富 → 新浪财经"})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        # 腾讯优先且成功，其余源未被调用
        assert stub_registry["eastmoney"].calls == []
        assert stub_registry["sina"].calls == []

    asyncio.run(_run())


def test_unsupported_source_skipped(stub_registry) -> None:
    async def _run() -> None:
        # 腾讯排第一但不支持 board → 跳过（不算失败），东财承接
        m = _market({"market_api_priority": "腾讯财经 → 东方财富 → 新浪财经"})
        board = await m.board(BoardKind.A_SHARE)
        assert board == "eastmoney:board"
        assert "board" in stub_registry["tencent"].calls
        assert "board" in stub_registry["eastmoney"].calls

    asyncio.run(_run())


def test_network_error_falls_through(stub_registry) -> None:
    async def _run() -> None:
        # 东财网络失败 → 顺延腾讯成功
        stub_registry["eastmoney"].fail_ifaces = {"quote": "network"}
        m = _market({"market_api_priority": "东方财富 → 腾讯财经 → 新浪财经"})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"

        # 东财挂 + 腾讯无 board（跳过）→ 新浪承接 board
        stub_registry["eastmoney"].fail_ifaces = {"board": "network"}
        board = await m.board(BoardKind.A_SHARE)
        assert board == "sina:board"

    asyncio.run(_run())


def test_all_fail_returns_highest_priority_real_error(stub_registry) -> None:
    async def _run() -> None:
        # 三个源都网络失败 → 返回东财（优先级最高的真实错误）
        stub_registry["eastmoney"].fail_ifaces = {"quote": "network"}
        stub_registry["tencent"].fail_ifaces = {"quote": "network"}
        stub_registry["sina"].fail_ifaces = {"quote": "network"}
        m = _market({"market_api_priority": "东方财富 → 腾讯财经 → 新浪财经"})
        q = await m.quote("600519")
        assert is_market_error(q)
        assert q.provider == "eastmoney"
        assert q.code == "network"
        # 三个源都被尝试过
        assert "quote" in stub_registry["tencent"].calls
        assert "quote" in stub_registry["sina"].calls

    asyncio.run(_run())


def test_not_found_short_circuits(stub_registry) -> None:
    async def _run() -> None:
        # 东财 not_found：解析层共用，直接返回不换源
        stub_registry["eastmoney"].fail_ifaces = {"quote": "not_found"}
        m = _market({"market_api_priority": "东方财富 → 腾讯财经 → 新浪财经"})
        q = await m.quote("不存在的股票")
        assert is_market_error(q)
        assert q.code == "not_found"
        assert q.provider == "eastmoney"
        # 后续源未被尝试
        assert stub_registry["tencent"].calls == []
        assert stub_registry["sina"].calls == []

    asyncio.run(_run())


def test_provider_exception_falls_through(stub_registry) -> None:
    async def _run() -> None:
        # 源内部抛异常（如东财瞬断时 aiohttp 连接错误穿透）→ 转 network 错误顺延，不炸链
        stub_registry["eastmoney"].fail_ifaces = {"quote": "raise"}
        m = _market({"market_api_priority": "东方财富 → 腾讯财经 → 新浪财经"})
        q = await m.quote("QQQ")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        assert "quote" in stub_registry["tencent"].calls
        # 全链都抛异常 → 返回优先级最高源的 network 错误而非向上冒泡
        stub_registry["tencent"].fail_ifaces = {"quote": "raise"}
        stub_registry["sina"].fail_ifaces = {"quote": "raise"}
        q2 = await m.quote("QQQ")
        assert is_market_error(q2)
        assert q2.provider == "eastmoney"
        assert q2.code == "network"

    asyncio.run(_run())


def test_eastmoney_only_interface_survives_without_eastmoney_in_chain(stub_registry) -> None:
    async def _run() -> None:
        # 链里没有东财：东财独占接口（northbound）自动补链尾兜底
        m = _market({"market_api_priority": "腾讯财经 → 新浪财经"})
        nb = await m.northbound()
        # 腾讯/新浪 unsupported 跳过 → 东财承接
        assert nb == "eastmoney:northbound"
        assert "northbound" in stub_registry["eastmoney"].calls

    asyncio.run(_run())


def test_quotes_follow_chain(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority": "新浪财经 → 东方财富 → 腾讯财经"})
        results = await m.quotes(["600519", "000001"])
        first = results[0]
        assert isinstance(first, Quote)
        assert first.symbol.name == "sina:quote"
        assert "quote" in stub_registry["sina"].calls

    asyncio.run(_run())


def test_resolve_uses_chain_head(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority": "腾讯财经 → 东方财富 → 新浪财经"})
        ref = await m.resolve("600519")
        assert ref is not None
        assert ref.exchange == "tencent"

    asyncio.run(_run())


def test_winner_provider_stamped(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority": "东方财富 → 腾讯财经 → 新浪财经"})
        # 链头正常命中 → 盖链头 id
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.provider == "eastmoney"
        # 东财失败 → 腾讯承接 → 盖腾讯 id（渲染层据此展示真实数据来源）
        stub_registry["eastmoney"].fail_ifaces = {"quote": "network"}
        q2 = await m.quote("600519")
        assert isinstance(q2, Quote)
        assert q2.provider == "tencent"
        # 非模型结果（stub 返回字符串）原样透传，不受盖章影响
        stub_registry["eastmoney"].fail_ifaces = {"kline": "network"}
        kl = await m.kline("600519", KlinePeriod.D1)
        assert kl == "tencent:kline"

    asyncio.run(_run())
