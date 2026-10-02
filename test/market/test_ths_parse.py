"""同花顺（扶摇）适配器离线测试：符号映射 / 快照与日K解析 / 错误码映射 / K线窗口。

payload 来自 2026-09-29 真实接口采样（默认 API Key）。
"""

from __future__ import annotations

import asyncio
from datetime import date

from SayuStock.utils.market.enums import AssetClass, KlinePeriod
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import SymbolRef
from SayuStock.utils.market.adapters.ths import client as ths_client, provider as ths_provider
from SayuStock.utils.market.adapters.ths.parse import (
    parse_snapshot_item,
    parse_historical_bars,
    ths_symbol_from_secid,
)

# -- 符号映射 ---------------------------------------------------------------


def _ref(secid: str, asset: AssetClass) -> SymbolRef:
    return SymbolRef(
        code=secid.split(".")[-1],
        name="测试",
        asset_class=asset,
        exchange="TEST",
        provider_symbol=secid,
    )


def test_ths_symbol_from_secid() -> None:
    # A 股个股：沪 / 深 / 北
    assert ths_symbol_from_secid("1.600519", AssetClass.EQUITY) == ("600519.SH", "stock")
    assert ths_symbol_from_secid("0.000001", AssetClass.EQUITY) == ("000001.SZ", "stock")
    assert ths_symbol_from_secid("0.920002", AssetClass.EQUITY) == ("920002.BJ", "stock")
    assert ths_symbol_from_secid("0.832566", AssetClass.EQUITY) == ("832566.BJ", "stock")
    # 指数：上证 / 深证
    assert ths_symbol_from_secid("1.000001", AssetClass.INDEX) == ("000001.SH", "index")
    assert ths_symbol_from_secid("0.399001", AssetClass.INDEX) == ("399001.SZ", "index")
    # 解析层对部分指数返回空 sec_type → asset_class 误判 EQUITY，按代码形态兜底判指数
    assert ths_symbol_from_secid("1.000001", AssetClass.EQUITY) == ("000001.SH", "index")
    assert ths_symbol_from_secid("1.000300", AssetClass.EQUITY) == ("000300.SH", "index")
    assert ths_symbol_from_secid("0.399006", AssetClass.EQUITY) == ("399006.SZ", "index")
    # 场内基金
    assert ths_symbol_from_secid("1.510300", AssetClass.ETF) == ("510300.SH", "fund")
    assert ths_symbol_from_secid("0.161725", AssetClass.ETF) == ("161725.SZ", "fund")
    # 不覆盖：美股 / 港股 / 场外基金（150.* 走天天基金槽）/ 债券
    assert ths_symbol_from_secid("105.QQQ", AssetClass.EQUITY) is None
    assert ths_symbol_from_secid("100.SPX", AssetClass.INDEX) is None
    assert ths_symbol_from_secid("116.00700", AssetClass.EQUITY) is None
    assert ths_symbol_from_secid("150.001056", AssetClass.FUND) is None
    assert ths_symbol_from_secid("1.019547", AssetClass.BOND) is None


# -- 快照解析（2026-09-29 实测采样） -----------------------------------------


def test_parse_stock_snapshot_item() -> None:
    item = {
        "thscode": "600519.SH",
        "ticker": "600519",
        "volume": 2636630,
        "turnover": 3260057900,
        "last_price": 1235.58,
        "price_change": -8.3,
        "price_change_ratio_pct": -0.667267,
        "open_price": 1244.6,
        "high_price": 1245.87,
        "low_price": 1230.88,
        "prev_price": 1243.88,
    }
    quote = parse_snapshot_item(item, symbol=_ref("1.600519", AssetClass.EQUITY), timestamp_ms=1790676533000)
    assert not is_market_error(quote)
    assert quote.price == 1235.58
    assert quote.open == 1244.6
    assert quote.high == 1245.87
    assert quote.low == 1230.88
    assert quote.prev_close == 1243.88
    assert quote.change_pct == -0.667267
    assert quote.change_amount == -8.3
    assert quote.volume == 2636630.0  # 股，无需换算
    assert quote.amount == 3260057900.0  # 元
    assert quote.turnover_rate is None  # 个股快照无换手
    assert quote.pe is None and quote.pb is None and quote.market_cap is None
    assert quote.as_of is not None
    assert quote.as_of.year == 2026 and quote.as_of.month == 9 and quote.as_of.day == 29


def test_parse_fund_snapshot_item() -> None:
    item = {
        "thscode": "510300.SH",
        "ticker": "510300",
        "last_price": 4.416,
        "open_price": 4.399,
        "high_price": 4.431,
        "low_price": 4.398,
        "prev_price": 4.417,
        "price_change_ratio_pct": -0.02264,
        "price_change": -0.001,
        "price_amplitude_ratio_pct": 0.747113,
        "volume": 468501410,
        "turnover": 2066922300,
        "turnover_ratio_pct": 1.937711,
    }
    quote = parse_snapshot_item(item, symbol=_ref("1.510300", AssetClass.ETF), timestamp_ms=1790676536000)
    assert not is_market_error(quote)
    assert quote.price == 4.416
    assert quote.turnover_rate == 1.937711  # 场内基金快照带换手
    assert quote.amount == 2066922300.0


def test_parse_snapshot_missing_price() -> None:
    result = parse_snapshot_item({"thscode": "600519.SH"}, symbol=_ref("1.600519", AssetClass.EQUITY))
    assert is_market_error(result)


# -- 日K解析（2026-09-29 实测采样，前复权带小数） ------------------------------


def test_parse_historical_bars() -> None:
    data = {
        "timestamp": 1753891200000,
        "item": [
            {
                "date_ms": 1753632000000,
                "volume": 3858632.0,
                "turnover": 5560966385.77,
                "open_price": 1401.01877,
                "high_price": 1403.00877,
                "low_price": 1384.3187699999999,
                "close_price": 1386.67877,
            },
            {
                "date_ms": 1753718400000,
                "volume": 2630408.0,
                "turnover": 3785848101.96,
                "open_price": 1387.6187699999998,
                "high_price": 1396.8087699999999,
                "low_price": 1383.02877,
                "close_price": 1387.01877,
            },
        ],
    }
    bars = parse_historical_bars(data)
    assert len(bars) == 2
    first = bars[0]
    # date_ms 为东八区交易日零点 → 朴素北京时间
    assert (first.ts.year, first.ts.month, first.ts.day) == (2025, 7, 28)
    assert first.ts.hour == 0 and first.ts.minute == 0
    assert first.open == 1401.01877
    assert first.close == 1386.67877
    assert first.volume == 3858632.0  # 股
    assert first.amount == 5560966385.77  # 元
    assert bars[1].ts.day == 29
    # 非法行（缺价格/缺时间）跳过；item 非列表返回空
    assert parse_historical_bars({"item": [{"date_ms": 1753632000000}]}) == []
    assert parse_historical_bars({}) == []


# -- 错误码映射（实测口径：无效Key=2003、未知代码=1002） ------------------------


def test_envelope_error_mapping() -> None:
    # 无效 / 缺失 Key → network（顺延，提示检查配置）
    err = ths_client._envelope_error(2003, "Invalid or revoked API key")
    assert err.code == "network" and "同花顺API密钥" in err.message
    assert ths_client._envelope_error(2001, "未认证").code == "network"
    # 标的不存在 / 类型不支持 → unsupported（跳过顺延）
    assert ths_client._envelope_error(3001, "标的不存在").code == "unsupported"
    assert ths_client._envelope_error(3004, "类型不支持").code == "unsupported"
    # 数据未就绪 → empty（顺延）
    assert ths_client._envelope_error(3002, "数据未就绪").code == "empty"
    # 限流 / 上游错误 → network
    assert ths_client._envelope_error(4001, "频率超限").code == "network"
    assert ths_client._envelope_error(5003, "数据源不可用").code == "network"
    # 参数类（如未知代码 1002）→ parse（顺延 + 告警日志）
    assert ths_client._envelope_error(1002, "Unknown A-share thscode: 999999.SH").code == "parse"


# -- Provider 路由与 K 线窗口（monkeypatch 网络层） ----------------------------


def _fake_resolve_factory(mapping: dict[str, SymbolRef]):
    async def fake_resolve(query: str) -> SymbolRef | None:
        return mapping.get(query)

    return fake_resolve


def test_provider_quote_routes_by_asset(monkeypatch) -> None:
    async def _run() -> None:
        refs = {
            "600519": _ref("1.600519", AssetClass.EQUITY),
            "上证指数": _ref("1.000001", AssetClass.INDEX),
            "510300": _ref("1.510300", AssetClass.ETF),
            "QQQ": _ref("105.QQQ", AssetClass.EQUITY),  # 美股不覆盖
        }
        monkeypatch.setattr(ths_provider, "resolve_em_symbol", _fake_resolve_factory(refs))

        stock_payload = {
            "timestamp": 1790676533000,
            "item": [
                {
                    "thscode": "600519.SH",
                    "last_price": 1235.58,
                    "prev_price": 1243.88,
                    "open_price": 1244.6,
                    "high_price": 1245.87,
                    "low_price": 1230.88,
                }
            ],
        }
        index_payload = {
            "timestamp": 1790676536000,
            "item": [
                {
                    "thscode": "000001.SH",
                    "last_price": 3830.45,
                    "prev_price": 3823.62,
                    "open_price": 3816.15,
                    "high_price": 3843.84,
                    "low_price": 3810.81,
                }
            ],
        }
        fund_payload = {
            "timestamp": 1790676536000,
            "item": [{"thscode": "510300.SH", "last_price": 4.416, "prev_price": 4.417}],
        }

        calls: list[tuple[str, object]] = []

        async def fake_stock_snapshot(thscodes):
            calls.append(("stock", list(thscodes)))
            return stock_payload

        async def fake_index_snapshot(thscodes):
            calls.append(("index", list(thscodes)))
            return index_payload

        async def fake_fund_snapshot(thscode):
            calls.append(("fund", thscode))
            return fund_payload

        monkeypatch.setattr(ths_provider, "fetch_stock_snapshot", fake_stock_snapshot)
        monkeypatch.setattr(ths_provider, "fetch_index_snapshot", fake_index_snapshot)
        monkeypatch.setattr(ths_provider, "fetch_fund_snapshot", fake_fund_snapshot)

        port = ths_provider.THSMarketData()
        # 单只个股
        q = await port.quote("600519")
        assert not is_market_error(q)
        assert q.price == 1235.58
        # 混合批量：个股批量 1 次 + 指数批量 1 次 + 基金单只 1 次，结果按入参顺序回填
        results = await port.quotes(["600519", "上证指数", "510300", "QQQ"])
        assert len(results) == 4
        assert not is_market_error(results[0])
        assert not is_market_error(results[1])
        assert not is_market_error(results[2])
        assert results[0].price == 1235.58
        assert results[1].price == 3830.45
        assert results[2].price == 4.416
        # 美股 → unsupported（不覆盖，交给注册表回落）
        assert is_market_error(results[3]) and results[3].code == "unsupported"
        assert ("stock", ["600519.SH"]) in calls
        assert ("index", ["000001.SH"]) in calls
        assert ("fund", "510300.SH") in calls

    asyncio.run(_run())


def test_provider_kline_window_and_routing(monkeypatch) -> None:
    async def _run() -> None:
        refs = {
            "600519": _ref("1.600519", AssetClass.EQUITY),
            "上证指数": _ref("1.000001", AssetClass.INDEX),
            "510300": _ref("1.510300", AssetClass.ETF),
        }
        monkeypatch.setattr(ths_provider, "resolve_em_symbol", _fake_resolve_factory(refs))

        captured: dict[str, tuple[str, int, int]] = {}

        def make_fake(tag):
            async def fake(thscode: str, start_ms: int, end_ms: int):
                captured[tag] = (thscode, start_ms, end_ms)
                return {
                    "timestamp": 1753891200000,
                    "item": [
                        {
                            "date_ms": 1753632000000,
                            "open_price": 1401.0,
                            "high_price": 1403.0,
                            "low_price": 1384.0,
                            "close_price": 1386.6,
                            "volume": 3858632.0,
                            "turnover": 5560966385.77,
                        }
                    ],
                }

            return fake

        monkeypatch.setattr(ths_provider, "fetch_stock_historical", make_fake("stock"))
        monkeypatch.setattr(ths_provider, "fetch_index_historical", make_fake("index"))
        monkeypatch.setattr(ths_provider, "fetch_fund_historical", make_fake("fund"))

        port = ths_provider.THSMarketData()
        end = date(2026, 9, 29)

        # D1 默认窗口：start 缺省 = end - 800 天；end 取到当日末（含当日 bar）
        series = await port.kline("600519", KlinePeriod.D1, end=end)
        assert not is_market_error(series)
        assert series.period == KlinePeriod.D1
        assert series.adjusted is True
        assert len(series.bars) == 1
        thscode, start_ms, end_ms = captured["stock"]
        assert thscode == "600519.SH"
        assert end_ms - start_ms + 1 == 801 * 86_400_000

        # 显式超 10 年窗口 → 夹到 10 年
        await port.kline("600519", KlinePeriod.D1, start=date(2015, 1, 1), end=end)
        _, start_ms, end_ms = captured["stock"]
        assert end_ms - start_ms + 1 == 3651 * 86_400_000

        # 指数走指数端点（同 10 年上限）
        await port.kline("上证指数", KlinePeriod.D1, end=end)
        assert captured["index"][0] == "000001.SH"

        # ETF 走基金端点，窗口上限 5 个自然年
        await port.kline("510300", KlinePeriod.D1, start=date(2015, 1, 1), end=end)
        thscode, start_ms, end_ms = captured["fund"]
        assert thscode == "510300.SH"
        assert end_ms - start_ms + 1 == 1826 * 86_400_000

        # 仅日线：分钟/周/月 K 不支持
        for period in (KlinePeriod.M5, KlinePeriod.W1, KlinePeriod.MON1):
            result = await port.kline("600519", period, end=end)
            assert is_market_error(result) and result.code == "unsupported"

    asyncio.run(_run())


def test_provider_intraday_unsupported(monkeypatch) -> None:
    async def _run() -> None:
        refs = {"600519": _ref("1.600519", AssetClass.EQUITY)}
        monkeypatch.setattr(ths_provider, "resolve_em_symbol", _fake_resolve_factory(refs))
        port = ths_provider.THSMarketData()
        # 高频动向（分时/分钟K）暂未开放外部接入
        result = await port.intraday("600519")
        assert is_market_error(result) and result.code == "unsupported"

    asyncio.run(_run())
