"""「有图必有文字」回归测试。

部分模型看不到图，``ai_return`` 的文字是它拿到的**全部**信息。渲染入口会缓存图片
（``mapcloud_refresh_minutes`` 内直接 return 文件），历史上这个早退把 ``_ai_return_*``
整个绕过去了 —— 同一命令问第二次，AI 就一个字都收不到。

这里驱动**真实的** ``render_image_file``（mock 掉网络），断言冷/热缓存两次都发文字。
"""

import sys
import asyncio
from typing import Any
from pathlib import Path
from collections.abc import Iterator

import pytest
from kline_fixtures import make_klines

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from SayuStock.stock_stockinfo import render_mpl as rm  # noqa: E402
from SayuStock.utils.stock.utils import get_file  # noqa: E402
from SayuStock.stock_stockinfo.data import CloudMapDataResult  # noqa: E402


def _klines(n: int = 120, seed: int = 5) -> list[str]:
    return make_klines(n, seed)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """替换 ai_return，收集发给 AI 的文字。"""
    got: list[str] = []
    monkeypatch.setattr(rm, "ai_return", lambda t: got.append(t))
    return got


@pytest.fixture
def fake_kline_fetch(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """mock 掉取数，返回固定的日 K。

    本测试必须走真实的图片缓存路径（要验的就是缓存命中时会不会丢文字），而缓存写在
    ``DATA_PATH`` —— 也就是用户放真实行情缓存的目录。所以前后都要清掉产物，
    别在人家数据目录里留下测试图。
    """
    name = "AI文字投递测试股"
    raw: dict[str, Any] = {"data": {"name": name, "code": "600000", "klines": _klines()}}

    async def fetch(market, sector, start_time=None, end_time=None):  # noqa: ANN001, ARG001
        return CloudMapDataResult(raw, [], "single-stock-kline-101", None)

    monkeypatch.setattr(rm.CLOUDMAP_DATA_SERVICE, "fetch", fetch)

    cache = get_file(name, "png", "single-stock-kline-101", None)
    cache.unlink(missing_ok=True)
    yield name
    cache.unlink(missing_ok=True)


def test_ai_text_sent_on_cold_and_warm_cache(captured: list[str], fake_kline_fetch: str) -> None:
    """冷缓存画图、热缓存直接返回文件 —— 两种情况都必须发文字给 AI。"""
    name = fake_kline_fetch

    captured.clear()
    first = asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    assert not isinstance(first, str), f"第一次出图失败: {first}"
    assert captured, "冷缓存时 AI 没收到文字"
    cold_text = captured[0]

    captured.clear()
    second = asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    assert not isinstance(second, str), f"第二次出图失败: {second}"
    assert captured, "热缓存命中时 AI 没收到文字 —— 看不到图的模型将完全无输入"
    assert captured[0] == cold_text, "冷/热缓存两次发给 AI 的文字必须一致"


def test_ai_text_on_cache_hit_still_has_indicators(captured: list[str], fake_kline_fetch: str) -> None:
    """热缓存那次的文字同样要含全部指标，不能是个缩水版。"""
    name = fake_kline_fetch
    asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))  # 预热
    captured.clear()
    asyncio.run(rm.render_image_file(name, "single-stock-kline-101"))
    assert captured
    text = captured[0]
    for label in ("MA20", "BBI", "KDJ(9,3,3)", "RSI6", "MACD(12,26,9)", "BOLL(20,2)"):
        assert label in text, f"热缓存的文字缺 {label}"


def test_emit_ai_text_dispatch_matches_chart_kinds(captured: list[str]) -> None:
    """_emit_ai_text 的分支必须与绘图分发一致：每种图都要有对应文字。"""
    kline = {"data": {"name": "甲", "klines": _klines()}}

    captured.clear()
    rm._emit_ai_text("甲", "single-stock-kline-101", kline, [])
    assert captured and "日K" in captured[0]

    captured.clear()
    rm._emit_ai_text("甲", "compare-stock", kline, [kline, kline])
    assert captured and "个股对比" in captured[0]

    captured.clear()
    rm._emit_ai_text("甲", "single-stock", {"data": {"f58": "甲", "f43": 10}}, [])
    assert captured and "分时" in captured[0]

    captured.clear()
    cloud = {"data": {"diff": [{"f3": 1.0, "f14": "股A", "f100": "板块"}]}}
    rm._emit_ai_text("大盘云图", None, cloud, [])
    assert captured and "大盘云图" in captured[0]


# ============================================================
# 模型预测（Kronos）—— 出图函数挂 @async_file_cache(minutes=150)，
# 命中缓存时装饰器直接返回文件、函数体根本不执行。发文字必须留在缓存之外。
# ============================================================
def test_kronos_ai_text_sent_on_cold_and_warm_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import plotly.graph_objects as go

    from SayuStock.stock_ai import draw_ai_map as dm

    raw: dict[str, Any] = {"data": {"name": "预测缓存测试股", "code": "600000", "klines": _kronos_klines()}}

    async def fake_get_gg(sec_id, sector, *a, **k):  # noqa: ANN001, ARG001
        return raw

    async def fake_code_id(market):  # noqa: ANN001, ARG001
        return ("1.600000", "预测缓存测试股")

    async def fake_pw(fig, w, h, s):  # noqa: ANN001, ARG001
        return b"PNG"

    monkeypatch.setattr(dm, "get_gg", fake_get_gg)
    monkeypatch.setattr(dm, "get_code_id", fake_code_id)
    monkeypatch.setattr(dm, "get_full_security_code", lambda c: "1.600000")
    # 不跑真模型（要 3 分钟 + 权重），也不截图（要 playwright 浏览器）
    monkeypatch.setattr(dm, "gdf", lambda df, raw_data: go.Figure(data=[go.Scatter(y=[1, 2, 3])]))
    monkeypatch.setattr(dm, "render_image_by_pw", fake_pw)

    got: list[str] = []
    monkeypatch.setattr(dm, "ai_return", lambda t: got.append(t))

    # 缓存写在 DATA_PATH（用户放真实缓存的目录），前后都清掉，别留测试产物
    cache = get_file("1.600000", "html", "single-stock-ai", None)
    cache.unlink(missing_ok=True)

    class FakeBot:
        async def send(self, *a, **k):  # noqa: ANN001, ARG002
            return None

    try:
        got.clear()
        asyncio.run(dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot()))  # type: ignore[arg-type]
        assert got, "冷缓存时 AI 没收到文字"
        cold = got[0]

        got.clear()
        asyncio.run(dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot()))  # type: ignore[arg-type]
        assert got, "@async_file_cache 命中时 AI 没收到文字 —— 150 分钟内问第二次将完全无输入"
        assert got[0] == cold
    finally:
        cache.unlink(missing_ok=True)


def _kronos_klines(n: int = 200, seed: int = 4) -> list[str]:
    """Kronos 走 30 分钟 K，首列带 HH:MM。"""
    return make_klines(n, seed, minute=True)
