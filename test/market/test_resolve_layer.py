"""解析层（东财 searchapi）瞬断 ≠ 标的不存在：strict 契约与适配器转换。

背景：解析层失败若误报 not_found 会短路整条行情优先级链（换源无意义），
用户对真实存在的股票看到「不存在该股票」；必须转 network 错误顺延。
"""

from __future__ import annotations

import asyncio

import pytest

from SayuStock.utils.stock import request_utils
from SayuStock.utils.market.enums import ValueKind
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.stock.request_utils import (
    ResolveLayerError,
    get_code_id,
    local_digit_code,
    get_code_id_strict,
)
from SayuStock.utils.market.adapters.sina.provider import SinaMarketData
from SayuStock.utils.market.adapters.tencent.provider import TencentMarketData
from SayuStock.utils.market.adapters.eastmoney.provider import EastMoneyMarketData


def _patch_one(monkeypatch, behavior) -> None:
    """按代码路由的 _get_code_id_one 假实现：Exception 抛出，None 为不存在。"""

    async def fake_one(code, priority=None):
        result = behavior(code)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(request_utils, "_get_code_id_one", fake_one)


def test_strict_raises_on_layer_failure(monkeypatch) -> None:
    _patch_one(monkeypatch, lambda code: ResolveLayerError("timeout"))
    with pytest.raises(ResolveLayerError):
        asyncio.run(get_code_id_strict("QQQ"))


def test_non_strict_swallows_layer_failure(monkeypatch) -> None:
    # 旧契约保持：get_code_id 解析层失败 → None（存量调用方不受影响）
    _patch_one(monkeypatch, lambda code: ResolveLayerError("timeout"))
    assert asyncio.run(get_code_id("QQQ")) is None


def test_none_means_not_found_in_both_modes(monkeypatch) -> None:
    _patch_one(monkeypatch, lambda code: None)
    assert asyncio.run(get_code_id_strict("QQQ")) is None
    assert asyncio.run(get_code_id("QQQ")) is None


def test_strict_prefers_hit_over_layer_error(monkeypatch) -> None:
    # 复合 query：前面的候选解析层失败，后面的候选命中 → 返回命中
    def behavior(code: str):
        if code == "QQQ":
            return ResolveLayerError("timeout")
        return ("105.QQQ", "纳斯达克100ETF-Invesco", "美股")

    _patch_one(monkeypatch, behavior)
    hit = asyncio.run(get_code_id_strict("QQQ 纳指ETF"))
    assert hit == ("105.QQQ", "纳斯达克100ETF-Invesco", "美股")
    # 全部候选解析层失败 → 抛出
    _patch_one(monkeypatch, lambda code: ResolveLayerError("timeout"))
    with pytest.raises(ResolveLayerError):
        asyncio.run(get_code_id_strict("QQQ 纳指ETF"))


def test_secid_form_carries_local_name_without_network() -> None:
    # secid 形态本地短路，名称从随仓库分发的 A 股表补 ——
    # 缺名会让同花顺（快照无名称）把 Quote.symbol.name 退化成代码，
    # 模拟盘 matcher._is_st 随之判不出 ST
    from SayuStock.utils.constant import chinese_stocks

    hit = asyncio.run(get_code_id_strict("1.600519"))
    assert hit is not None
    assert hit[1] == chinese_stocks["600519"]["name"] != ""


def test_local_digit_code_maps_share_and_fund() -> None:
    from SayuStock.utils.constant import chinese_stocks

    hit = local_digit_code("600519")
    assert hit == ("1.600519", chinese_stocks["600519"]["name"], "沪A")
    star = local_digit_code("688981")
    assert star is not None and star[0] == "1.688981" and star[2] == "科创板"
    gem = local_digit_code("300750")
    assert gem is not None and gem[0] == "0.300750" and gem[2] == "创业板"
    bank = local_digit_code("000001")
    assert bank == ("0.000001", chinese_stocks["000001"]["name"], "深A")
    etf = local_digit_code("510300")
    assert etf == ("1.510300", "", "基金")
    # 000905 是厦门港务，不是中证500；中证500 只走 1.000905 / 名称表
    port = local_digit_code("000905")
    assert port == ("0.000905", chinese_stocks["000905"]["name"], "深A")
    assert local_digit_code("000300") is None


def test_bare_share_code_does_not_call_searchapi(monkeypatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("searchapi")

    monkeypatch.setattr(request_utils, "ClientSession", _boom)
    hit = asyncio.run(get_code_id_strict("600519"))
    assert hit is not None
    assert hit[0] == "1.600519"


def test_bare_index_code_still_reaches_searchapi(monkeypatch) -> None:
    called = {"n": 0}

    class _Boom:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            called["n"] += 1
            raise ResolveLayerError("blocked")

    monkeypatch.setattr(request_utils, "ClientSession", _Boom)
    with pytest.raises(ResolveLayerError):
        asyncio.run(get_code_id_strict("000300"))
    assert called["n"] == 1


def test_secid_form_skips_name_on_market_mismatch() -> None:
    # 1.000001 是上证指数，不是 000001 平安银行：撞码时不许把股票名贴到指数上
    hit = asyncio.run(get_code_id_strict("1.000001"))
    assert hit == ("1.000001", "", "沪A")


def test_em_quote_layer_error_returns_network(monkeypatch) -> None:
    async def fake_strict(code, priority=None):
        raise ResolveLayerError("searchapi down")

    monkeypatch.setattr(request_utils, "get_code_id_strict", fake_strict)
    result = asyncio.run(EastMoneyMarketData().quote("QQQ"))
    assert is_market_error(result)
    assert result.code == "network"
    assert result.provider == "eastmoney"


def test_em_valuation_series_layer_error_returns_network(monkeypatch) -> None:
    # valuation_series 曾漏用加固解析：解析层瞬断被误报 not_found，
    # 会短路整条优先级链（实测矩阵里出现过两次瞬时「不存在该股票」）
    async def fake_strict(code, priority=None):
        raise ResolveLayerError("searchapi down")

    monkeypatch.setattr(request_utils, "get_code_id_strict", fake_strict)
    result = asyncio.run(EastMoneyMarketData().valuation_series("QQQ", ValueKind.PE))
    assert is_market_error(result)
    assert result.code == "network"
    assert result.provider == "eastmoney"


def test_em_valuation_series_absent_symbol_stays_not_found(monkeypatch) -> None:
    # 标的不存在仍是 not_found（短路语义不变，勿把「查无此票」改成顺延）
    async def fake_strict(code, priority=None):
        return None

    monkeypatch.setattr(request_utils, "get_code_id_strict", fake_strict)
    result = asyncio.run(EastMoneyMarketData().valuation_series("QQQ", ValueKind.PE))
    assert is_market_error(result)
    assert result.code == "not_found"


def _patch_resolve_em(monkeypatch, module_path: str) -> None:
    from SayuStock.utils.market.adapters import sina as _s, tencent as _t

    mod = _t.provider if module_path == "tencent" else _s.provider

    async def fake_resolve(query):
        raise ResolveLayerError("searchapi down")

    monkeypatch.setattr(mod, "resolve_em_symbol", fake_resolve)


def test_tencent_symbol_of_layer_error_to_network(monkeypatch) -> None:
    _patch_resolve_em(monkeypatch, "tencent")
    result = asyncio.run(TencentMarketData()._symbol_of("QQQ"))
    assert is_market_error(result)
    assert result.code == "network"
    assert result.provider == "tencent"


def test_sina_symbol_of_layer_error_to_network(monkeypatch) -> None:
    _patch_resolve_em(monkeypatch, "sina")
    result = asyncio.run(SinaMarketData()._symbol_of("QQQ"))
    assert is_market_error(result)
    assert result.code == "network"
    assert result.provider == "sina"
