"""PE/PB/DY and Kronos galleries and their command-boundary checks."""

import os
import sys
import struct
from math import sin
from base64 import b64decode
from pathlib import Path
from datetime import date, datetime, timedelta

import pytest
from test_takumi_render import _render_sample
from test_ai_text_delivery import _kline_series
from test_render_chart_gallery import _fixture_png

from SayuStock.stock_ai import draw_ai_map as dm
from SayuStock.stock_sina import eastmoney_value
from SayuStock.utils.stock import render_cache
from SayuStock.render.valuation import prepare_valuation
from SayuStock.render.chart_page import page_html, render_chart
from SayuStock.utils.market.enums import ValueKind, AssetClass
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import SymbolRef, KlineSeries, ValueSeries
from SayuStock.utils.market.models.value import ValuePoint, DividendEvent
from SayuStock.utils.market.adapters.eastmoney.parse_value import parse_value_series_payload


def _valuation(kind: ValueKind) -> list[ValueSeries]:
    begin = date(2025, 11, 3)
    series: list[ValueSeries] = []
    for i, (code, name) in enumerate((("600101", "远山科技"), ("600102", "星海微电子"))):
        symbol = SymbolRef(code, name, AssetClass.EQUITY, "SSE", f"1.{code}")
        points: list[ValuePoint] = []
        for j in range(192):
            day = begin + timedelta(days=j)
            if day.weekday() >= 5:
                continue
            if kind == ValueKind.DY and 52 - i * 18 < j < 106 + i * 12:
                continue
            events: tuple[DividendEvent, ...] = ()
            if kind == ValueKind.DY and j in {22, 141}:
                events = (DividendEvent(day, date(2025, 6, 30), 0.32 - i * 0.1, 1.26 + 0.36 * i, False),)
            base = 26 if kind == ValueKind.PE else 3.8 if kind == ValueKind.PB else 2.8
            price = base * (1 + 0.25 * sin(j / 38 + i) + 0.04 * sin(j / 4 + i) - 0.15 * i)
            points.append(ValuePoint(day, price, events))
        series.append(ValueSeries(symbol, kind, tuple(points), "eastmoney"))
    return series


def test_dy_adapter_carries_real_ex_dates_and_chart_leaves_blank_intervals() -> None:
    event = {"ex_date": "2026-01-05", "report_date": "2025-06-30", "bonus_per_share": 0.32, "contribution_pct": 1.26}
    data = parse_value_series_payload(
        {
            "code": "600101",
            "name": "远山科技",
            "rows": [
                {"date": "2026-01-05", "value": 2.8, "events": [event]},
                {"date": "2026-01-06", "value": 2.7, "events": [event]},
                {"date": "2026-01-07", "value": None},
                {"date": "2026-01-08", "value": 2.6},
                {
                    "date": "2026-04-01",
                    "value": 3.1,
                    "events": [{**event, "ex_date": "2026-04-01", "is_planned": True}],
                },
            ],
        },
        kind=ValueKind.DY,
    )
    assert not is_market_error(data)
    assert data.points[0].events[0].contribution_pct == 1.26
    assert len(data.points) == 4
    assert data.missing_days == (date(2026, 1, 7),)
    page = prepare_valuation([data], ValueKind.DY, "股息率", failures=["测试缺源股"])
    assert not isinstance(page, str)
    html = page_html(page)
    assert "每股 0.32元 · 贡献 +1.26%" in html
    assert html.count("除权") == 1
    assert "测试缺源股" in html
    assert page.panels[0].series[0].path.count("M") == 3


@pytest.mark.asyncio
async def test_kronos_cold_warm_and_config_switch_preserve_ai_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    series = _kline_series("预测缓存测试股", n=80, seed=7)

    class FakeMarket:
        async def kline(self, *args: object) -> KlineSeries:
            return series

    class FakeBot:
        async def send(self, *args: object) -> None:
            pass

    async def code_id(market: str) -> tuple[str, str, str]:
        return ("1.600000", market, "股票")

    draws: list[dm.KronosRunConfig] = []
    got: list[str] = []
    config = dm.KronosRunConfig("tokenizer/a", "model/a", "cpu")

    def gdf(df: object, bars: object, conf: dm.KronosRunConfig) -> object:
        draws.append(conf)
        return _render_sample()

    async def render(page: object) -> bytes:
        return _fixture_png()

    monkeypatch.setattr(render_cache, "DATA_PATH", tmp_path)
    monkeypatch.setattr(dm, "get_market", FakeMarket)
    monkeypatch.setattr(dm, "get_code_id", code_id)
    monkeypatch.setattr(dm, "get_full_security_code", lambda code: code)
    monkeypatch.setattr(dm, "kronos_run_config", lambda: config)
    monkeypatch.setattr(dm, "gdf", gdf)
    monkeypatch.setattr(dm, "render_chart", render)
    monkeypatch.setattr(dm, "ai_return", got.append)
    for _ in range(2):
        result = await dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot())
        assert isinstance(result, str) and result.startswith("base64://")
        assert b64decode(result.removeprefix("base64://")) == _fixture_png()
    assert len(got) == 2 and got[0] == got[1] and "30分钟K线" in got[0]
    assert len(draws) == 1
    config = dm.KronosRunConfig("tokenizer/a", "model/b", "cpu")
    result = await dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot())
    assert isinstance(result, str) and result.startswith("base64://")
    config = dm.KronosRunConfig("tokenizer/a", "model/b", "cuda:0")
    result = await dm.draw_ai_kline_with_forecast("预测缓存测试股", FakeBot())
    assert isinstance(result, str) and result.startswith("base64://")
    assert len(draws) == 3 and len(list(tmp_path.glob("*.png"))) == 3
    assert not dm.NOW_QUEUE


def test_kronos_missing_torch_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    assert dm._resolve_kronos_device("cuda:0") == "cpu"
    series = _kline_series("预测缓存测试股", n=80)
    frame = dm.fill_kline_by_kronos(series)
    assert frame is not None
    result = dm.gdf(frame, series)
    assert isinstance(result, str) and "安装" in result and "torch" in result


@pytest.mark.asyncio
async def test_valuation_command_preserves_date_filter_failure_and_jpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    stock_list = [
        {"name": "远山科技", "code": "600101", "secid": "1.600101", "sec_type": "股票"},
        {"name": "测试缺源股", "code": "600102", "secid": "1.600102", "sec_type": "股票"},
    ]
    got: list[str] = []

    async def parse(query: str) -> list[dict[str, str]]:
        return stock_list

    async def fetch(stock: dict[str, str], kind: str) -> ValueSeries | None:
        return _valuation(ValueKind.PE)[0] if stock["code"] == "600101" else None

    async def render(series: list[ValueSeries], kind: str, *, failures: list[str]) -> bytes:
        assert kind == "pe" and failures == ["测试缺源股(600102)"]
        assert len(series) == 1
        assert [point.day for point in series[0].points] == [date(2025, 11, day) for day in (5, 6, 7)]
        assert "测试缺源股(600102)" in got[0]
        return _fixture_png()

    monkeypatch.setattr(eastmoney_value, "_parse_stock_input", parse)
    monkeypatch.setattr(eastmoney_value, "fetch_eastmoney_value_series", fetch)
    monkeypatch.setattr(eastmoney_value, "draw_value_compare_chart", render)
    monkeypatch.setattr(eastmoney_value, "ai_return", got.append)
    result = await eastmoney_value.get_eastmoney_pepb_compare(
        "估值测试", "pe", datetime(2025, 11, 5), datetime(2025, 11, 7)
    )
    assert isinstance(result, bytes) and result[:2] == b"\xff\xd8"


def _save_png(name: str, result: str | bytes) -> None:
    assert isinstance(result, bytes), result
    assert result.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", result[16:24])
    assert width >= 1200 and height >= 600
    if "SAYUSTOCK_RENDER_EVIDENCE_DIR" in os.environ:
        target = Path(os.environ["SAYUSTOCK_RENDER_EVIDENCE_DIR"]) / "valuation"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.png").write_bytes(result)


@pytest.mark.asyncio
@pytest.mark.usefixtures("require_render_runtime")
async def test_valuation_and_forecast_gallery_uses_core_renderer() -> None:
    for kind in ValueKind:
        _save_png(
            kind.value,
            await eastmoney_value.draw_value_compare_chart(
                _valuation(kind),
                kind.value,
                failures=["测试缺源股（暂无估值）"] if kind == ValueKind.DY else [],
            ),
        )
    for name, page in (
        (
            "kronos",
            _render_sample(tokenizer="NeoQuasar/Kronos-Tokenizer-base", model="NeoQuasar/Kronos-mini", device="cpu"),
        ),
        ("kronos-no-model-note", _render_sample()),
    ):
        _save_png(name, await render_chart(page))
