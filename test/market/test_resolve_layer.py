"""解析层（东财 searchapi）瞬断 ≠ 标的不存在：strict 契约与适配器转换。

背景：解析层失败若误报 not_found 会短路整条行情优先级链（换源无意义），
用户对真实存在的股票看到「不存在该股票」；必须转 network 错误顺延。
"""

from __future__ import annotations

import asyncio

import pytest

from SayuStock.utils.stock import request_utils
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.stock.request_utils import (
    ResolveLayerError,
    get_code_id,
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


def test_em_quote_layer_error_returns_network(monkeypatch) -> None:
    async def fake_strict(code, priority=None):
        raise ResolveLayerError("searchapi down")

    monkeypatch.setattr(request_utils, "get_code_id_strict", fake_strict)
    result = asyncio.run(EastMoneyMarketData().quote("QQQ"))
    assert is_market_error(result)
    assert result.code == "network"
    assert result.provider == "eastmoney"


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
