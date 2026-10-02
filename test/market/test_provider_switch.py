"""行情API 每源优先级数字：链构建 / 平局 / 禁用 / 迁移 / 顺延语义（不打真网）。"""

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
    legacy_chain_to_numbers,
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
        return (
            result
            if isinstance(result, MarketError)
            else Quote(
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
    ths = _StubPort("ths", unsupported_ifaces=em_only | frozenset({"board", "rank_list", "intraday"}))
    monkeypatch.setattr(
        pr,
        "_PROVIDER_FACTORIES",
        {
            "eastmoney": lambda: em,
            "sina": lambda: sina,
            "tencent": lambda: tencent,
            "ths": lambda: ths,
        },
    )
    return {"eastmoney": em, "sina": sina, "tencent": tencent, "ths": ths}


def _market(cfg: dict[str, str]) -> ConfigurableEquityMarket:
    return ConfigurableEquityMarket(config_reader=lambda key, fallback: cfg.get(key, fallback))


# -- 旧链串解析（迁移依赖） --------------------------------------------------


def test_parse_priority_chain_separators_and_aliases() -> None:
    # 旧下拉默认值（箭头）
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
    assert normalize_provider_id("同花顺") == "ths"
    assert normalize_provider_id("ths") == "ths"
    assert normalize_provider_id("") is None
    assert normalize_provider_id("不认识") is None
    assert PROVIDER_LABELS["eastmoney"] == "东方财富"
    assert PROVIDER_LABELS["ths"] == "同花顺"


# -- 每源优先级数字 → 调用链 ------------------------------------------------


def test_build_priority_chain_defaults() -> None:
    # 无配置 → 出厂链 = 系统默认顺序（东财→腾讯→新浪→同花顺）
    assert build_priority_chain(lambda key, fallback: fallback) == ["eastmoney", "tencent", "sina", "ths"]
    assert build_priority_chain(lambda key, fallback: "") == ["eastmoney", "tencent", "sina", "ths"]


def test_build_priority_chain_custom_numbers() -> None:
    # 腾讯拉到 50 → 链头腾讯
    cfg = {"market_api_priority_tencent": "50"}
    assert build_priority_chain(lambda key, fallback: cfg.get(key, fallback)) == [
        "tencent",
        "eastmoney",
        "sina",
        "ths",
    ]
    # 同花顺拉到 90、新浪 80 → 同花顺、新浪、东财、腾讯
    cfg = {"market_api_priority_ths": "90", "market_api_priority_sina": "80"}
    assert build_priority_chain(lambda key, fallback: cfg.get(key, fallback)) == [
        "ths",
        "sina",
        "eastmoney",
        "tencent",
    ]


def test_build_priority_chain_tie_uses_default_order() -> None:
    # 全部 25：数字相同 → 按系统默认顺序（东财→腾讯→新浪→同花顺）裁决
    cfg = {
        "market_api_priority_eastmoney": "25",
        "market_api_priority_tencent": "25",
        "market_api_priority_sina": "25",
        "market_api_priority_ths": "25",
    }
    assert build_priority_chain(lambda key, fallback: cfg.get(key, fallback)) == [
        "eastmoney",
        "tencent",
        "sina",
        "ths",
    ]


def test_build_priority_chain_zero_disables() -> None:
    # 0 = 禁用：东财禁用后不再自动补链尾（独占接口随之无兜底）
    cfg = {"market_api_priority_eastmoney": "0"}
    assert build_priority_chain(lambda key, fallback: cfg.get(key, fallback)) == ["tencent", "sina", "ths"]
    # 全部禁用 → 保底东财，避免行情整体瘫痪
    cfg = {
        "market_api_priority_eastmoney": "0",
        "market_api_priority_tencent": "0",
        "market_api_priority_sina": "0",
        "market_api_priority_ths": "0",
    }
    assert build_priority_chain(lambda key, fallback: cfg.get(key, fallback)) == ["eastmoney"]


def test_build_priority_chain_clamps_and_invalid() -> None:
    # 越界夹到 0-100：150→100 仍最高；-5→0 禁用
    cfg = {"market_api_priority_sina": "150", "market_api_priority_ths": "-5"}
    chain = build_priority_chain(lambda key, fallback: cfg.get(key, fallback))
    assert chain == ["sina", "eastmoney", "tencent"]
    # 非法值 → 回该源出厂默认
    cfg = {"market_api_priority_eastmoney": "乱写的"}
    assert build_priority_chain(lambda key, fallback: cfg.get(key, fallback)) == [
        "eastmoney",
        "tencent",
        "sina",
        "ths",
    ]


# -- 旧链串 → 数字迁移 ------------------------------------------------------


def test_legacy_chain_to_numbers() -> None:
    # 默认链 → 出厂数字
    assert legacy_chain_to_numbers(["eastmoney", "tencent", "sina"]) == {
        "eastmoney": 40,
        "tencent": 30,
        "sina": 20,
    }
    # 链里没东财：先按旧语义补链尾，再按位置转数字
    assert legacy_chain_to_numbers(["tencent", "sina"]) == {"tencent": 40, "sina": 30, "eastmoney": 20}
    # 链外旧源 = 0（禁用，旧语义里链外源不参与）
    assert legacy_chain_to_numbers(["sina", "eastmoney"]) == {"sina": 40, "eastmoney": 30, "tencent": 0}
    assert legacy_chain_to_numbers(["eastmoney"]) == {"eastmoney": 40, "tencent": 0, "sina": 0}
    # 同花顺不在旧体系，迁移结果不含 ths（保持出厂默认）


# -- 路由语义 ---------------------------------------------------------------


def test_default_chain_routes_to_eastmoney_first(stub_registry) -> None:
    async def _run() -> None:
        m = _market({})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "eastmoney:quote"
        assert "quote" in stub_registry["eastmoney"].calls
        assert stub_registry["sina"].calls == []
        assert stub_registry["tencent"].calls == []
        assert stub_registry["ths"].calls == []

    asyncio.run(_run())


def test_custom_priority_order(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority_tencent": "50"})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        # 腾讯优先且成功，其余源未被调用
        assert stub_registry["eastmoney"].calls == []
        assert stub_registry["sina"].calls == []
        assert stub_registry["ths"].calls == []

    asyncio.run(_run())


def test_unsupported_source_skipped(stub_registry) -> None:
    async def _run() -> None:
        # 腾讯排第一但不支持 board → 跳过（不算失败），东财承接
        m = _market({"market_api_priority_tencent": "50"})
        board = await m.board(BoardKind.A_SHARE)
        assert board == "eastmoney:board"
        assert "board" in stub_registry["tencent"].calls
        assert "board" in stub_registry["eastmoney"].calls

    asyncio.run(_run())


def test_network_error_falls_through(stub_registry) -> None:
    async def _run() -> None:
        # 东财网络失败 → 顺延腾讯成功
        stub_registry["eastmoney"].fail_ifaces = {"quote": "network"}
        m = _market({})
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
        # 四个源都网络失败 → 返回东财（优先级最高的真实错误）
        for stub in stub_registry.values():
            stub.fail_ifaces = {"quote": "network"}
        m = _market({})
        q = await m.quote("600519")
        assert is_market_error(q)
        assert q.provider == "eastmoney"
        assert q.code == "network"
        # 四个源都被尝试过
        for stub in stub_registry.values():
            assert "quote" in stub.calls

    asyncio.run(_run())


def test_not_found_short_circuits(stub_registry) -> None:
    async def _run() -> None:
        # 东财 not_found：解析层共用，直接返回不换源
        stub_registry["eastmoney"].fail_ifaces = {"quote": "not_found"}
        m = _market({})
        q = await m.quote("不存在的股票")
        assert is_market_error(q)
        assert q.code == "not_found"
        assert q.provider == "eastmoney"
        # 后续源未被尝试
        assert stub_registry["tencent"].calls == []
        assert stub_registry["sina"].calls == []
        assert stub_registry["ths"].calls == []

    asyncio.run(_run())


def test_provider_exception_falls_through(stub_registry) -> None:
    async def _run() -> None:
        # 源内部抛异常（如东财瞬断时 aiohttp 连接错误穿透）→ 转 network 错误顺延，不炸链
        stub_registry["eastmoney"].fail_ifaces = {"quote": "raise"}
        m = _market({})
        q = await m.quote("QQQ")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        assert "quote" in stub_registry["tencent"].calls
        # 全链都抛异常 → 返回优先级最高源的 network 错误而非向上冒泡
        for stub in stub_registry.values():
            stub.fail_ifaces = {"quote": "raise"}
        q2 = await m.quote("QQQ")
        assert is_market_error(q2)
        assert q2.provider == "eastmoney"
        assert q2.code == "network"

    asyncio.run(_run())


def test_disabled_eastmoney_not_appended(stub_registry) -> None:
    async def _run() -> None:
        # 东财=0（禁用）：独占接口不再自动补链尾兜底 → 链上源全部 unsupported 后报错
        m = _market({"market_api_priority_eastmoney": "0"})
        nb = await m.northbound()
        assert is_market_error(nb)
        assert nb.provider == "tencent"  # 链头腾讯的 unsupported
        assert "northbound" in stub_registry["tencent"].calls
        assert stub_registry["eastmoney"].calls == []
        # 普通接口照常由腾讯承接
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"

    asyncio.run(_run())


def test_quotes_follow_chain(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority_sina": "60"})
        results = await m.quotes(["600519", "000001"])
        first = results[0]
        assert isinstance(first, Quote)
        assert first.symbol.name == "sina:quote"
        assert "quote" in stub_registry["sina"].calls

    asyncio.run(_run())


def test_resolve_uses_chain_head(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_priority_tencent": "50"})
        ref = await m.resolve("600519")
        assert ref is not None
        assert ref.exchange == "tencent"

    asyncio.run(_run())


def test_winner_provider_stamped(stub_registry) -> None:
    async def _run() -> None:
        m = _market({})
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
