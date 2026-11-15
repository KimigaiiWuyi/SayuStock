"""分时时间轴对齐、五日分时与跨市场日线对齐核心回归测试。"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from SayuStock.render.compare import prepare_compare
from SayuStock.utils.constant import ErroText
from SayuStock.utils.time_range import (
    Market,
    get_market_sessions,
    is_market_active_now,
    get_session_anchor_date,
    get_trading_datetimes_bjt,
    is_within_trading_day_window,
)
from SayuStock.utils.render_data import (
    build_compare_render_data,
    _rows_from_resolved_trends,
    build_multi_stock_render_data,
    build_single_stock_render_data,
    _resolve_trend_absolute_datetimes,
)
from SayuStock.utils.market.enums import AssetClass, KlinePeriod
from SayuStock.utils.stock_period import (
    is_intraday_sector,
    parse_stock_img_request,
    intraday_ndays_from_sector,
)
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import (
    Bar,
    Quote,
    SymbolRef,
    KlineSeries,
    IntradayPoint,
    IntradaySeries,
)
from SayuStock.utils.market.convert.dataframe import kline_to_df
from SayuStock.utils.market.adapters.eastmoney.parse_intraday import (
    parse_trend_line,
    parse_intraday_from_trends_list,
)


def _as_float(value: object) -> float:
    assert isinstance(value, (int, float)), f"分时字段不是数值: {value!r}"
    return float(value)


def _symbol(
    *,
    code: str = "000001",
    name: str = "上证指数",
    asset_class: AssetClass = AssetClass.INDEX,
    exchange: str = "SSE",
    provider_symbol: str = "1.000001",
    sec_type: str = "指数",
) -> SymbolRef:
    return SymbolRef(
        code=code,
        name=name,
        asset_class=asset_class,
        exchange=exchange,
        provider_symbol=provider_symbol,
        sec_type=sec_type,
    )


def _quote(symbol: SymbolRef, price: float, *, prev_close: float = 3900.0, change_pct: float = 0.5) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        open=prev_close,
        high=price,
        low=price,
        prev_close=prev_close,
        change_pct=change_pct,
        change_amount=None,
        volume=1.0,
        amount=1.0,
        turnover_rate=1.0,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=None,
    )


def _series_from_trends(
    name: str,
    code: str,
    provider_symbol: str,
    trends: list[dict[str, object]],
    *,
    prev_close: float,
    change_pct: float,
) -> IntradaySeries:
    points: list[IntradayPoint] = []
    for row in trends:
        ts_s = str(row["datetime"])
        try:
            ts = dt.datetime.strptime(ts_s, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        price = _as_float(row["price"])
        points.append(
            IntradayPoint(
                ts=ts,
                price=price,
                open=price,
                high=price,
                low=price,
                volume=_as_float(row.get("amount") or 0),
                amount=_as_float(row.get("money") or 0),
                avg_price=price,
            )
        )
    symbol = _symbol(
        code=code,
        name=name,
        asset_class=AssetClass.EQUITY,
        exchange="EM",
        provider_symbol=provider_symbol,
        sec_type="",
    )
    quote = _quote(
        symbol,
        points[-1].price if points else prev_close,
        prev_close=prev_close,
        change_pct=change_pct,
    )
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=quote)


def _kline_series(
    *,
    name: str,
    code: str,
    exchange: str,
    sec_type: str,
    closes: list[float],
    hour: int,
    period: KlinePeriod = KlinePeriod.D1_YEAR,
) -> KlineSeries:
    start = dt.datetime(2026, 1, 5, hour, 0, 0)
    bars = tuple(
        Bar(
            ts=start + dt.timedelta(days=index),
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
        for index, close in enumerate(closes)
    )
    return KlineSeries(
        symbol=_symbol(
            code=code,
            name=name,
            asset_class=AssetClass.CRYPTO if exchange == "OKX" else AssetClass.ETF,
            exchange=exchange,
            provider_symbol=code,
            sec_type=sec_type,
        ),
        period=period,
        bars=bars,
        adjusted=False,
    )


def test_bjt_sessions_and_overnight_anchor() -> None:
    """美股/美期夏冬令时 BJT 会话与跨午夜锚定。"""
    assert get_market_sessions(Market.US_FUTURE, dt.datetime(2026, 7, 24, 12, 0))[0] == ("06:00", "05:00")
    assert get_market_sessions(Market.US_FUTURE, dt.datetime(2026, 1, 15, 12, 0))[0] == ("07:00", "06:00")
    assert get_market_sessions(Market.US_STOCK, dt.datetime(2026, 8, 24, 12, 0))[0] == ("21:30", "04:00")
    assert get_market_sessions(Market.US_STOCK, dt.datetime(2026, 1, 15, 12, 0))[0] == ("22:30", "05:00")
    assert get_market_sessions(Market.US_BOND, dt.datetime(2026, 8, 24, 10, 0))[0] == ("06:00", "05:00")

    now = dt.datetime(2026, 7, 24, 1, 5)
    assert get_session_anchor_date("103.NQ00Y", now) == dt.date(2026, 7, 23)
    arr = get_trading_datetimes_bjt("103.NQ00Y", now)
    assert arr[0] == dt.datetime(2026, 7, 23, 6, 0)
    assert arr[-1] == dt.datetime(2026, 7, 24, 5, 0)
    assert is_market_active_now("103.NQ00Y", now) is True
    assert get_session_anchor_date("103.NQ00Y", dt.datetime(2026, 7, 24, 12, 0)) == dt.date(2026, 7, 24)

    winter = dt.datetime(2026, 1, 15, 1, 5)
    warr = get_trading_datetimes_bjt("103.NQ00Y", winter)
    assert warr[0] == dt.datetime(2026, 1, 14, 7, 0)
    assert warr[-1] == dt.datetime(2026, 1, 15, 6, 0)


def test_a_share_window_and_future_nan_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 股午休窗口与盘中 future 空点保留（X 轴延伸到 15:00）。"""
    morning = dt.datetime(2026, 8, 3, 9, 34)
    lunch = dt.datetime(2026, 8, 3, 12, 0)
    after = dt.datetime(2026, 8, 3, 16, 0)
    assert is_within_trading_day_window("1.600000", morning) is True
    assert is_market_active_now("1.600000", morning) is True
    assert is_within_trading_day_window("1.600000", lunch) is True
    assert is_market_active_now("1.600000", lunch) is False
    assert is_within_trading_day_window("1.600000", after) is False

    trends = [{"datetime": f"2026-08-03 09:{m:02d}", "price": 10.0 + m * 0.01, "money": 100.0} for m in range(31, 35)]
    resolved = _resolve_trend_absolute_datetimes(trends, now_bjt=morning)
    rows = _rows_from_resolved_trends(
        resolved,
        code_id="1.600000",
        now_bjt=morning,
        fill_session_future=True,
        fill_session_gaps=True,
    )
    assert any(r.get("price") is None for r in rows)
    assert max(pd.Timestamp(str(r["datetime"])) for r in rows).hour == 15

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
            return morning.replace(tzinfo=tz) if tz is not None else morning

    monkeypatch.setattr(dt, "datetime", _FixedDateTime)
    series = _series_from_trends("测试股", "600000", "1.600000", trends, prev_close=10.0, change_pct=0.5)
    result = build_single_stock_render_data(series)
    assert not isinstance(result, str), result
    assert bool(result.df["price"].isna().any())
    max_dt = result.df["dt"].max()
    assert max_dt.hour == 15 and max_dt.minute == 0
    assert result.df["price"].notna().sum() == 4


def test_overnight_datetime_never_remaps_history() -> None:
    """HH:MM 序列回绕与完整时间戳透传均不得把历史点甩到次日。"""
    hhmm_trends: list[dict[str, object]] = []
    for h in list(range(6, 24)) + [0, 1]:
        for m in range(60):
            if h == 1 and m > 0:
                break
            hhmm_trends.append({"datetime": f"{h:02d}:{m:02d}", "price": float(h * 60 + m)})
    resolved = _resolve_trend_absolute_datetimes(hhmm_trends, now_bjt=dt.datetime(2026, 7, 24, 1, 5))
    assert resolved[0][1] == dt.datetime(2026, 7, 23, 6, 0)
    assert resolved[-1][1] == dt.datetime(2026, 7, 24, 1, 0)

    abs_trends = [
        {"datetime": "2026-07-23 06:00", "price": 100.0, "money": 1},
        {"datetime": "2026-07-23 12:00", "price": 101.0, "money": 1},
        {"datetime": "2026-07-24 01:00", "price": 99.0, "money": 1},
    ]
    abs_resolved = _resolve_trend_absolute_datetimes(abs_trends, now_bjt=dt.datetime(2026, 7, 24, 1, 5))
    rows = _rows_from_resolved_trends(
        abs_resolved,
        code_id="103.NQ00Y",
        now_bjt=dt.datetime(2026, 7, 24, 1, 5),
        fill_session_future=True,
        fill_session_gaps=False,
    )
    priced = [pd.Timestamp(str(row["datetime"])) for row in rows if row.get("price") is not None]
    assert priced == [
        dt.datetime(2026, 7, 23, 6, 0),
        dt.datetime(2026, 7, 23, 12, 0),
        dt.datetime(2026, 7, 24, 1, 0),
    ]
    assert all(t.date() != dt.date(2026, 7, 25) for t in priced)


def test_multi_stock_overnight_not_shifted() -> None:
    """多股同框混排日盘与跨天夜盘时，对齐只看绝对时间。"""
    day_session = [
        {"datetime": f"2026-07-23 {h:02d}:{m:02d}", "price": 10.0 + h * 0.01, "money": 100.0}
        for h in range(9, 12)
        for m in (0, 30)
    ]
    overnight = [
        {
            "datetime": f"{'2026-07-23' if h >= 18 else '2026-07-24'} {h:02d}:{m:02d}",
            "price": 100.0 - h * 0.1,
            "money": 50.0,
        }
        for h in list(range(18, 24)) + [0, 1]
        for m in (0, 30)
        if not (h == 1 and m > 0)
    ]
    result = build_multi_stock_render_data(
        [
            _series_from_trends("甲", "600000", "1.600000", day_session, prev_close=10.0, change_pct=1.0),
            _series_from_trends("乙", "FAKE", "103.FAKE", overnight, prev_close=100.0, change_pct=-1.0),
        ]
    )
    assert not isinstance(result, str), result
    overnight_checked = False
    for stock in result.stocks:
        priced = stock.df.loc[stock.df["price"].notna(), "dt"]
        assert priced.max() < dt.datetime(2026, 7, 25)
        if priced.min().date() < priced.max().date():
            overnight_checked = True
            assert set(priced[priced.dt.hour >= 18].dt.date.astype(str)) == {"2026-07-23"}
            assert set(priced[priced.dt.hour < 5].dt.date.astype(str)) == {"2026-07-24"}
            assert len(priced[(priced.dt.date == dt.date(2026, 7, 24)) & (priced.dt.hour >= 6)]) == 0
    assert overnight_checked


def test_five_day_intraday_parse_and_render() -> None:
    """五日分时命令识别、开盘 0 价回退与逐日完整轴渲染。"""
    assert parse_stock_img_request("五日 贵州茅台") == ("贵州茅台", "single-stock-ndays-5")
    assert parse_stock_img_request("5天茅台") == ("茅台", "single-stock-ndays-5")
    assert parse_stock_img_request("日k 贵州茅台") == ("贵州茅台", "single-stock-kline-101")
    assert is_intraday_sector("single-stock-ndays-5") and is_intraday_sector("single-stock")
    assert not is_intraday_sector("single-stock-kline-101")
    assert intraday_ndays_from_sector("single-stock-ndays-5") == 5
    assert intraday_ndays_from_sector("single-stock") == 1

    point = parse_trend_line("2026-08-26 09:30,0.00,3881.74,3881.74,3881.74,100,200,3888.25")
    assert point is not None and point.price == 3881.74 and point.open == 3881.74 and point.avg_price == 3888.25

    parsed = parse_intraday_from_trends_list(
        [
            "2026-08-26 09:30,0.00,3881.74,3881.74,3881.74,1,1,3888.25",
            "2026-08-26 15:00,3890.00,3890.10,3890.10,3889.90,1,1,3885.00",
            "2026-09-01 15:00,3979.65,3979.89,3979.89,3979.65,1,1,3992.38",
        ],
        _symbol(),
        ndays=5,
    )
    assert not is_market_error(parsed) and parsed.ndays == 5

    days = ("2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01")
    points: list[IntradayPoint] = []
    price = 3880.0
    for day in days:
        for hm, bump in (("09:31", 1.0), ("10:00", 2.0), ("14:00", -1.0)):
            price += bump
            points.append(
                IntradayPoint(
                    ts=dt.datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M"),
                    price=price,
                    open=price,
                    high=price,
                    low=price,
                    volume=100.0,
                    amount=1000.0,
                    avg_price=price,
                )
            )
    sym = _symbol()
    result = build_single_stock_render_data(
        IntradaySeries(symbol=sym, points=tuple(points), quote=_quote(sym, price), ndays=5)
    )
    assert not isinstance(result, str), result
    assert result.ndays == 5 and len(result.day_starts) == 5 and len(result.day_tick_labels) == 5
    dates = sorted({ts.date() for ts in result.df["dt"] if ts is not None})
    assert len(dates) == 5
    first_day = result.df[result.df["dt"].dt.date == dates[0]]
    last_day = result.df[result.df["dt"].dt.date == dates[-1]]
    assert first_day["dt"].min().hour == 9 and first_day["dt"].max().hour == 15
    assert len(first_day) == len(last_day)
    assert "五日累计" in result.title_text and "今日" in result.title_text and abs(result.today_change - 0.5) < 1e-9


def test_cross_market_compare_aligns_on_calendar_day() -> None:
    """OKX 日线按日历日对齐 A 股，分钟 K 线保留时分。"""
    daily_crypto = _kline_series(
        name="BTC-USDT",
        code="BTC-USDT",
        exchange="OKX",
        sec_type="加密货币",
        closes=[100.0 + i for i in range(12)],
        hour=8,
    )
    minute_crypto = _kline_series(
        name="BTC-USDT",
        code="BTC-USDT",
        exchange="OKX",
        sec_type="加密货币",
        closes=[100.0, 110.0],
        hour=8,
        period=KlinePeriod.M5,
    )
    assert list(kline_to_df(daily_crypto)["date"])[:2] == ["2026-01-05", "2026-01-06"]
    assert list(kline_to_df(minute_crypto)["date"]) == ["2026-01-05 08:00", "2026-01-06 08:00"]

    etf = _kline_series(
        name="沪深300ETF",
        code="510300",
        exchange="SSE",
        sec_type="ETF",
        closes=[4.0 + i * 0.01 for i in range(12)],
        hour=0,
    )
    data = build_compare_render_data([daily_crypto, etf])
    assert not isinstance(data, str)
    assert {p.date() for p in data.items[0].df["日期"]} == {p.date() for p in data.items[1].df["日期"]}

    page = prepare_compare([daily_crypto, etf])
    assert page != ErroText["notData"] and not isinstance(page, str)
    assert len(page.panels[0].series) == 2 and len(page.legend) == 2
