"""市场后缀解析与标题展示名。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from SayuStock.utils.constant import PREFIX_DATA
from SayuStock.utils.time_range import Market, _parse_em_code
from SayuStock.utils.market.enums import AssetClass
from SayuStock.utils.market.models import SymbolRef
from SayuStock.utils.stock.request_utils import get_code_id


def test_display_name_with_sec_type() -> None:
    s = SymbolRef("005930", "三星电子", AssetClass.EQUITY, "KRX", "177.005930", sec_type="韩股")
    assert s.display_name == "三星电子 (韩股)"


def test_display_name_without_sec_type() -> None:
    s = SymbolRef("600519", "贵州茅台", AssetClass.EQUITY, "SSE", "1.600519")
    assert s.display_name == "贵州茅台"


def test_display_name_avoids_duplicate_suffix() -> None:
    s = SymbolRef(
        "005930",
        "三星电子 (韩股)",
        AssetClass.EQUITY,
        "KRX",
        "177.005930",
        sec_type="韩股",
    )
    assert s.display_name == "三星电子 (韩股)"


def test_prefix_177_is_korean() -> None:
    assert PREFIX_DATA["177"] == "韩股"


def test_dotted_secid_returns_korean_sec_type() -> None:
    result = asyncio.run(get_code_id("177.005930"))
    assert result is not None
    secid, name, sec_type = result
    assert secid == "177.005930"
    assert sec_type == "韩股"


def test_dotted_secid_refines_chinext() -> None:
    result = asyncio.run(get_code_id("0.300750"))
    assert result is not None
    assert result[2] == "创业板"


def test_dotted_secid_refines_star_board() -> None:
    result = asyncio.run(get_code_id("1.688981"))
    assert result is not None
    assert result[2] == "科创板"


def test_kr_suffix_sets_priority_and_matches() -> None:
    """`.kr` 应剥后缀并以韩股 SecurityTypeName 优先匹配。"""
    mock_payload: dict[str, Any] = {
        "QuotationCodeTable": {
            "Data": [
                {
                    "QuoteID": "153.SSNGY",
                    "Name": "三星电子(GDR)",
                    "SecurityTypeName": "粉单",
                },
                {
                    "QuoteID": "177.005930",
                    "Name": "三星电子",
                    "SecurityTypeName": "韩股",
                },
            ]
        }
    }

    class _Resp:
        status = 200

        async def text(self) -> str:
            import json

            return json.dumps(mock_payload)

        async def __aenter__(self) -> "_Resp":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class _Sess:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Sess":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def get(self, *args: object, **kwargs: object) -> _Resp:
            # 确认搜索关键字已去掉 .kr
            params = kwargs.get("params") or args[1] if len(args) > 1 else None
            if params is not None:
                # params 为 tuple of pairs
                as_dict = dict(params)
                assert as_dict.get("input") == "三星电子"
            return _Resp()

    with patch("SayuStock.utils.stock.request_utils.ClientSession", _Sess):
        result = asyncio.run(get_code_id("三星电子.kr"))
    assert result is not None
    assert result[0] == "177.005930"
    assert result[1] == "三星电子"
    assert result[2] == "韩股"


def test_parse_em_code_korean_stock() -> None:
    assert _parse_em_code("177.005930") == Market.KR_STOCK
