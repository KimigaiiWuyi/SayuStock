"""行情路由、优先级链、解析层、容灾切换与交易时段核心测试。"""

from __future__ import annotations

import json
import time
import asyncio
from typing import Literal
from datetime import date, datetime
from unittest.mock import patch
from collections.abc import Callable, Iterable, Sequence

import pytest

from SayuStock.utils import eastmoney as em
from SayuStock.utils.stock import request_utils as ru
from SayuStock.utils.market import get_market, set_market, provider_registry as pr
from SayuStock.utils.constant import PREFIX_DATA, chinese_stocks
from SayuStock.utils.time_range import Market, get_market_sessions, is_market_active_now
from SayuStock.utils.market.enums import (
    RankBy,
    BoardKind,
    IpoMarket,
    ValueKind,
    AssetClass,
    KlinePeriod,
)
from SayuStock.utils.market.errors import (
    MarketError,
    not_found,
    unsupported,
    network_error,
    is_market_error,
)
from SayuStock.utils.market.models import (
    Quote,
    IpoEvent,
    SymbolRef,
    BreadthBar,
    KlineSeries,
    ValueSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)
from SayuStock.utils.market.display import (
    PROVIDER_DISPLAY,
    source_label,
    source_footer,
    stamp_provider,
)
from SayuStock.utils.market_holidays import is_market_holiday
from SayuStock.stock_config.stock_config import STOCK_CONFIG
from SayuStock.utils.stock.request_utils import (
    ResolveLayerError,
    get_code_id,
    local_digit_code,
    get_code_id_strict,
)
from SayuStock.utils.market.adapters.sina import provider as sina_provider
from SayuStock.stock_config.config_default import CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsListStrConfig
from SayuStock.utils.market.adapters.tencent import provider as tencent_provider
from SayuStock.utils.market.provider_registry import (
    PROVIDER_LABELS,
    ConfigurableEquityMarket,
    build_priority_chain,
    parse_priority_chain,
    normalize_provider_id,
)
from SayuStock.utils.market.adapters.composite import CompositeMarketData
from SayuStock.utils.market.adapters.okx.provider import is_crypto_query
from SayuStock.utils.market.adapters.vix.provider import is_vix_query
from SayuStock.utils.market.adapters.sina.provider import SinaMarketData
from SayuStock.utils.market.adapters.tencent.provider import TencentMarketData


def _make_sym(query: str = "600519", name: str = "贵州茅台", exchange: str = "SSE") -> SymbolRef:
    return SymbolRef(
        code=query.split(".")[-1],
        name=name,
        asset_class=AssetClass.EQUITY,
        exchange=exchange,
        provider_symbol=query,
    )


def _make_quote(query: str = "600519", name: str = "贵州茅台", exchange: str = "SSE") -> Quote:
    return Quote(
        symbol=_make_sym(query, name=name, exchange=exchange),
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


class _StubPort:
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
        if iface in self.sleep_ifaces:
            await asyncio.sleep(self.sleep_ifaces[iface])

    def _err_or_none(self, iface: str) -> MarketError | None:
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
        return None

    async def resolve(self, query: str) -> SymbolRef | None:
        self.calls.append("resolve")
        return _make_sym(query, name=self.tag, exchange=self.tag)

    async def quote(self, query: str) -> Quote | MarketError:
        await self._sleep_gate("quote")
        err = self._err_or_none("quote")
        if err is not None:
            return err
        return _make_quote(query, name=f"{self.tag}:quote", exchange=self.tag)

    async def quotes(self, queries: Sequence[str]) -> list[Quote | MarketError]:
        return [await self.quote(q) for q in queries]

    async def batch_quotes(self, symbols: Sequence[str]) -> dict[str, Quote] | MarketError:
        err = self._err_or_none("batch_quotes")
        if err is not None:
            return err
        return {sym: _make_quote(sym[2:], name=f"{self.tag}:batch", exchange=self.tag) for sym in symbols}

    async def intraday(self, query: str, *, ndays: int = 1) -> IntradaySeries | MarketError:
        _ = query, ndays
        return unsupported("intraday", provider=self.tag)

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries | MarketError:
        _ = start, end
        err = self._err_or_none("kline")
        if err is not None:
            return err
        return KlineSeries(
            symbol=_make_sym(query, name=f"{self.tag}:kline", exchange=self.tag),
            period=period,
            bars=(),
            adjusted=False,
        )

    async def board(
        self,
        kind: BoardKind | str,
        *,
        sector: str | None = None,
        limit: int | None = None,
        sort_asc: bool = False,
    ) -> BoardSnapshot | MarketError:
        _ = sector, limit, sort_asc
        err = self._err_or_none("board")
        if err is not None:
            return err
        bkind = kind if isinstance(kind, BoardKind) else BoardKind.A_SHARE
        return BoardSnapshot(kind=bkind, title=f"{self.tag}:board", rows=())

    async def rank_list(
        self,
        rank_by: RankBy | str,
        *,
        limit: int = 20,
        high_first: bool | None = None,
    ) -> RankSnapshot | MarketError:
        _ = rank_by, limit, high_first
        return unsupported("rank_list", provider=self.tag)

    async def hotmap(self) -> BoardSnapshot | MarketError:
        err = self._err_or_none("hotmap")
        if err is not None:
            return err
        return BoardSnapshot(kind=BoardKind.INDUSTRY, title=f"{self.tag}:hotmap", rows=())

    async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str] | MarketError:
        _ = kind
        return unsupported("sector_menu", provider=self.tag)

    async def breadth(self) -> BreadthBar | MarketError:
        return unsupported("breadth", provider=self.tag)

    async def market_turnover(self) -> MarketTurnover | MarketError:
        return unsupported("market_turnover", provider=self.tag)

    async def northbound(self) -> NorthboundFlow | MarketError:
        err = self._err_or_none("northbound")
        if err is not None:
            return err
        return NorthboundFlow(sh_net_yi=1.0, sz_net_yi=2.0)

    async def valuation_series(self, query: str, kind: ValueKind) -> ValueSeries | MarketError:
        _ = query, kind
        return unsupported("valuation_series", provider=self.tag)

    async def financial_snapshot(self, code: str) -> FinancialSnapshot | MarketError:
        _ = code
        return unsupported("financial_snapshot", provider=self.tag)

    async def ipo_calendar(self, market: IpoMarket | str) -> list[IpoEvent] | MarketError:
        key = market.value if isinstance(market, IpoMarket) else str(market)
        err = self._err_or_none(f"ipo_{key}")
        if err is not None:
            return err
        im = market if isinstance(market, IpoMarket) else IpoMarket.CN
        return [IpoEvent(market=im, code=self.tag, name=f"{self.tag}:ipo_{key}")]


def _reader(cfg: dict[str, object]) -> Callable[[str, object], object]:
    return lambda key, fallback: cfg.get(key, fallback)


def _market(cfg: dict[str, object]) -> ConfigurableEquityMarket:
    return ConfigurableEquityMarket(config_reader=_reader(cfg))


def _patch_registry(monkeypatch: pytest.MonkeyPatch, *, include_nasdaq: bool = False) -> dict[str, _StubPort]:
    em_only = frozenset(
        {"hotmap", "breadth", "market_turnover", "northbound", "valuation_series", "financial_snapshot"}
    )
    em = _StubPort("eastmoney", unsupported_ifaces=frozenset({"batch_quotes"}))
    sina = _StubPort("sina", unsupported_ifaces=em_only)
    tencent = _StubPort("tencent", unsupported_ifaces=em_only | frozenset({"board", "rank_list"}))
    ths = _StubPort("ths", unsupported_ifaces=em_only | frozenset({"board", "rank_list", "intraday", "batch_quotes"}))
    factories: dict[str, Callable[[], _StubPort]] = {
        "eastmoney": lambda: em,
        "sina": lambda: sina,
        "tencent": lambda: tencent,
        "ths": lambda: ths,
    }
    stubs = {"eastmoney": em, "sina": sina, "tencent": tencent, "ths": ths}
    if include_nasdaq:
        nasdaq = _StubPort(
            "nasdaq",
            unsupported_ifaces=em_only
            | frozenset(
                {"quote", "batch_quotes", "intraday", "kline", "board", "rank_list", "sector_menu", "ipo_cn", "ipo_hk"}
            ),
        )
        factories["nasdaq"] = lambda: nasdaq
        stubs["nasdaq"] = nasdaq
    monkeypatch.setattr(pr, "_PROVIDER_FACTORIES", factories)
    return stubs


def test_composite_routing_and_slot_stamps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composite 按 query 路由权益/加密/VIX/OTC基金并盖对应槽位来源。"""
    import SayuStock.utils.market.adapters.composite as composite_mod

    assert is_crypto_query("BTC") and is_crypto_query("btc")
    assert not is_crypto_query("510300") and not is_crypto_query("btc 510300")
    assert is_vix_query("300VIX") and not is_vix_query("600519")

    class _EquityOtc(_StubPort):
        async def kline(
            self,
            query: str,
            period: KlinePeriod,
            *,
            start: date | None = None,
            end: date | None = None,
        ) -> KlineSeries | MarketError:
            _ = start, end
            return KlineSeries(
                symbol=SymbolRef(
                    code=query,
                    name="兴全合润",
                    asset_class=AssetClass.FUND,
                    exchange="OTC",
                    provider_symbol="150.163406",
                    sec_type="基金",
                ),
                period=period,
                bars=(),
                adjusted=False,
            )

    async def _yes(_q: str) -> bool:
        return True

    async def _no(_q: str) -> bool:
        return False

    async def _run() -> None:
        port = CompositeMarketData(_StubPort("equity"), _StubPort("crypto"), _StubPort("vix"))
        q_eq = await port.quote("600519")
        q_btc = await port.quote("BTC")
        q_vix = await port.quote("300VIX")
        assert not is_market_error(q_eq) and q_eq.provider is None
        assert not is_market_error(q_btc) and q_btc.provider == "okx"
        assert not is_market_error(q_vix) and q_vix.provider == "vix"

        mixed = await port.quotes(["btc", "510300"])
        assert not is_market_error(mixed[0]) and mixed[0].symbol.exchange == "crypto"
        assert not is_market_error(mixed[1]) and mixed[1].symbol.exchange == "equity"

        monkeypatch.setattr(composite_mod, "is_otc_fund_query", _yes)
        otc_port = CompositeMarketData(_StubPort("equity"), _StubPort("crypto"), _StubPort("vix"), _StubPort("fund"))
        k_fund = await otc_port.kline("720001", KlinePeriod.D1_YEAR)
        k_etf = await otc_port.kline("510300", KlinePeriod.D1_YEAR)
        assert not is_market_error(k_fund) and k_fund.symbol.exchange == "fund"
        assert not is_market_error(k_etf) and k_etf.symbol.exchange == "equity"

        monkeypatch.setattr(composite_mod, "is_otc_fund_query", _no)
        retry_port = CompositeMarketData(_EquityOtc("equity"), _StubPort("crypto"), _StubPort("vix"), _StubPort("fund"))
        k_retry = await retry_port.kline("兴全合润", KlinePeriod.D1_YEAR)
        assert not is_market_error(k_retry) and k_retry.symbol.exchange == "fund"

        fake = _StubPort("fake")
        set_market(fake)
        try:
            q_fake = await get_market().quote("anything")
            assert not is_market_error(q_fake) and q_fake.symbol.exchange == "fake"
        finally:
            set_market(None)

    asyncio.run(_run())


def test_priority_chain_parsing_and_shipped_defaults() -> None:
    """源链解析、去重、链外兜底、分域一致性及出货默认值等价性。"""
    assert parse_priority_chain(["东方财富", "腾讯", "新浪", "新浪", "未知", ""]) == ["eastmoney", "tencent", "sina"]
    assert normalize_provider_id("同花顺") == "ths" and normalize_provider_id("不存在") is None
    assert PROVIDER_LABELS["eastmoney"] == "东方财富" and PROVIDER_LABELS["ths"] == "同花顺"
    assert build_priority_chain(_reader({})) == ["eastmoney", "tencent", "sina", "ths"]
    assert build_priority_chain(_reader({"market_api_chain": ["腾讯财经", "新浪财经"]})) == [
        "tencent",
        "sina",
        "eastmoney",
        "ths",
    ]
    assert build_priority_chain(_reader({"market_api_chain": ["腾讯财经"], "market_api_chain_quote": []}), "quote") == [
        "tencent",
        "eastmoney",
        "sina",
        "ths",
    ]
    assert set(pr._IFACE_GROUPS.values()) <= set(pr._GROUP_CHAIN_CONFIG_KEYS)

    def shipped_reader(key: str, fallback: object) -> object:
        item = CONFIG_DEFAULT.get(key)
        return item.data if isinstance(item, GsListStrConfig) and item.data else fallback

    for key, item in CONFIG_DEFAULT.items():
        if key.startswith("market_api_chain"):
            assert isinstance(item, GsListStrConfig) and item.data and set(item.data) <= set(item.options)

    for group, cfg_key in pr._GROUP_CHAIN_CONFIG_KEYS.items():
        item = CONFIG_DEFAULT[cfg_key]
        assert isinstance(item, GsListStrConfig)
        capable = set(parse_priority_chain(item.options))
        new_chain = [p for p in build_priority_chain(shipped_reader, group) if p in capable]
        old_chain = [p for p in build_priority_chain(lambda _k, _f: [], group) if p in capable]
        assert new_chain == old_chain, group


def test_provider_routing_fallback_and_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    """网络/异常/unsupported 顺延，empty 不短路，not_found 短路，IPO 路由。"""
    stubs = _patch_registry(monkeypatch, include_nasdaq=True)

    async def _run() -> None:
        m_default = _market({})
        q = await m_default.quote("600519")
        assert isinstance(q, Quote) and q.provider == "eastmoney" and stubs["sina"].calls == []

        stubs["eastmoney"].fail_ifaces = {"quote": "network"}
        q2 = await m_default.quote("600519")
        assert isinstance(q2, Quote) and q2.provider == "tencent"

        stubs["eastmoney"].fail_ifaces = {"quote": "raise"}
        q3 = await m_default.quote("QQQ")
        assert isinstance(q3, Quote) and q3.provider == "tencent"

        m_exclusive = _market({"market_api_chain_exclusive": ["腾讯财经", "新浪财经", "同花顺"]})
        nb = await m_exclusive.northbound()
        assert isinstance(nb, NorthboundFlow) and nb.provider == "eastmoney"

        stubs["eastmoney"].fail_ifaces = {"quote": "not_found"}
        stubs["tencent"].calls.clear()
        nf = await m_default.quote("不存在")
        assert is_market_error(nf) and nf.code == "not_found" and stubs["tencent"].calls == []

        for s in stubs.values():
            s.fail_ifaces = {"quote": "network"}
        all_fail = await m_default.quote("600519")
        assert is_market_error(all_fail) and all_fail.provider == "eastmoney"

        for s in stubs.values():
            s.fail_ifaces = {}
        m_ipo = _market({"market_api_chain_ipo": ["纳斯达克"]})
        us_ipo = await m_ipo.ipo_calendar("us")
        cn_ipo = await m_ipo.ipo_calendar("cn")
        unknown_ipo = await m_ipo.ipo_calendar("xx")
        assert not is_market_error(us_ipo) and us_ipo[0].code == "nasdaq"
        assert not is_market_error(cn_ipo) and cn_ipo[0].code == "eastmoney"
        assert is_market_error(unknown_ipo) and unknown_ipo.code == "unsupported"

        stubs["tencent"].fail_ifaces = {"batch_quotes": "network"}
        batch = await m_default.batch_quotes(["sh600519"])
        assert isinstance(batch, dict) and batch["sh600519"].symbol.exchange == "sina"

    asyncio.run(_run())

    async def _fake_ref(query: str) -> SymbolRef:
        secid = "1.600519" if query == "600519" else query
        return _make_sym(secid)

    async def _empty_lines(symbols: Iterable[str]) -> dict[str, str]:
        return {str(sym): "" for sym in symbols}

    async def _check_empty() -> None:
        with (
            patch.object(tencent_provider, "resolve_em_symbol", _fake_ref),
            patch.object(tencent_provider, "fetch_qt_lines", _empty_lines),
        ):
            res_tx = await TencentMarketData().quote("600519")
        with (
            patch.object(sina_provider, "resolve_em_symbol", _fake_ref),
            patch.object(sina_provider, "fetch_hq_lines", _empty_lines),
        ):
            res_sn = await SinaMarketData().quote("600519")
        assert is_market_error(res_tx) and res_tx.code == "empty" and res_tx.provider == "tencent"
        assert is_market_error(res_sn) and res_sn.code == "empty" and res_sn.provider == "sina"

    asyncio.run(_check_empty())


def test_provider_timeout_and_chain_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """慢源每源超时切断，chain_deadline 分片保底且耗尽停止。"""
    stubs = _patch_registry(monkeypatch)

    async def _run() -> None:
        with pr.chain_deadline(12.0):
            assert pr._source_budget(4) >= pr.MIN_SOURCE_SLICE_S > 3.98
        with pr.chain_deadline(-1.0):
            assert pr._source_budget(4) == 0.0
            err = await _market({}).quote("600519")
            assert is_market_error(err) and err.code == "network" and stubs["tencent"].calls == []

        monkeypatch.setattr(pr, "SOURCE_TIMEOUT_S", 0.05)
        monkeypatch.setattr(pr, "MIN_SOURCE_SLICE_S", 0.05)
        stubs["eastmoney"].sleep_ifaces = {"quote": 30.0}
        started = time.monotonic()
        with pr.chain_deadline(0.8):
            q = await _market({}).quote("600519")
        assert time.monotonic() - started < 2.0
        assert isinstance(q, Quote) and q.provider == "tencent"

    asyncio.run(_run())


def test_resolve_layer_strict_and_local_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve strict/non-strict、A股本地短路与基金补名不窜代码。"""

    async def _layer_fail(_code: str, _priority: object = None) -> None:
        raise ResolveLayerError("timeout")

    with monkeypatch.context() as m:
        m.setattr(ru, "_get_code_id_one", _layer_fail)
        with pytest.raises(ResolveLayerError):
            asyncio.run(get_code_id_strict("QQQ"))
        assert asyncio.run(get_code_id("QQQ")) is None

    assert local_digit_code("600519") == ("1.600519", chinese_stocks["600519"]["name"], "沪A")
    assert local_digit_code("688981") == ("1.688981", chinese_stocks["688981"]["name"], "科创板")
    assert local_digit_code("300750") == ("0.300750", chinese_stocks["300750"]["name"], "创业板")
    assert local_digit_code("510300") == ("1.510300", "", "基金")
    assert local_digit_code("000300") is None

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("searchapi should not be called")

    monkeypatch.setattr(ru, "ClientSession", _boom)
    assert asyncio.run(get_code_id_strict("1.600519")) == ("1.600519", chinese_stocks["600519"]["name"], "沪A")
    assert asyncio.run(get_code_id_strict("600519")) == ("1.600519", chinese_stocks["600519"]["name"], "沪A")


def test_symbol_suffix_display_and_query_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """SymbolRef 展示名、市场后缀匹配与 scm/原油主连 本地重定向。"""
    assert SymbolRef("005930", "三星电子", AssetClass.EQUITY, "KRX", "177.005930", sec_type="韩股").display_name == (
        "三星电子 (韩股)"
    )
    assert SymbolRef("600519", "贵州茅台", AssetClass.EQUITY, "SSE", "1.600519").display_name == "贵州茅台"
    assert PREFIX_DATA["177"] == "韩股"

    kr_payload = {
        "QuotationCodeTable": {
            "Data": [
                {"QuoteID": "153.SSNGY", "Name": "三星电子(GDR)", "SecurityTypeName": "粉单"},
                {"QuoteID": "177.005930", "Name": "三星电子", "SecurityTypeName": "韩股"},
            ]
        }
    }
    calls: list[str] = []

    class _Resp:
        status = 200

        async def text(self) -> str:
            return json.dumps(kr_payload)

        async def __aenter__(self) -> "_Resp":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Sess:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Sess":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str, params: Iterable[tuple[str, str]] | None = None) -> _Resp:
            if params is not None:
                calls.append(dict(params).get("input", ""))
            return _Resp()

    monkeypatch.setattr(ru, "ClientSession", _Sess)
    assert asyncio.run(get_code_id("scm")) == ("142.scm", "原油主连", "期货")
    assert asyncio.run(get_code_id("原油主连")) == ("142.scm", "原油主连", "期货")
    assert calls == []
    assert asyncio.run(get_code_id("三星电子.kr")) == ("177.005930", "三星电子", "韩股")
    assert calls == ["三星电子"]


def test_eastmoney_domain_fallback_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财 push2 超时切换备用域名，Cookie/UA 在各域名均保留。"""
    attempts: list[str] = []
    timeout_hosts: set[str] = {"push2.eastmoney.com"}
    seen_headers: dict[str, str] = {}

    class _Resp:
        status = 200

        def __init__(self, payload: object) -> None:
            self._payload = payload

        async def json(self, content_type: str | None = None) -> object:
            _ = content_type
            return self._payload

        async def text(self) -> str:
            return ""

    class _CM:
        def __init__(self, *, resp: _Resp | None = None, exc: BaseException | None = None) -> None:
            self._resp = resp
            self._exc = exc

        async def __aenter__(self) -> _Resp | None:
            if self._exc is not None:
                raise self._exc
            return self._resp

        async def __aexit__(self, *_args: object) -> bool:
            return False

    class _Sess:
        def __init__(self, *args: object, headers: dict[str, str], **kwargs: object) -> None:
            _ = args, kwargs
            seen_headers.clear()
            seen_headers.update(headers)

        async def __aenter__(self) -> "_Sess":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        def request(self, _method: str, *, url: str, **_kwargs: object) -> _CM:
            attempts.append(url)
            host = url.split("/")[2]
            if host in timeout_hosts:
                return _CM(exc=asyncio.TimeoutError())
            return _CM(resp=_Resp({"rc": 0, "data": {"hit": host}}))

    monkeypatch.setattr(em, "ClientSession", _Sess)
    values = {"eastmoney_cookie": "test_session=ok", "eastmoney_user_agent": "test-UA"}
    monkeypatch.setattr(STOCK_CONFIG, "get_config", lambda key: GsStrConfig(key, key, values.get(key, "")))

    requester = em.EastMoneyRequester()
    resp = asyncio.run(requester.stock_request("https://push2.eastmoney.com/api/qt/stock/get"))
    assert isinstance(resp, dict) and resp["data"]["hit"] == "push2delay.eastmoney.com"
    assert requester.preferred_push_domain == "push2delay"
    assert seen_headers["Cookie"] == "test_session=ok" and seen_headers["User-Agent"] == "test-UA"

    attempts.clear()
    timeout_hosts.add("push2delay.eastmoney.com")
    assert asyncio.run(requester.stock_request("https://push2.eastmoney.com/api/qt/stock/get")) == -400016


def test_market_sessions_and_holidays() -> None:
    """各市场 BJT [start, end) 开收盘边界、周末及节假日日历。"""
    assert is_market_active_now("1.000001", datetime(2026, 8, 24, 9, 30)) is True
    assert is_market_active_now("1.000001", datetime(2026, 8, 24, 11, 30)) is False
    assert is_market_active_now("1.000001", datetime(2026, 8, 24, 15, 0)) is False
    assert is_market_active_now("100.HSI", datetime(2026, 8, 24, 15, 30)) is True
    assert is_market_active_now("100.HSI", datetime(2026, 8, 24, 16, 0)) is False
    assert get_market_sessions(Market.JP_INDEX, datetime(2026, 8, 24, 10, 0)) == [
        ("08:00", "10:30"),
        ("11:30", "14:30"),
    ]
    assert is_market_active_now("BTC", datetime(2026, 8, 22, 10, 0)) is True
    assert is_market_active_now("100.NDX", datetime(2026, 8, 22, 2, 0)) is True

    assert is_market_holiday(Market.CRYPTO, datetime(2026, 10, 1, 10, 0)) is False
    assert is_market_holiday(Market.FX, datetime(2026, 1, 1, 20, 0)) is True
    pytest.importorskip("holidays")
    assert is_market_holiday(Market.A_SHARE, datetime(2026, 10, 1, 10, 0)) is True
    assert is_market_holiday(Market.US_STOCK, datetime(2026, 5, 25, 23, 0)) is True


def test_source_label_and_footer() -> None:
    """数据来源展示名、多源拼接与模型盖章不覆盖已有来源。"""
    for pid in ("eastmoney", "tencent", "sina", "ths", "tiantian", "okx", "vix"):
        assert pid in PROVIDER_DISPLAY
    assert source_label("eastmoney", "tencent") == "东方财富、腾讯财经"
    assert source_footer("sina", "sina", note="技术分析") == "数据来源：新浪财经 | SayuStock 技术分析"
    q = stamp_provider(_make_quote(), "tencent")
    assert isinstance(q, Quote) and q.provider == "tencent"
    assert stamp_provider(q, "sina").provider == "tencent"
