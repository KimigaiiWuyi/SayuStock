"""七大行情/IPO 适配器离线解析核心测试（基于 fixtures 与真实接口采样）。"""

from __future__ import annotations

import sys
import json
import asyncio
from types import ModuleType
from pathlib import Path
from datetime import date, datetime
from unittest.mock import patch

import pytest

from SayuStock.utils.render_data import build_ipo_calendar_render_data
from SayuStock.utils.render_text import ipo_calendar_text
from SayuStock.utils.stock.utils import calculate_difference
from SayuStock.stock_ipo.draw_ipo import parse_ipo_market_filter
from SayuStock.stock_ipo.ipo_html import build_ipo_calendar_html
from SayuStock.utils.market.enums import (
    RankBy,
    IpoStage,
    BoardKind,
    IpoMarket,
    AssetClass,
    KlinePeriod,
)
from SayuStock.utils.market.compat import (
    board_to_em_dict,
    kline_to_em_dict,
    quote_with_intraday_to_em,
)
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import (
    Bar,
    Quote,
    IpoEvent,
    SymbolRef,
    IntradaySeries,
    MarketTurnover,
    breadth_counts as counts_of,
)
from SayuStock.utils.market.fund_route import (
    extract_fund_code,
    is_otc_fund_series,
    maybe_otc_fund_query,
)
from SayuStock.utils.market.adapters.ths import client as ths_client
from SayuStock.utils.market.convert.dataframe import board_to_df, kline_to_df, kline_to_cn_df
from SayuStock.utils.market.adapters.okx.parse import (
    candles_to_bars,
    build_kline_series,
    build_intraday_series,
    quote_from_index_ticker,
)
from SayuStock.utils.market.adapters.ths.parse import (
    parse_snapshot_item,
    parse_historical_bars,
    ths_symbol_from_secid,
)
from SayuStock.utils.market.adapters.sina.parse import (
    industry_menu,
    parse_hq_line,
    parse_hq_line_us,
    parse_kline_rows,
    parse_breadth_rows,
    parse_minline_rows,
    parse_us_daily_rows,
    _limit_threshold_pct,
    parse_money_flow_rank,
    parse_turnover_quotes,
    parse_industry_summary,
    sina_symbol_from_secid,
)
from SayuStock.utils.market.adapters.nasdaq.parse import (
    dedupe_ipo_events,
    months_for_window,
    parse_nasdaq_calendar,
)
from SayuStock.utils.market.adapters.tencent.parse import (
    parse_qt_line,
    parse_qt_line_fx,
    parse_qt_line_hf,
    parse_qt_line_hk,
    parse_qt_line_us,
    tencent_quote_only,
    parse_kline_payload as parse_tencent_kline_payload,
    intraday_from_minute_bars,
    tencent_symbol_from_secid,
)
from SayuStock.utils.market.adapters.aastocks.parse import (
    parse_mainpage,
    parse_upcoming,
    hk_ipo_events_from_extras,
)
from SayuStock.utils.market.adapters.tencent.client import _QT_RE
from SayuStock.utils.market.adapters.tiantian.parse import (
    parse_nav_rows,
    make_fund_symbol,
    quote_from_nav_rows,
    parse_search_payload,
)
from SayuStock.utils.market.adapters.eastmoney.provider import EastMoneyMarketData
from SayuStock.utils.market.adapters.eastmoney.parse_ipo import (
    parse_ipo_apply_payload,
    parse_ipo_clist_payload,
)
from SayuStock.utils.market.adapters.eastmoney.parse_board import parse_board_payload
from SayuStock.utils.market.adapters.eastmoney.parse_kline import parse_kline_payload
from SayuStock.utils.market.adapters.eastmoney.parse_quote import parse_quote_payload
from SayuStock.utils.market.adapters.eastmoney.parse_breadth import parse_breadth_payload
from SayuStock.utils.market.adapters.eastmoney.parse_finance import parse_financial_snapshot_payload
from SayuStock.utils.market.adapters.eastmoney.parse_intraday import (
    extract_trends_from_payload,
    parse_intraday_from_trends_list,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _sym(secid: str = "1.600519", name: str = "贵州茅台", *, sec_type: str = "沪A") -> SymbolRef:
    return SymbolRef(
        code=secid.split(".")[-1],
        name=name,
        asset_class=AssetClass.EQUITY,
        exchange="SSE",
        provider_symbol=secid,
        sec_type=sec_type,
    )


def test_eastmoney_quote_f45_fallback_and_intraday() -> None:
    """东财 f45=最低/f170=涨跌幅、f43='-' 回退与 trends 分时解析。"""
    payload = _load("quote_600519.json")
    assert isinstance(payload, dict)
    q = parse_quote_payload(payload, provider_symbol="1.600519", sec_type="沪深A")
    assert not is_market_error(q)
    assert q.price == 1680.0 and q.low == 1660.0 and q.change_pct == 1.82
    assert q.symbol.display_name == "贵州茅台 (沪深A)" and q.prev_close == 1650.0

    trends = extract_trends_from_payload(payload)
    series = parse_intraday_from_trends_list(trends, q.symbol, q)
    assert not is_market_error(series) and len(series.points) == 2 and series.points[-1].price == 1680.0

    dash = {
        "rc": 0,
        "data": {"f57": "TLM", "f58": "三十债主连", "f43": "-", "f60": 115.4, "f170": "-"},
    }
    qd = parse_quote_payload(dash, provider_symbol="220.TLM", sec_type="国债期货")
    assert not is_market_error(qd) and qd.price == 115.4 and qd.change_pct is None

    no_name = parse_quote_payload({"data": {"f57": "609999", "f43": 106.0, "f60": 100.0}}, provider_symbol="1.609999")
    assert not is_market_error(no_name) and no_name.symbol.name == "609999"


def test_eastmoney_kline_board_finance_and_sort_order() -> None:
    """东财 K 线、板块、财务快照解析与 board/rank_list po 排序参数映射。"""
    kl = parse_kline_payload(_load("kline_600519.json"), symbol=_sym(), period=KlinePeriod.D1)
    assert not is_market_error(kl) and len(kl.bars) == 3 and kl.bars[-1].close == 1650.0
    assert list(kline_to_df(kl)["close"])[-1] == 1650.0
    assert len(kline_to_em_dict(kl)["data"]["klines"]) == 3

    snap = parse_board_payload(_load("board_clist.json"), kind=BoardKind.A_SHARE, title="沪深A")
    assert not is_market_error(snap) and len(snap.rows) == 2 and snap.rows[0].code == "600519"
    assert len(board_to_df(snap)) == 2 and board_to_em_dict(snap)["data"]["total"] == 2

    fin = parse_financial_snapshot_payload("600519", _load("finance_main.json"))
    assert not is_market_error(fin) and fin.roe == 30.5 and fin.eps == 55.2 and fin.industry_type == "standard"

    provider = EastMoneyMarketData()
    recorded_po: list[int] = []
    recorded_params: list[dict[str, str]] = []

    async def _fake_list(_m: str, is_loop: bool = False, po: int = 1, pz: int = 20) -> dict[str, object]:
        _ = is_loop, pz
        recorded_po.append(po)
        return {"data": {"diff": [{"f12": "BK0437", "f14": "电子", "f2": 100.0, "f3": 2.5, "f6": 1e9}]}}

    async def _fake_req(_url: str, _method: str, params: list[tuple[str, str]]) -> dict[str, object]:
        recorded_params.append(dict(params))
        return {"data": {"diff": [{"f12": "600519", "f14": "贵州茅台", "f2": 1800.0, "f3": 1.5, "f6": 5e9}]}}

    with (
        patch.object(
            __import__(
                "SayuStock.utils.market.adapters.eastmoney.provider", fromlist=["EASTMONEY_REQUESTER"]
            ).EASTMONEY_REQUESTER,
            "get_market_list",
            side_effect=_fake_list,
        ),
        patch.object(
            __import__(
                "SayuStock.utils.market.adapters.eastmoney.provider", fromlist=["EASTMONEY_REQUESTER"]
            ).EASTMONEY_REQUESTER,
            "stock_request",
            side_effect=_fake_req,
        ),
    ):
        asyncio.run(provider.board(BoardKind.INDUSTRY, limit=20, sort_asc=True))
        asyncio.run(provider.board(BoardKind.INDUSTRY, limit=20, sort_asc=False))
        assert recorded_po == [0, 1]
        asyncio.run(provider.rank_list(RankBy.AMOUNT, limit=20, high_first=True))
        asyncio.run(provider.rank_list(RankBy.AMOUNT, limit=20, high_first=False))
        assert [p.get("po") for p in recorded_params] == ["1", "0"]


def test_eastmoney_breadth_turnover_and_holiday_calendar() -> None:
    """东财涨跌分布 11 档归并、两市成交额今日/差值反解及长假最近交易日计算。"""
    payload: dict[str, object] = {
        "2": [1200, 900, 600, 400, 300, 180, 120, 80, 50, 30],
        "3": [1100, 800, 500, 350, 250, 150, 100, 70, 45, 25],
        "4": 200,
        "5": 61,
        "6": 18,
    }
    bar = parse_breadth_payload(payload)
    assert not is_market_error(bar)
    counts = counts_of(bar)
    assert counts["涨停"] == 61 and counts["跌停"] == 18 and counts["平"] == 200
    assert counts["5~10"] == 460 and counts["-5~-10"] == 390
    assert is_market_error(parse_breadth_payload("<html>blocked</html>"))

    stand_in = ModuleType("SayuStock.utils.stock.request")
    today, diff = 1_000_000_000_000.0, 200_000_000_000.0

    async def _ok_hours() -> tuple[float, float, datetime | None]:
        return (today, diff, datetime(2026, 9, 30, 15, 0))

    async def _fail_hours() -> tuple[float, float, datetime | None]:
        return (0.0, 0.0, None)

    setattr(stand_in, "get_hours_from_em", _ok_hours)
    with patch.dict(sys.modules, {"SayuStock.utils.stock.request": stand_in}):
        mt = asyncio.run(EastMoneyMarketData().market_turnover())
    assert not is_market_error(mt) and mt.amount == today and mt.prev_amount == today - diff

    setattr(stand_in, "get_hours_from_em", _fail_hours)
    with patch.dict(sys.modules, {"SayuStock.utils.stock.request": stand_in}):
        err = asyncio.run(EastMoneyMarketData().market_turnover())
    assert is_market_error(err) and err.code == "network"

    trends = [
        *[f"2026-09-29 09:{30 + i:02d},0,0,0,0,0,100.0,0" for i in range(3)],
        *[f"2026-09-30 09:{30 + i:02d},0,0,0,0,0,200.0,0" for i in range(3)],
    ]
    with patch("SayuStock.utils.stock.utils.get_adjusted_date", return_value=datetime(2026, 10, 7, 10, 0)):
        amt, d_amt, actual_date = calculate_difference(trends)
    assert amt == 600.0 and d_amt == 300.0 and actual_date == datetime(2026, 9, 30)


def test_okx_parse_kline_intraday_and_index() -> None:
    """OKX candle → K 线、分时、盘口与指数行情解析。"""
    sample = [
        ["1722470400000", "60000", "61000", "59500", "60500", "10", "0", "600000"],
        ["1722470460000", "60500", "61200", "60400", "61000", "12", "0", "732000"],
        ["1722470520000", "61000", "61500", "60800", "61200", "8", "0", "489600"],
    ]
    bars = candles_to_bars(sample)
    assert len(bars) == 3 and bars[0].open == 60000.0 and bars[-1].close == 61200.0
    kl = build_kline_series("BTC-USDT", sample, KlinePeriod.D1)
    assert not is_market_error(kl) and kl.symbol.exchange == "OKX" and len(kline_to_em_dict(kl)["data"]["klines"]) == 3

    intr = build_intraday_series("BTC-USDT", sample)
    assert not is_market_error(intr) and intr.quote is not None and intr.quote.price == 61200.0
    assert len(quote_with_intraday_to_em(intr)["trends"]) == 3

    iq = quote_from_index_ticker("BTC-USD", price=100.0, open_24h=90.0, open_utc8=95.0)
    assert iq.prev_close == 95.0 and iq.change_pct is not None


def test_sina_parse_symbols_quotes_kline_and_breadth() -> None:
    """新浪北交所/美股符号、hq/K线/分时、概念资金流与涨跌停阈值归档。"""
    assert sina_symbol_from_secid("0.920000") == "bj920000"
    assert sina_symbol_from_secid("0.000001") == "sz000001"
    assert sina_symbol_from_secid("105.QQQ") == "gb_qqq"
    assert sina_symbol_from_secid("100.NDX") == "gb_ixic"

    hq = (
        "贵州茅台,1262.990,1266.980,1257.120,1265.880,1256.100,1257.120,1257.130,"
        "2489087,3135849108.000,831,1257.120,200,1257.110,100,1257.080,200,1257.060,"
        "200,1257.050,100,1257.130,200,1257.240,100,1257.280,1600,1258.000,100,"
        "1258.280,2026-09-18,15:34:59,00"
    )
    q = parse_hq_line(hq, symbol=_sym())
    assert not is_market_error(q) and q.price == 1257.12 and q.prev_close == 1266.98

    us_hq = (
        "阿里巴巴,110.8000,-4.74,2026-09-24 16:08:34,-5.5100,112.0000,112.2800,110.5910,"
        "191.6200,91.9900,12223741,9314764,275407096431,6.41,17.290000,0.00,0.00,0.00,0.00,"
        "2485623614,40,112.2500,1.31,1.45,Sep 24 04:08AM EDT,Sep 23 04:03PM EDT,116.3100,"
        "157210,1,2026,1360968048.1589,113.1800,111.9700,17657831.0876,112.0800,110.8000"
    )
    qu = parse_hq_line_us(us_hq, symbol=_sym("106.BABA", "阿里巴巴", sec_type="美股"))
    assert not is_market_error(qu) and qu.prev_close == 116.31 and qu.as_of is None

    daily = [
        {
            "day": "2026-09-18",
            "open": "1262.99",
            "high": "1265.88",
            "low": "1256.10",
            "close": "1257.12",
            "volume": "2489087",
        }
    ]
    assert not is_market_error(parse_kline_rows(daily, symbol=_sym(), period=KlinePeriod.D1))
    us_daily = [
        {
            "d": "2026-09-22",
            "o": "740.98",
            "h": "748.35",
            "l": "740.93",
            "c": "747.46",
            "v": "40128826",
            "a": "29927400000",
        },
        {
            "d": "2026-09-23",
            "o": "746.97",
            "h": "747.13",
            "l": "738.19",
            "c": "741.21",
            "v": "33191773",
            "a": "24601000000",
        },
    ]
    assert not is_market_error(
        parse_us_daily_rows(us_daily, symbol=_sym("105.QQQ", "QQQ"), period=KlinePeriod.D1, limit=2)
    )
    assert not is_market_error(
        parse_minline_rows(
            [{"m": "09:30:00", "v": "43000", "p": "1259.19", "avg_p": "1260.923"}],
            symbol=_sym(),
            quote=None,
            trade_date="2026-09-18",
        )
    )

    fljk = {"gn_cxy": "gn_cxy,创新药,52,35.11,1.28,3.622,300266423,53116148987,sh688185,20.005,90.20,8.900,康希诺"}
    snap = parse_industry_summary(fljk, kind=BoardKind.CONCEPT, title="概念板块")
    assert not is_market_error(snap) and snap.rows[0].lead_name == "康希诺"
    menu = industry_menu(fljk)
    assert not is_market_error(menu) and menu["创新药"] == "gn_cxy"
    mf = parse_money_flow_rank(
        [
            {
                "symbol": "sz002402",
                "name": "江淮汽车",
                "trade": "41.83",
                "changeratio": "0.05492",
                "amount": "1",
                "netamount": "1196600000",
            }
        ],
        rank_by=RankBy.MAIN_INFLOW,
        high_first=True,
        limit=5,
    )
    assert not is_market_error(mf) and mf.rows[0].change_pct == 5.492
    assert _limit_threshold_pct("688001") == 20.0 and _limit_threshold_pct("920000") == 30.0
    b_bar = parse_breadth_rows(
        [
            {
                "code": "000911",
                "name": "*ST广糖",
                "trade": "5.40",
                "changepercent": 9.98,
                "settlement": "4.91",
                "volume": 1,
            },
            {
                "code": "688265",
                "name": "南模生物",
                "trade": "64.98",
                "changepercent": 19.867,
                "settlement": "54.21",
                "volume": 1,
            },
        ]
    )
    assert not is_market_error(b_bar) and counts_of(b_bar)["涨停"] == 1 and counts_of(b_bar)["5~10"] == 1
    mt = parse_turnover_quotes(
        [
            Quote(
                symbol=_sym("1.000001"),
                price=1.0,
                open=None,
                high=None,
                low=None,
                prev_close=1.0,
                change_pct=0.0,
                change_amount=0.0,
                volume=None,
                amount=500.0,
                turnover_rate=None,
                pe=None,
                pb=None,
                market_cap=None,
                float_market_cap=None,
                industry=None,
                limit_up=None,
                limit_down=None,
                as_of=datetime(2026, 9, 30, 15, 0),
            )
        ]
    )
    assert isinstance(mt, MarketTurnover) and mt.amount == 500.0 and mt.prev_amount is None


def test_etf_disclosure_empty_page_and_shenzhen_creation_basket() -> None:
    from datetime import date

    from SayuStock.utils.market.adapters.etf_basket import (
        HolderPage,
        CreationBasket,
        parse_holder_page,
        parse_creation_basket,
    )

    day = date(2026, 11, 17)
    assert parse_holder_page({"success": True, "pages": 0, "data": []}, day) == HolderPage((), 0)
    assert isinstance(parse_holder_page({"success": True, "pages": 4, "data": []}, day), str)
    xml = b"""<PCF><SecurityID>159843</SecurityID><TradingDay>20261117</TradingDay><TotalRecordNum>3</TotalRecordNum>
    <Item><UnderlyingSecurityID>600519</UnderlyingSecurityID><UnderlyingSecurityIDSource>101</UnderlyingSecurityIDSource>
    <ComponentShare>300</ComponentShare></Item>
    <Item><UnderlyingSecurityID>000001</UnderlyingSecurityID><UnderlyingSecurityIDSource>102</UnderlyingSecurityIDSource>
    <ComponentShare>200</ComponentShare></Item>
    <Item><UnderlyingSecurityID>159900</UnderlyingSecurityID><ComponentShare>0</ComponentShare></Item></PCF>"""
    basket = parse_creation_basket(xml, "159843", day)
    assert isinstance(basket, CreationBasket)
    assert [(stock.symbol, stock.quantity) for stock in basket.stocks] == [("sh600519", 300), ("sz000001", 200)]
    assert not basket.zero_stocks
    assert isinstance(parse_creation_basket(xml, "159862", day), str)


def test_etf_keeps_tracked_index_distinct_from_benchmark() -> None:
    from SayuStock.utils.market.adapters.etf_basket import parse_tracked_index

    tracked = "<th>跟踪标的</th><td><a>细分食品</a></td>"
    benchmark = "<th>业绩比较基准</th><td>中证细分食品饮料产业主题指数收益率</td>"
    assert parse_tracked_index(tracked + benchmark) == "细分食品"
    assert parse_tracked_index(tracked + benchmark.replace("细分食品饮料产业主题", "白酒")) == "细分食品"
    assert parse_tracked_index(benchmark) == ""


def test_tencent_parse_qt_kline_and_ndays() -> None:
    """腾讯符号映射、qt A股/美股/全天候解析、K 线及分钟拼五日分时。"""
    assert tencent_symbol_from_secid("0.920000") == "bj920000"
    assert tencent_symbol_from_secid("100.NDX") == "usIXIC"
    assert tencent_symbol_from_secid("2.932000") is None and tencent_quote_only("122.XAU")
    assert {m.group("sym") for m in _QT_RE.finditer('v_hf_XAU="4190";v_sh600519="1~a";')} == {"hf_XAU", "sh600519"}

    qt_cn = "~".join(
        ["1", "贵州茅台", "600519", "1257.12", "1266.98", "1262.99", "24891", "12061", "12829"]
        + ["0"] * 20
        + [
            "",
            "20260918161436",
            "-9.86",
            "-0.78",
            "1265.88",
            "1256.10",
            "",
            "24891",
            "313585",
            "0.20",
            "19.30",
            "",
            "1265.88",
            "1256.10",
            "0.77",
            "15715.03",
            "15715.03",
            "6.25",
            "1393.68",
            "1140.28",
        ]
    )
    qc = parse_qt_line(qt_cn, symbol=_sym())
    assert not is_market_error(qc) and qc.volume == 2489100.0 and qc.amount == 3135850000.0 and qc.pb == 6.25
    assert parse_qt_line(qt_cn, symbol=_sym(), require_price=True) == qc
    missing = qt_cn.replace("~1257.12~", "~0~", 1)
    fallback = parse_qt_line(missing, symbol=_sym())
    assert not is_market_error(fallback) and fallback.price == 1266.98
    assert is_market_error(parse_qt_line(missing, symbol=_sym(), require_price=True))
    assert is_market_error(parse_qt_line(qt_cn, symbol=_sym("1.600887"), require_price=True))

    qt_us = "~".join(
        ["200", "阿里巴巴", "BABA.N", "110.80", "116.31", "112.00", "12223741", "0", "0"]
        + ["0"] * 20
        + [
            "",
            "2026-09-23 16:07:51",
            "-5.51",
            "-4.74",
            "112.28",
            "110.59",
            "USD",
            "12223741",
            "1359611049",
            "0.49",
            "25.33",
            "",
            "17.38",
            "1:8",
            "1.45",
            "2705.6",
            "2754.0",
        ]
    )
    qu = parse_qt_line_us(qt_us, symbol=_sym("106.BABA", "阿里巴巴"))
    assert not is_market_error(qu) and qu.pb == 1.45 and qu.as_of is None

    assert isinstance(
        parse_qt_line_hk(
            "100~恒生~HSI~24210.94~23785.79~23941.26~"
            + "~".join(["0"] * 24)
            + "~2026/10/09 14:15:52~425.15~1.79~24224.05~23941.26",
            symbol=_sym("100.HSI", "恒生"),
        ),
        Quote,
    )
    assert isinstance(
        parse_qt_line_hf(
            "4190.83,1.39,4190.83,4191.18,4207.46,4130.90,14:29:00,4133.46,4134.26,0,0,0,2026-10-09,伦敦金",
            symbol=_sym("122.XAU", "XAU"),
        ),
        Quote,
    )
    assert isinstance(
        parse_qt_line_fx(
            "310~美元日元~USDJPY~158.20~0~20261009143020~157.82~157.82~158.21~157.75~0~0~0.50~0.32",
            symbol=_sym("119.USDJPY", "JPY"),
        ),
        Quote,
    )

    kl = parse_tencent_kline_payload(
        {
            "code": 0,
            "data": {"sh600519": {"qfqday": [["2026-09-18", "1262.99", "1257.12", "1265.88", "1256.10", "24891"]]}},
        },
        symbol=_sym(),
        tencent_symbol="sh600519",
        period=KlinePeriod.D1,
        unit="day",
        adjusted=True,
    )
    assert not is_market_error(kl) and kl.bars[0].volume == 2489100.0

    bars = [
        Bar(
            ts=datetime(2026, 10, 7, 15, 0),
            open=90.0,
            high=90.0,
            low=90.0,
            close=90.0,
            volume=100.0,
            amount=1000.0,
            amplitude=None,
            change_pct=None,
            change_amount=None,
            turnover_rate=None,
        ),
        Bar(
            ts=datetime(2026, 10, 8, 15, 0),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=100.0,
            amount=1000.0,
            amplitude=None,
            change_pct=None,
            change_amount=None,
            turnover_rate=None,
        ),
        Bar(
            ts=datetime(2026, 10, 9, 15, 0),
            open=104.0,
            high=104.0,
            low=104.0,
            close=104.0,
            volume=100.0,
            amount=700.0,
            amplitude=None,
            change_pct=None,
            change_amount=None,
            turnover_rate=None,
        ),
    ]
    nd = intraday_from_minute_bars(bars, symbol=_sym(), quote=None, ndays=2)
    assert (
        isinstance(nd, IntradaySeries) and nd.ref_close == 90.0 and nd.quote is not None and nd.quote.change_pct == 4.0
    )


def test_ths_parse_snapshot_bars_and_errors() -> None:
    """同花顺符号映射、快照与历史日 K 解析、envelope 错误码分类。"""
    assert ths_symbol_from_secid("1.600519", AssetClass.EQUITY) == ("600519.SH", "stock")
    assert ths_symbol_from_secid("1.000300", AssetClass.EQUITY) == ("000300.SH", "index")
    assert ths_symbol_from_secid("105.QQQ", AssetClass.EQUITY) is None

    q = parse_snapshot_item(
        {
            "thscode": "600519.SH",
            "volume": 2636630,
            "turnover": 3260057900,
            "last_price": 1235.58,
            "price_change": -8.3,
            "price_change_ratio_pct": -0.667,
            "open_price": 1244.6,
            "high_price": 1245.87,
            "low_price": 1230.88,
            "prev_price": 1243.88,
        },
        symbol=_sym(),
        timestamp_ms=1790676533000,
    )
    assert not is_market_error(q) and q.price == 1235.58 and q.volume == 2636630.0
    bars = parse_historical_bars(
        {
            "item": [
                {
                    "date_ms": 1753632000000,
                    "volume": 3858632.0,
                    "turnover": 5560966385.77,
                    "open_price": 1401.0,
                    "high_price": 1403.0,
                    "low_price": 1384.0,
                    "close_price": 1386.6,
                }
            ]
        }
    )
    assert len(bars) == 1 and (bars[0].ts.year, bars[0].ts.month, bars[0].ts.day) == (2025, 7, 28)
    assert ths_client._envelope_error(2003, "bad key").code == "network"
    assert ths_client._envelope_error(3001, "missing").code == "unsupported"
    assert ths_client._envelope_error(3002, "wait").code == "empty"
    assert ths_client._envelope_error(1002, "bad code").code == "parse"


def test_tiantian_fund_route_search_nav_and_quote() -> None:
    """天天基金场外路由、搜索、单位净值日 K 与盘口解析。"""
    assert maybe_otc_fund_query("720001") and not maybe_otc_fund_query("510300")
    assert extract_fund_code("150.720001") == "720001" and extract_fund_code("财通") is None

    search_raw = _load("tiantian_search_720001.json")
    nav_raw = _load("tiantian_nav_720001.json")
    assert isinstance(search_raw, dict) and isinstance(nav_raw, dict)
    hit = parse_search_payload(search_raw, "720001")
    assert hit is not None and hit.code == "720001" and hit.unit_nav == 14.081

    symbol = make_fund_symbol("720001", "财通价值动量混合A", "混合型-灵活")
    series = parse_nav_rows(
        nav_raw["Datas"], symbol=symbol, period=KlinePeriod.D1_YEAR, start=date(2026, 8, 26), end=date(2026, 9, 1)
    )
    assert not is_market_error(series) and is_otc_fund_series(series) and len(series.bars) == 5
    assert len(kline_to_cn_df(series)) == 5

    q = quote_from_nav_rows(symbol, hit, nav_raw["Datas"])
    assert q.price == 14.081 and q.change_pct == -3.44


def test_ipo_calendars_parse_and_render() -> None:
    """IPO 阶段判断、东财/纳斯达克/AAStocks 解析、渲染数据/HTML 与播报文本。"""
    assert (
        IpoEvent(market=IpoMarket.CN, code="1", name="n", apply_date=date(2026, 9, 28)).stage_on(date(2026, 9, 21))
        == IpoStage.APPLY
    )
    assert (
        IpoEvent(market=IpoMarket.HK, code="2", name="n", listing_date=date(2026, 9, 21)).stage_on(date(2026, 9, 21))
        == IpoStage.LISTED
    )

    cn_pending = parse_ipo_apply_payload(_load("ipo_apply_cn.json"))
    cn_listed = parse_ipo_apply_payload(_load("ipo_apply_cn_listed.json"))
    hk_clist = parse_ipo_clist_payload(_load("ipo_clist_hk.json"), IpoMarket.HK)
    us_clist = parse_ipo_clist_payload(_load("ipo_clist_us.json"), IpoMarket.US)
    nasdaq = parse_nasdaq_calendar(_load("ipo_nasdaq_202609.json"))
    assert isinstance(cn_pending, list) and {e.code: e for e in cn_pending}["920157"].board == "北交所"
    assert isinstance(cn_listed, list) and {e.code: e for e in cn_listed}["601091"].raise_yi == pytest.approx(13.652911)
    assert isinstance(hk_clist, list) and "83599" not in {e.code for e in hk_clist}
    assert (
        isinstance(us_clist, list) and "XIIIW" not in {e.code for e in us_clist} and "EMI" in {e.code for e in us_clist}
    )
    assert isinstance(nasdaq, list) and {e.code: e for e in nasdaq}["HYACU"].currency == "USD"

    assert (
        len(
            dedupe_ipo_events(
                [
                    IpoEvent(market=IpoMarket.US, code="BMB", name="Bamboo", issue_price_text="18.00-20.00"),
                    IpoEvent(market=IpoMarket.US, code="bmb", name="Bamboo", issue_price=19.0),
                ]
            )
        )
        == 1
    )
    assert parse_ipo_market_filter("a股 港股") == [IpoMarket.CN, IpoMarket.HK]
    assert months_for_window(date(2026, 12, 30)) == ["2026-12", "2027-01"]

    main_ex = parse_mainpage((FIX / "aastocks_mainpage.html").read_text(encoding="utf-8"))
    up_ex = parse_upcoming((FIX / "aastocks_upcoming.html").read_text(encoding="utf-8"))
    assert main_ex["09607"].name == "彤程新材料" and "09607" in up_ex
    assert len(hk_ipo_events_from_extras(main_ex)) > 0

    events = [
        IpoEvent(
            market=IpoMarket.CN,
            code="301569",
            name="联亚药业",
            apply_date=date(2026, 9, 22),
            listing_date=date(2026, 10, 10),
            board="创业板",
        ),
        IpoEvent(
            market=IpoMarket.CN,
            code="601091",
            name="沈鼓集团",
            apply_date=date(2026, 9, 8),
            listing_date=date(2026, 9, 20),
            first_day_change=373.8,
        ),
        IpoEvent(
            market=IpoMarket.HK,
            code="06727",
            name="星环科技",
            listing_date=date(2026, 9, 21),
            first_day_change=-9.18,
            oversubscription=72.9,
            issue_price=49.0,
            currency="HKD",
        ),
    ]
    data = build_ipo_calendar_render_data(events, anchor=date(2026, 9, 21))
    assert data.total == 3 and "沈鼓集团(601091)" in ipo_calendar_text(data)
    html = build_ipo_calendar_html(data)
    assert "星环科技" in html and "超购72.9倍" in html
