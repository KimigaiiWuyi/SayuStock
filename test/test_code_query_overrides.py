"""手动重定向兜底：scm / 原油主连 必须解析到 142.scm（原油主连）。

东财 suggest 按热度排序，实测 input=scm 时美股 SCM 排第一、input=原油主连 时
布油 B00Y 排第一；这两个 payload 都取自真实接口，用于复现并锁死该优先级问题。
"""

from __future__ import annotations

import json
import asyncio
from typing import Any

import pytest

from SayuStock.utils.stock import request_utils as ru

_SCM_PAYLOAD: dict[str, Any] = {
    "QuotationCodeTable": {
        "Data": [
            {"QuoteID": "106.SCM", "Name": "Stellus Capital Investment Corp", "SecurityTypeName": "美股"},
            {"QuoteID": "142.scm", "Name": "原油主连", "SecurityTypeName": "期货"},
        ]
    }
}

_OIL_PAYLOAD: dict[str, Any] = {
    "QuotationCodeTable": {
        "Data": [
            {"QuoteID": "112.B00Y", "Name": "布伦特原油当月连续", "SecurityTypeName": "期货"},
            {"QuoteID": "142.scm", "Name": "原油主连", "SecurityTypeName": "期货"},
        ]
    }
}

_MAOTAI_PAYLOAD: dict[str, Any] = {
    "QuotationCodeTable": {"Data": [{"QuoteID": "1.600519", "Name": "贵州茅台", "SecurityTypeName": "沪A"}]}
}

_PAYLOADS: dict[str, dict[str, Any]] = {
    "scm": _SCM_PAYLOAD,
    "原油主连": _OIL_PAYLOAD,
    "贵州茅台": _MAOTAI_PAYLOAD,
    "SCM": _SCM_PAYLOAD,
}


def _patch_session(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """用真实 suggest payload 替换 ClientSession，并记录每次联网的 input。"""

    class _Resp:
        status = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        async def text(self) -> str:
            return json.dumps(self._payload)

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

        def get(self, url: str, params: Any = None) -> _Resp:
            key = str(dict(params or []).get("input"))
            calls.append(key)
            return _Resp(_PAYLOADS.get(key, {"QuotationCodeTable": {"Data": []}}))

    monkeypatch.setattr(ru, "ClientSession", _Sess)


def test_scm_redirects_to_crude_oil_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_session(monkeypatch, calls)

    result = asyncio.run(ru.get_code_id("scm"))

    assert result == ("142.scm", "原油主连", "期货")
    assert calls == []


def test_oil_main_contract_name_redirects_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_session(monkeypatch, calls)

    result = asyncio.run(ru.get_code_id("原油主连"))

    assert result == ("142.scm", "原油主连", "期货")
    assert calls == []


def test_override_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_session(monkeypatch, calls)

    result = asyncio.run(ru.get_code_id("SCM"))

    assert result == ("142.scm", "原油主连", "期货")
    assert calls == []


def test_suggest_would_misroute_scm_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """移除重定向后应复现东财把 scm 给到美股，证明 payload 真实有效。"""
    calls: list[str] = []
    _patch_session(monkeypatch, calls)
    monkeypatch.delitem(ru.code_query_overrides, "scm")

    result = asyncio.run(ru.get_code_id("scm"))

    assert result == ("106.SCM", "Stellus Capital Investment Corp", "美股")
    assert calls == ["scm"]


def test_suggest_would_misroute_oil_name_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """移除重定向后应复现东财把原油主连给到布油 B00Y。"""
    calls: list[str] = []
    _patch_session(monkeypatch, calls)
    monkeypatch.delitem(ru.code_query_overrides, "原油主连")

    result = asyncio.run(ru.get_code_id("原油主连"))

    assert result == ("112.B00Y", "布伦特原油当月连续", "期货")
    assert calls == ["原油主连"]


def test_non_override_query_still_uses_suggest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_session(monkeypatch, calls)

    result = asyncio.run(ru.get_code_id("贵州茅台"))

    assert result == ("1.600519", "贵州茅台", "沪A")
    assert calls == ["贵州茅台"]


def test_market_suffix_bypasses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.us` 显式指定美股时不应被重定向吞掉。"""
    calls: list[str] = []
    _patch_session(monkeypatch, calls)

    result = asyncio.run(ru.get_code_id("SCM.us"))

    assert result == ("106.SCM", "Stellus Capital Investment Corp", "美股")
    assert calls == ["SCM"]
