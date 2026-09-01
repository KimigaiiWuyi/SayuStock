"""天天基金搜索 / 净值解析，以及场外基金路由粗判。"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from datetime import date

from SayuStock.utils.market.enums import AssetClass, KlinePeriod
from SayuStock.utils.market.errors import MarketError, is_market_error
from SayuStock.utils.market.models import Bar, Quote, SymbolRef, KlineSeries
from SayuStock.utils.market.fund_route import (
    extract_fund_code,
    is_otc_fund_series,
    maybe_otc_fund_query,
)
from SayuStock.utils.market.adapters._base import PartialMarketData
from SayuStock.utils.market.convert.dataframe import kline_to_cn_df
from SayuStock.utils.market.adapters.tiantian.parse import (
    parse_nav_rows,
    make_fund_symbol,
    quote_from_nav_rows,
    parse_search_payload,
)

FIX = Path(__file__).parent / "fixtures"


def test_maybe_otc_fund_query() -> None:
    assert maybe_otc_fund_query("720001")
    assert maybe_otc_fund_query("150.720001")
    assert maybe_otc_fund_query("财通价值动量混合A")
    assert maybe_otc_fund_query("110022")
    assert not maybe_otc_fund_query("510300")
    assert not maybe_otc_fund_query("600519")
    assert not maybe_otc_fund_query("000001")
    assert not maybe_otc_fund_query("159915")
    assert not maybe_otc_fund_query("茅台")
    assert not maybe_otc_fund_query("沪深300")
    assert not maybe_otc_fund_query("证券ETF")


def test_extract_fund_code() -> None:
    assert extract_fund_code("720001") == "720001"
    assert extract_fund_code("150.720001") == "720001"
    assert extract_fund_code("财通价值动量混合A") is None


def test_parse_search_payload() -> None:
    payload = json.loads((FIX / "tiantian_search_720001.json").read_text(encoding="utf-8"))
    hit = parse_search_payload(payload, "720001")
    assert hit is not None
    assert hit.code == "720001"
    assert hit.name == "财通价值动量混合A"
    assert "混合" in hit.fund_type
    assert hit.unit_nav == 14.081
    folded = parse_search_payload(payload, "财通价值动量混合a")
    assert folded is not None
    assert folded.code == "720001"


def test_parse_nav_rows_to_kline() -> None:
    payload = json.loads((FIX / "tiantian_nav_720001.json").read_text(encoding="utf-8"))
    symbol = make_fund_symbol("720001", "财通价值动量混合A", "混合型-灵活")
    series = parse_nav_rows(
        payload["Datas"],
        symbol=symbol,
        period=KlinePeriod.D1_YEAR,
        start=date(2026, 8, 26),
        end=date(2026, 9, 1),
    )
    assert not is_market_error(series)
    assert series.symbol.asset_class == AssetClass.FUND
    assert series.symbol.provider_symbol == "150.720001"
    assert is_otc_fund_series(series)
    assert len(series.bars) == 5
    assert series.bars[0].ts.date() == date(2026, 8, 26)
    assert series.bars[-1].ts.date() == date(2026, 9, 1)
    assert series.bars[-1].close == 14.552
    assert series.bars[-1].open == series.bars[-1].close
    assert series.bars[-1].change_pct is not None
    assert abs(series.bars[-1].change_pct - ((14.552 - 15.053) / 15.053 * 100.0)) < 1e-9
    df = kline_to_cn_df(series)
    assert len(df) == 5
    assert float(df["收盘"].iloc[-1]) == 14.552
    assert "归一化" in df.columns


def test_nav_date_filter() -> None:
    payload = json.loads((FIX / "tiantian_nav_720001.json").read_text(encoding="utf-8"))
    symbol = make_fund_symbol("720001", "财通价值动量混合A")
    series = parse_nav_rows(
        payload["Datas"],
        symbol=symbol,
        period=KlinePeriod.D1_YEAR,
        start=date(2026, 8, 31),
        end=date(2026, 9, 1),
    )
    assert not is_market_error(series)
    assert [b.ts.date() for b in series.bars] == [date(2026, 8, 31), date(2026, 9, 1)]
    assert series.bars[-1].change_amount is not None
    assert abs(series.bars[-1].change_amount - (14.552 - 15.053)) < 1e-9


def test_quote_uses_unit_nav_not_acc_nav() -> None:
    payload = json.loads((FIX / "tiantian_nav_720001.json").read_text(encoding="utf-8"))
    symbol = make_fund_symbol("720001", "财通价值动量混合A", "混合型-灵活")
    hit = parse_search_payload(
        json.loads((FIX / "tiantian_search_720001.json").read_text(encoding="utf-8")),
        "720001",
    )
    assert hit is not None
    q = quote_from_nav_rows(symbol, hit, payload["Datas"])
    assert q.price == 14.081
    assert q.change_pct == -3.44
    assert q.prev_close is not None
    assert abs(q.prev_close - 14.582) < 1e-9
    assert q.change_amount is not None
    assert abs(q.change_amount - (14.081 - 14.582)) < 1e-9


def test_compare_render_accepts_fund_nav_series() -> None:
    from datetime import datetime, timedelta

    from PIL import Image

    from SayuStock.utils.constant import ErroText
    from SayuStock.utils.render_data import build_compare_render_data
    from SayuStock.utils.market.models import Bar, SymbolRef, KlineSeries
    from SayuStock.stock_stockinfo.chart_compare import draw_compare_chart

    payload = json.loads((FIX / "tiantian_nav_720001.json").read_text(encoding="utf-8"))
    fund = parse_nav_rows(
        payload["Datas"],
        symbol=make_fund_symbol("720001", "财通价值动量混合A", "混合型-灵活"),
        period=KlinePeriod.D1_YEAR,
        start=None,
        end=None,
    )
    assert not is_market_error(fund)
    etf_bars = []
    start = datetime(2026, 8, 26)
    for index, close in enumerate([4.0, 4.1, 4.05, 4.2, 4.15]):
        ts = start + timedelta(days=index)
        etf_bars.append(
            Bar(
                ts=ts,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1.0,
                amount=1.0,
                amplitude=0.0,
                change_pct=0.0,
                change_amount=0.0,
                turnover_rate=0.0,
            )
        )
    etf = KlineSeries(
        symbol=SymbolRef(
            code="510300",
            name="沪深300ETF",
            asset_class=AssetClass.ETF,
            exchange="SSE",
            provider_symbol="1.510300",
            sec_type="ETF",
        ),
        period=KlinePeriod.D1_YEAR,
        bars=tuple(etf_bars),
        adjusted=True,
    )
    data = build_compare_render_data([fund, etf])
    assert not isinstance(data, str)
    assert len(data.items) == 2
    fig = draw_compare_chart([fund, etf])
    assert fig != ErroText["notData"]
    assert isinstance(fig, Image.Image)
    assert fig.size[0] > 100


def _bar(ts_day: int, close: float) -> Bar:
    from datetime import datetime

    ts = datetime(2026, 8, ts_day)
    return Bar(
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
        amount=0.0,
        amplitude=0.0,
        change_pct=0.0,
        change_amount=0.0,
        turnover_rate=0.0,
    )


class _FakeMarket(PartialMarketData):
    provider_name = "fake"

    async def resolve(self, query: str) -> SymbolRef | None:
        text = query.strip()
        if text in {"720001", "150.720001", "财通价值动量混合A"}:
            return make_fund_symbol("720001", "财通价值动量混合A", "混合型-灵活")
        if text == "510300":
            return SymbolRef(
                code="510300",
                name="沪深300ETF",
                asset_class=AssetClass.ETF,
                exchange="SSE",
                provider_symbol="1.510300",
                sec_type="ETF",
            )
        return SymbolRef(
            code=text,
            name=text,
            asset_class=AssetClass.EQUITY,
            exchange="SSE",
            provider_symbol=text,
        )

    async def kline(
        self,
        query: str,
        period: KlinePeriod,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> KlineSeries:
        _ = start, end
        ref = await self.resolve(query)
        assert ref is not None
        return KlineSeries(symbol=ref, period=period, bars=(_bar(26, 1.0), _bar(27, 1.1)), adjusted=True)

    async def quote(self, query: str) -> Quote:
        ref = await self.resolve(query)
        assert ref is not None
        return Quote(
            symbol=ref,
            price=1.0,
            open=1.0,
            high=1.0,
            low=1.0,
            prev_close=1.0,
            change_pct=0.0,
            change_amount=0.0,
            volume=0.0,
            amount=0.0,
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

    async def sector_menu(self, kind: str) -> MarketError:
        from SayuStock.utils.market.errors import unsupported

        _ = kind
        return unsupported("x", provider="fake")


def test_kline_command_detects_otc_fund() -> None:
    from SayuStock.utils.market import set_market
    from SayuStock.stock_stockinfo.data import CloudMapDataService

    async def _run() -> None:
        set_market(_FakeMarket())
        try:
            svc = CloudMapDataService()
            assert await svc._should_compare_otc_fund("720001")
            assert await svc._should_compare_otc_fund("720001 510300")
            assert not await svc._should_compare_otc_fund("510300")
            assert not await svc._should_compare_otc_fund("600519")
        finally:
            set_market(None)

    asyncio.run(_run())


def test_fetch_kline_fund_becomes_compare() -> None:
    from SayuStock.utils.market import set_market
    from SayuStock.stock_stockinfo.data import CloudMapDataService

    async def _run() -> None:
        set_market(_FakeMarket())
        try:
            svc = CloudMapDataService()
            fund = await svc.fetch("720001", "single-stock-kline-101", None, None)
            assert fund.sector == "compare-stock"
            assert len(fund.raw_datas) == 1
            assert isinstance(fund.raw_datas[0], KlineSeries)
            assert fund.raw_datas[0].symbol.asset_class == AssetClass.FUND
            etf = await svc.fetch("510300", "single-stock-kline-101", None, None)
            assert etf.sector == "single-stock-kline-101"
            mixed = await svc.fetch("720001 510300", "single-stock-kline-101", None, None)
            assert mixed.sector == "compare-stock"
            assert len(mixed.raw_datas) == 2
        finally:
            set_market(None)

    asyncio.run(_run())
