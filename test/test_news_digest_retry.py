"""雪球新闻推送：发送失败不推进水位线 / 不记已发送、合并转发禁用时退回纯文本。全部离线。"""

import asyncio
from typing import Any, Dict, List, Union

from SayuStock.stock_news import _DIGEST_BATCH, _send_digest, _digest_payload, _throttled_send
from SayuStock.utils.models import ItemType


class FakeSubscribe:
    """只保留 _send_digest / _throttled_send 真正读到的字段。"""

    def __init__(self, send_result: Union[int, None] = None) -> None:
        self.group_id = "10001"
        self.bot_id = "bot"
        self.extra_message = "0"
        self.send_result = send_result
        self.sent: List[Union[str, List[str]]] = []

    async def send(self, reply: Union[str, List[str]] = None) -> Union[int, None]:
        self.sent.append(reply)
        return self.send_result


def _item(news_id: int, created_at: int) -> ItemType:
    return ItemType(
        id=news_id,
        text=f"新闻{news_id}",
        mark=1,
        target="",
        created_at=created_at,
        view_count=0,
        status_id=0,
        reply_count=0,
        share_count=0,
        sub_type=0,
    )


def _patch(monkeypatch: Any) -> tuple:
    """掐掉 2-5s 节流与落库副作用，返回 (已发送记录, 水位线记录) 两个收集器。"""
    sent: List[tuple] = []
    watermark: List[int] = []

    async def _instant_sleep(_delay: float) -> None:
        return None

    async def _fake_watermark(_sub: object, value: int) -> None:
        watermark.append(value)

    monkeypatch.setattr("SayuStock.stock_news.asyncio.sleep", _instant_sleep)
    monkeypatch.setattr("SayuStock.stock_news.random.random", lambda: 0.0)
    monkeypatch.setattr("SayuStock.stock_news._mark_sent", lambda gid, nid: sent.append((gid, nid)))
    monkeypatch.setattr("SayuStock.stock_news._update_watermark", _fake_watermark)
    return sent, watermark


# ─────────────────────────── _throttled_send ───────────────────────────


def test_send_minus_one_reported_as_failure(monkeypatch: Any) -> None:
    """Subscribe.send 机器人离线时返回 -1，必须被当成失败而不是已送达"""
    _patch(monkeypatch)
    sub = FakeSubscribe(send_result=-1)
    assert asyncio.run(_throttled_send(sub, "hi")) is False

    sub2 = FakeSubscribe(send_result=None)
    assert asyncio.run(_throttled_send(sub2, "hi")) is True


# ─────────────────────────── _send_digest ───────────────────────────


def test_digest_send_failure_keeps_watermark(monkeypatch: Any) -> None:
    """整批发送失败：不记已发送、不推进水位线，下个窗口原样重发"""
    sent, watermark = _patch(monkeypatch)
    items = [_item(i, 1_700_000_000_000 + i * 60_000) for i in range(1, 4)]
    sub = FakeSubscribe(send_result=-1)

    asyncio.run(_send_digest(sub, items, "小时汇总"))  # type: ignore[arg-type]

    assert sent == []
    assert watermark == []


def test_digest_success_marks_and_advances(monkeypatch: Any) -> None:
    """全部成功：水位线推到最大 id，去重队列同步补齐"""
    sent, watermark = _patch(monkeypatch)
    items = [_item(i, 1_700_000_000_000 + i * 60_000) for i in range(1, 4)]
    sub = FakeSubscribe()

    asyncio.run(_send_digest(sub, items, "小时汇总"))  # type: ignore[arg-type]

    assert [nid for _, nid in sent] == [1, 2, 3]
    assert watermark == [3]
    # 首条批次带标题，其余不带
    assert isinstance(sub.sent[0], list)
    assert sub.sent[0][0].startswith("📰 雪球7x24 · 小时汇总")


def test_digest_partial_batch_failure_stops_at_failed_batch(monkeypatch: Any) -> None:
    """第二批失败：第一批照常记账，水位线停在第一批末尾，失败批次不记已发送"""
    sent, watermark = _patch(monkeypatch)
    total = _DIGEST_BATCH + 5
    items = [_item(i, 1_700_000_000_000 + i * 60_000) for i in range(1, total + 1)]

    sub = FakeSubscribe()
    calls = {"n": 0}
    real_send = sub.send

    async def _flaky_send(reply: Union[str, List[str]] = None) -> Union[int, None]:
        calls["n"] += 1
        if calls["n"] == 2:
            return -1
        return await real_send(reply)

    sub.send = _flaky_send  # type: ignore[method-assign]

    asyncio.run(_send_digest(sub, items, "每日汇总"))  # type: ignore[arg-type]

    assert [nid for _, nid in sent] == list(range(1, _DIGEST_BATCH + 1))
    assert watermark == [_DIGEST_BATCH]


def test_digest_skips_nothing_when_all_fail(monkeypatch: Any) -> None:
    """没有可发条目时不写水位线（避免无谓落库）"""
    _sent, watermark = _patch(monkeypatch)
    sub = FakeSubscribe()
    sub.extra_message = "99"
    items = [_item(i, 1_700_000_000_000 + i * 60_000) for i in range(1, 4)]

    asyncio.run(_send_digest(sub, items, "每日汇总"))  # type: ignore[arg-type]

    assert watermark == []


# ─────────────────────────── _digest_payload ───────────────────────────


def _force_forward(monkeypatch: Any, mode: str) -> None:
    class _Cfg:
        def __init__(self, data: str) -> None:
            self.data = data

    class _Sp:
        @staticmethod
        def get_config(_key: str) -> _Cfg:
            return _Cfg(mode)

    monkeypatch.setattr("SayuStock.stock_news.sp_config", _Sp)


def test_digest_payload_plain_text_when_forward_disabled(monkeypatch: Any) -> None:
    """核心禁止合并转发时 node 会被整条丢弃，必须退回纯文本"""
    _force_forward(monkeypatch, "禁止(不发送任何消息)")
    out = _digest_payload(["标题", "第一条", "第二条"])
    assert isinstance(out, str)
    assert out == "标题\n第一条\n第二条"


def test_digest_payload_keeps_list_when_forward_allowed(monkeypatch: Any) -> None:
    _force_forward(monkeypatch, "允许")
    out: Union[str, List[str]] = _digest_payload(["第一条"])
    assert isinstance(out, list)
    assert out == ["第一条"]


# ─────────────────────────── get_news 覆盖窗口 ───────────────────────────


def test_get_news_light_path_paginates_three_pages(monkeypatch: Any) -> None:
    """cover_ms 缺省（逐条实时）仍只翻 3 页，不给 5 分钟轮询加压"""
    from SayuStock.utils import request as req

    pages = {"n": 0}

    async def _fake_list(max_id: int = 0) -> Dict[str, Any]:
        pages["n"] += 1
        return {
            "next_max_id": max_id - 1,
            "next_id": max_id,
            "items": [_item(100 - pages["n"], 1_700_000_000_000 - pages["n"] * 1000)],
        }

    monkeypatch.setattr(req, "get_news_list", _fake_list)
    monkeypatch.setattr(req, "NEWS", {"next_max_id": 0, "items": [], "next_id": 0})

    asyncio.run(req.get_news())
    assert pages["n"] == 3


def test_get_news_cover_ms_backfills_until_window_covered(monkeypatch: Any) -> None:
    """空缓存 + cover_ms：翻到覆盖窗口为止，而不是卡在 45 条"""
    import time as _time

    from SayuStock.utils import request as req

    now_ms = int(_time.time() * 1000)
    pages = {"n": 0}
    # 每页 1 条，每条往前 6h；48h 窗口需要翻到第 9 页才满足
    step_ms = 6 * 60 * 60 * 1000

    async def _fake_list(max_id: int = 0) -> Dict[str, Any]:
        pages["n"] += 1
        return {
            "next_max_id": max_id - 1,
            "next_id": max_id,
            "items": [_item(1000 - pages["n"], now_ms - pages["n"] * step_ms)],
        }

    monkeypatch.setattr(req, "get_news_list", _fake_list)
    monkeypatch.setattr(req, "NEWS", {"next_max_id": 0, "items": [], "next_id": 0})

    result = asyncio.run(req.get_news(cover_ms=req.NEWS_RETENTION_MS))
    assert isinstance(result, tuple)
    # 翻到最旧条目早于 48h 才停
    assert pages["n"] >= 8
    assert pages["n"] <= req._NEWS_MAX_PAGES
