"""行情API 四域源链：链构建 / 域回落 / 链外兜底 / 顺延语义（不打真网）。"""

from __future__ import annotations

import time
import asyncio
from typing import Literal
from datetime import date
from collections.abc import Callable, Sequence

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
        self.sleep_ifaces: dict[str, float] = {}
        self.calls: list[str] = []

    async def _sleep_gate(self, iface: str) -> None:
        """模拟「连上了但不回包」的挂起源（超时/切片逻辑需要它）。"""
        if iface in self.sleep_ifaces:
            await asyncio.sleep(self.sleep_ifaces[iface])

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
        await self._sleep_gate("quote")
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


def _reader(cfg: dict[str, object]) -> Callable[[str, object], object]:
    """配置字典 → config_reader 形状（缺键回退 fallback）。"""
    return lambda key, fallback: cfg.get(key, fallback)


def _market(cfg: dict[str, object]) -> ConfigurableEquityMarket:
    return ConfigurableEquityMarket(config_reader=_reader(cfg))


# -- 源链解析（列表配置；别名 / 去重 / 未知项） ------------------------------


def test_parse_priority_chain_list_and_aliases() -> None:
    # 展示名 / 简称 / 裸 id 混用
    assert parse_priority_chain(["东方财富", "腾讯财经", "新浪财经"]) == ["eastmoney", "tencent", "sina"]
    assert parse_priority_chain(["东财", "腾讯", "新浪"]) == ["eastmoney", "tencent", "sina"]
    assert parse_priority_chain(["tencent", "sina", "eastmoney"]) == ["tencent", "sina", "eastmoney"]
    # 去重保序
    assert parse_priority_chain(["新浪", "新浪", "东财", "东财"]) == ["sina", "eastmoney"]
    # 未知项与空项忽略
    assert parse_priority_chain(["东方财富", "不认识", "腾讯", ""]) == ["eastmoney", "tencent"]
    # 空值 / 非列表一律忽略（配置类型由 Core 的 GsListStrConfig 保证）
    assert parse_priority_chain([]) == []
    assert parse_priority_chain(None) == []
    assert parse_priority_chain("东财→腾讯") == []


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


# -- 源链构建：域链 / 全局链 / 默认链 + 链外兜底 ------------------------------


def test_build_priority_chain_defaults() -> None:
    # 无任何配置 → 出厂链 = 系统默认顺序（东财→腾讯→新浪→同花顺）
    assert build_priority_chain(lambda key, fallback: fallback) == ["eastmoney", "tencent", "sina", "ths"]
    assert build_priority_chain(_reader({})) == ["eastmoney", "tencent", "sina", "ths"]


def test_build_priority_chain_global_and_outsiders_tail() -> None:
    # 全局链只写两个源：链内保序，链外（东财/同花顺）按内禀次序排链尾兜底
    cfg: dict[str, object] = {"market_api_chain": ["腾讯财经", "新浪财经"]}
    assert build_priority_chain(_reader(cfg)) == ["tencent", "sina", "eastmoney", "ths"]
    # 别名与裸 id 同样生效
    cfg = {"market_api_chain": ["sina", "tencent"]}
    assert build_priority_chain(_reader(cfg)) == ["sina", "tencent", "eastmoney", "ths"]


def test_build_priority_chain_group_overrides_and_falls_back() -> None:
    cfg: dict[str, object] = {
        "market_api_chain": ["东方财富"],
        "market_api_chain_quote": ["新浪财经"],
    }
    # 域链有值 → 用域链
    assert build_priority_chain(_reader(cfg), "quote") == ["sina", "eastmoney", "tencent", "ths"]
    # 域链留空 → 回落全局链
    assert build_priority_chain(_reader(cfg), "kline") == ["eastmoney", "tencent", "sina", "ths"]
    # 显式空列表同样回落全局链
    cfg = {"market_api_chain": ["腾讯财经"], "market_api_chain_quote": []}
    assert build_priority_chain(_reader(cfg), "quote") == ["tencent", "eastmoney", "sina", "ths"]


def test_build_priority_chain_invalid_ignored_with_backstop() -> None:
    # 全是无法识别的项：等价于没配 → 默认链；四个源一个不少
    cfg: dict[str, object] = {"market_api_chain": ["乱写的", "不存在"]}
    assert build_priority_chain(_reader(cfg)) == ["eastmoney", "tencent", "sina", "ths"]
    # 单源链：其余三源自动兜底补入，任何配置下都不缺源
    cfg = {"market_api_chain": ["同花顺"]}
    assert build_priority_chain(_reader(cfg)) == ["ths", "eastmoney", "tencent", "sina"]


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
        m = _market({"market_api_chain": ["腾讯财经"]})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        # 腾讯优先且成功，其余源未被调用
        assert stub_registry["eastmoney"].calls == []
        assert stub_registry["sina"].calls == []
        assert stub_registry["ths"].calls == []

    asyncio.run(_run())


def test_group_chain_isolated_from_global(stub_registry) -> None:
    async def _run() -> None:
        # 只配了盘口域链 → quote 走新浪；kline 域未配 → 仍走全局默认链（东财）
        m = _market({"market_api_chain_quote": ["新浪财经"]})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "sina:quote"
        kl = await m.kline("600519", KlinePeriod.D1)
        assert kl == "eastmoney:kline"
        assert "kline" in stub_registry["eastmoney"].calls

    asyncio.run(_run())


def test_unsupported_source_skipped(stub_registry) -> None:
    async def _run() -> None:
        # 腾讯排第一但不支持 board → 跳过（不算失败），东财承接
        m = _market({"market_api_chain_board": ["腾讯财经"]})
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


def test_out_of_chain_eastmoney_still_backstops(stub_registry) -> None:
    async def _run() -> None:
        # 独占域链不含东财：北向（另三源皆不支持）由链尾兜底的东财接住，
        # 而不是报「northbound 无源可用」—— 这是「任何配置下全功能可用」的核心保证
        m = _market({"market_api_chain_exclusive": ["腾讯财经", "新浪财经", "同花顺"]})
        nb = await m.northbound()
        assert nb == "eastmoney:northbound"
        assert "northbound" in stub_registry["tencent"].calls
        assert "northbound" in stub_registry["eastmoney"].calls
        # 域隔离：其余域不受影响，未配域链的 quote 走全局默认链（东财）
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "eastmoney:quote"

    asyncio.run(_run())


def test_quotes_follow_chain(stub_registry) -> None:
    async def _run() -> None:
        m = _market({"market_api_chain_quote": ["新浪财经"]})
        results = await m.quotes(["600519", "000001"])
        first = results[0]
        assert isinstance(first, Quote)
        assert first.symbol.name == "sina:quote"
        assert "quote" in stub_registry["sina"].calls

    asyncio.run(_run())


def test_resolve_uses_chain_head(stub_registry) -> None:
    async def _run() -> None:
        # resolve 走全局链链头（不配域链）
        m = _market({"market_api_chain": ["腾讯财经"]})
        ref = await m.resolve("600519")
        assert ref is not None
        assert ref.exchange == "tencent"
        # 只配域链不影响 resolve：仍走全局默认链头（东财）
        m2 = _market({"market_api_chain_quote": ["新浪财经"]})
        ref2 = await m2.resolve("600519")
        assert ref2 is not None
        assert ref2.exchange == "eastmoney"

    asyncio.run(_run())


# -- 分域表与配置键的一致性 ---------------------------------------------------


def test_every_routed_interface_group_has_a_config_key() -> None:
    """接口表的每个域都必须有对应配置键，否则该接口会静默回落全局链。"""
    assert set(pr._IFACE_GROUPS.values()) <= set(pr._GROUP_CHAIN_CONFIG_KEYS)


def test_exclusive_group_owns_the_single_source_interfaces() -> None:
    """只有东财实现的接口归 exclusive 组：用户在别的组里调优先级毫无意义。"""
    assert pr._IFACE_GROUPS["hotmap"] == "exclusive"
    assert pr._IFACE_GROUPS["northbound"] == "exclusive"
    assert pr._IFACE_GROUPS["valuation_series"] == "exclusive"
    assert pr._IFACE_GROUPS["financial_snapshot"] == "exclusive"
    # 多源接口不许混进独占组
    assert pr._IFACE_GROUPS["board"] == "board"
    assert pr._IFACE_GROUPS["breadth"] == "market"


def test_exclusive_chain_follows_its_own_key(stub_registry) -> None:
    async def _run() -> None:
        # exclusive 组有自己的键：配置生效（腾讯在链头，跳过不支持后仍由东财供数）
        m = _market({"market_api_chain_exclusive": ["腾讯财经"]})
        hm = await m.hotmap()
        assert hm == "eastmoney:hotmap"
        assert "hotmap" in stub_registry["tencent"].calls
        # 组间隔离：board 组没配 → 仍走全局默认链链头（东财），不受 exclusive 影响
        assert pr.build_priority_chain(_reader({"market_api_chain_exclusive": ["腾讯财经"]}), "board") == [
            "eastmoney",
            "tencent",
            "sina",
            "ths",
        ]

    asyncio.run(_run())


# -- 时间预算：慢源不许吃掉整条链 --------------------------------------------


def test_source_timeout_falls_through(stub_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """一个挂起的源必须被每源顶格切断并顺延，否则容灾形同虚设。"""

    async def _run() -> None:
        monkeypatch.setattr(pr, "SOURCE_TIMEOUT_S", 0.05)
        stub_registry["eastmoney"].sleep_ifaces = {"quote": 30.0}
        m = _market({})
        q = await m.quote("600519")
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"
        assert "quote" in stub_registry["tencent"].calls

    asyncio.run(_run())


def test_chain_deadline_reserves_time_for_the_rest_of_the_chain(stub_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """声明总预算后按「剩余预算/剩余源数」分片：链头挂起也轮得到后面的源。

    这正是模拟盘取价的场景——外层只有 wait_for(timeout) 时，链头一挂
    就把预算吃光，后面的源一个都不会开始。

    这里把每源下限压到 0.05s，免得为了等链头那 8s 的时间片把单测拖慢；
    下限本身的行为由 test_first_source_slice_covers_cold_start 单独钉。
    """

    async def _run() -> None:
        monkeypatch.setattr(pr, "MIN_SOURCE_SLICE_S", 0.05)
        stub_registry["eastmoney"].sleep_ifaces = {"quote": 30.0}
        m = _market({})
        started = time.monotonic()
        with pr.chain_deadline(0.8):
            q = await m.quote("600519")
        assert time.monotonic() - started < 2.0
        assert isinstance(q, Quote)
        assert q.symbol.name == "tencent:quote"

    asyncio.run(_run())


def test_first_source_slice_covers_cold_start() -> None:
    """4 源 12s 预算下链头必须拿到 > 实测冷启动 3.98s 的时间片。

    只按「剩余预算/剩余源数」平摊是 3s，会把一次**正常**的取价切在成功之前，
    于是整条链白跑（东财冷启动实测 3.98s）。下限同时不得让时间片越出剩余总预算。
    """

    async def _run() -> None:
        tol = 1e-6  # deadline 与 monotonic 都是大数浮点，减法有 1e-10 量级舍入
        assert pr._source_budget(4) == pr.SOURCE_TIMEOUT_S  # 无 deadline → 每源顶格
        with pr.chain_deadline(12.0):
            first = pr._source_budget(4)
            assert first >= pr.MIN_SOURCE_SLICE_S > 3.98, first
            assert first <= 12.0 + tol
            assert pr._source_budget(3) >= pr.MIN_SOURCE_SLICE_S  # 后续源同样吃下限
        # 预算比下限还小：只能拿到剩下的全部，不能凭空超支
        with pr.chain_deadline(0.8):
            assert pr._source_budget(4) <= 0.8 + tol
        # 预算耗尽 → 0，链应就地停止
        with pr.chain_deadline(-1.0):
            assert pr._source_budget(4) == 0.0

    asyncio.run(_run())


def test_exhausted_budget_stops_chain_with_market_error(stub_registry) -> None:
    """预算耗尽要就地停链并回 MarketError，不能把 None 漏给调用方。"""

    async def _run() -> None:
        stub_registry["eastmoney"].sleep_ifaces = {"quote": 30.0}
        m = _market({})
        with pr.chain_deadline(-1.0):
            q = await m.quote("600519")
        assert is_market_error(q)
        assert q.code == "network"
        assert stub_registry["tencent"].calls == []

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
