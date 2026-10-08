"""东财 stock_request：单个域名超时后必须改走备用域名。

背景（评审第 2 轮实测）：``ClientTimeout(total=)`` 抛的是**裸 asyncio.TimeoutError**
（不是 ServerTimeoutError，因而也不属于 ClientConnectionError）。原先只捕
ConnectionError 两个分支，超时会直接冲出 stock_request：备用域名一次都轮不到，
全 A 翻页时一页超时就让整个任务失败（``update_stocks.py`` 全量重跑实测死在这里）。
"""

from __future__ import annotations

import asyncio

import pytest

from SayuStock.utils import eastmoney as em


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self.status = 200
        self._payload = payload

    async def json(self, content_type: str | None = None) -> object:
        return self._payload

    async def text(self) -> str:
        return ""


class _FakeRequestCM:
    """形状对齐 aiohttp 的 _RequestContextManager：只用作 async with 的上下文。"""

    def __init__(self, *, resp: _FakeResp | None = None, exc: BaseException | None = None) -> None:
        self._resp = resp
        self._exc = exc

    async def __aenter__(self) -> _FakeResp | None:
        if self._exc is not None:
            raise self._exc
        return self._resp

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeSession:
    """只记录被请求的域名，按域名决定超时还是成功。"""

    attempts: list[str] = []
    timeout_hosts: set[str] = set()

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def request(self, method: str, *, url: str, **kwargs: object) -> _FakeRequestCM:
        _FakeSession.attempts.append(url)
        host = url.split("/")[2]
        if host in _FakeSession.timeout_hosts:
            return _FakeRequestCM(exc=asyncio.TimeoutError())
        return _FakeRequestCM(resp=_FakeResp({"rc": 0, "data": {"hit": host}}))


def _prepare(monkeypatch: pytest.MonkeyPatch, timeout_hosts: set[str]) -> em.EastMoneyRequester:
    _FakeSession.attempts = []
    _FakeSession.timeout_hosts = timeout_hosts
    monkeypatch.setattr(em, "ClientSession", _FakeSession)
    monkeypatch.setattr(em.STOCK_CONFIG, "get_config", lambda key: type("_C", (), {"data": None})())
    return em.EastMoneyRequester()


def test_timeout_falls_through_to_backup_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """push2 超时后必须再打一次 push2delay，而不是把 TimeoutError 抛出去。"""

    async def _run() -> None:
        requester = _prepare(monkeypatch, {"push2.eastmoney.com"})
        resp = await requester.stock_request("https://push2.eastmoney.com/api/qt/stock/get")

        assert isinstance(resp, dict), resp
        assert resp["data"]["hit"] == "push2delay.eastmoney.com"
        hosts = [u.split("/")[2] for u in _FakeSession.attempts]
        assert hosts == ["push2.eastmoney.com", "push2delay.eastmoney.com"], hosts
        # 记住失败域名，后续请求优先走备用
        assert requester.preferred_push_domain == "push2delay"

    asyncio.run(_run())


def test_both_domains_timing_out_returns_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个域名都超时要回错误码，不能让 TimeoutError 冒到调用方。"""

    async def _run() -> None:
        requester = _prepare(monkeypatch, {"push2.eastmoney.com", "push2delay.eastmoney.com"})
        resp = await requester.stock_request("https://push2.eastmoney.com/api/qt/stock/get")

        assert resp == -400016
        hosts = [u.split("/")[2] for u in _FakeSession.attempts]
        assert hosts == ["push2.eastmoney.com", "push2delay.eastmoney.com"], hosts

    asyncio.run(_run())


def test_non_push2_url_has_no_backup_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 push2 地址没有备用域名：超时只打一次，回错误码。"""

    async def _run() -> None:
        requester = _prepare(monkeypatch, {"push2his.eastmoney.com"})
        resp = await requester.stock_request("https://push2his.eastmoney.com/api/qt/stock/kline/get")

        assert resp == -400016
        assert len(_FakeSession.attempts) == 1

    asyncio.run(_run())
