"""空盘口行 ≠ 标的不存在：必须报 empty 让注册表顺延，不能 not_found 短路整链。

回归背景：腾讯/新浪在「符号映射成功、返回行却是空占位」时也报过 not_found。
not_found 是**短路**语义（解析层共用，换源无意义），于是东财失败后，
腾讯一行空占位就把新浪和同花顺一起挡在门外——容灾在最该生效的时候失效。
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import SymbolRef
from SayuStock.utils.market.adapters.sina import provider as sina_provider
from SayuStock.utils.market.adapters.tencent import provider as tencent_provider
from SayuStock.utils.market.adapters.sina.provider import SinaMarketData
from SayuStock.utils.market.adapters.tencent.provider import TencentMarketData


def _ref(query: str) -> SymbolRef:
    return SymbolRef(
        code=query,
        name="贵州茅台",
        asset_class=AssetClass.EQUITY,
        exchange="SSE",
        provider_symbol="1.600519",
    )


async def _fake_ref(query: str) -> SymbolRef:
    return _ref(query)


async def _empty_lines(symbols: object) -> dict[str, str]:
    """.get(sym) 命中一行空串：符号对得上，只是这次没数据。"""
    return {str(sym): "" for sym in symbols}  # type: ignore[union-attr]


def test_tencent_empty_line_reports_empty_not_not_found() -> None:
    async def _run() -> object:
        with (
            patch.object(tencent_provider, "resolve_em_symbol", _fake_ref),
            patch.object(tencent_provider, "fetch_qt_lines", _empty_lines),
        ):
            return await TencentMarketData().quote("600519")

    res = asyncio.run(_run())  # type: ignore[arg-type]
    assert is_market_error(res)
    assert res.code == "empty", "空行报 not_found 会短路整条源链"
    assert res.provider == "tencent"


def test_sina_empty_line_reports_empty_not_not_found() -> None:
    async def _run() -> object:
        with (
            patch.object(sina_provider, "resolve_em_symbol", _fake_ref),
            patch.object(sina_provider, "fetch_hq_lines", _empty_lines),
        ):
            return await SinaMarketData().quote("600519")

    res = asyncio.run(_run())  # type: ignore[arg-type]
    assert is_market_error(res)
    assert res.code == "empty", "空行报 not_found 会短路整条源链"
    assert res.provider == "sina"
