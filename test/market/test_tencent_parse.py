"""腾讯适配器离线解析测试（payload 来自真实接口采样）。"""

from __future__ import annotations

from datetime import date, datetime

from SayuStock.utils.market.enums import AssetClass, KlinePeriod
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import Quote, SymbolRef
from SayuStock.utils.market.adapters.tencent.parse import (
    parse_qt_line,
    parse_qt_line_fx,
    parse_qt_line_hf,
    parse_qt_line_hk,
    parse_qt_line_us,
    tencent_quote_only,
    parse_kline_payload,
    parse_minute_payload,
    tencent_symbol_from_secid,
)
from SayuStock.utils.market.adapters.tencent.client import _QT_RE

# qt.gtimg.cn q=sh600519（2026-09-18 收盘后采样，~ 分隔）
QT_600519 = "~".join(
    [
        "1",  # 0
        "贵州茅台",  # 1 名称
        "600519",  # 2 代码
        "1257.12",  # 3 现价
        "1266.98",  # 4 昨收
        "1262.99",  # 5 今开
        "24891",  # 6 成交量(手)
        "12061",  # 7 外盘
        "12829",  # 8 内盘
    ]
    + ["1257.12", "8", "1257.11", "2", "1257.08", "1", "1257.06", "2", "1257.05", "2"]  # 买五 9-18
    + ["1257.13", "1", "1257.24", "2", "1257.28", "1", "1258.00", "16", "1258.28", "1"]  # 卖五 19-28
    + [
        "",  # 29
        "20260918161436",  # 30 时间
        "-9.86",  # 31 涨跌
        "-0.78",  # 32 涨跌%
        "1265.88",  # 33 最高
        "1256.10",  # 34 最低
        "1257.12/24891/3135849108",  # 35
        "24891",  # 36 量(手)
        "313585",  # 37 额(万)
        "0.20",  # 38 换手%
        "19.30",  # 39 PE
        "",  # 40
        "1265.88",  # 41
        "1256.10",  # 42
        "0.77",  # 43 振幅
        "15715.03",  # 44 流通市值(亿)
        "15715.03",  # 45 总市值(亿)
        "6.25",  # 46 PB
        "1393.68",  # 47 涨停
        "1140.28",  # 48 跌停
    ]
)


# qt.gtimg.cn q=usBABA（2026-09-23 美股收盘后采样；量单位为股、额为美元）
QT_US_BABA = "~".join(
    [
        "200",  # 0
        "阿里巴巴",  # 1 名称
        "BABA.N",  # 2 代码.交易所后缀
        "110.80",  # 3 现价
        "116.31",  # 4 昨收
        "112.00",  # 5 今开
        "12223741",  # 6 成交量(股)
        "0",  # 7
        "0",  # 8
    ]
    + ["110.95", "700"]
    + ["0"] * 8  # 9-10 买一 / 11-18
    + ["111.01", "200"]
    + ["0"] * 8  # 19-20 卖一 / 21-28
    + [
        "",  # 29
        "2026-09-23 16:07:51",  # 30 时间(美东)
        "-5.51",  # 31 涨跌
        "-4.74",  # 32 涨跌%
        "112.28",  # 33 最高
        "110.59",  # 34 最低
        "USD",  # 35 币种
        "12223741",  # 36 量(股)
        "1359611049",  # 37 额(美元)
        "0.49",  # 38 换手%
        "25.33",  # 39 PE
        "",  # 40
        "17.38",  # 41 振幅
        "1:8",  # 42
        "1.45",  # 43 PB
        "2705.65972",  # 44 流通市值(亿美元)
        "2754.07097",  # 45 总市值(亿美元)
        "Alibaba Group Holding Ltd",  # 46 英文名
        "4.37",  # 47
        "191.62",  # 48 52周高
        "91.99",  # 49 52周低
    ]
)


def _sym(secid: str = "1.600519", name: str = "贵州茅台") -> SymbolRef:
    return SymbolRef(
        code=secid.split(".")[-1],
        name=name,
        asset_class=AssetClass.EQUITY,
        exchange="SSE",
        provider_symbol=secid,
        sec_type="沪A",
    )


def _sym_us(secid: str = "106.BABA", name: str = "阿里巴巴") -> SymbolRef:
    return SymbolRef(
        code=secid.split(".")[-1],
        name=name,
        asset_class=AssetClass.EQUITY,
        exchange="US",
        provider_symbol=secid,
        sec_type="美股",
    )


def test_tencent_symbol_from_secid() -> None:
    assert tencent_symbol_from_secid("1.600519") == "sh600519"
    assert tencent_symbol_from_secid("0.000001") == "sz000001"
    # 北交所：secid 前缀同为 0.，但腾讯行情用 bj 符号（sz 返回空值）
    assert tencent_symbol_from_secid("0.920000") == "bj920000"
    assert tencent_symbol_from_secid("0.830799") == "bj830799"
    # 美股：纳斯达克/纽交所/美交所/粉单 → us 前缀（仅盘口可用）
    assert tencent_symbol_from_secid("105.AAPL") == "usAAPL"
    assert tencent_symbol_from_secid("106.BABA") == "usBABA"
    assert tencent_symbol_from_secid("107.SPY") == "usSPY"
    assert tencent_symbol_from_secid("153.TCEHY") == "usTCEHY"
    # 美股指数：代码与东财不同名（东财 NDX 实为纳指综合 → IXIC）
    assert tencent_symbol_from_secid("100.SPX") == "usINX"
    assert tencent_symbol_from_secid("100.DJIA") == "usDJI"
    assert tencent_symbol_from_secid("100.NDX") == "usIXIC"
    assert tencent_symbol_from_secid("100.RUT") is None
    assert tencent_symbol_from_secid("100.HSI") == "hkHSI"
    assert tencent_symbol_from_secid("122.XAU") == "hf_XAU"
    assert tencent_symbol_from_secid("122.XAG") == "hf_XAG"
    assert tencent_symbol_from_secid("102.CL00Y") == "hf_CL"
    assert tencent_symbol_from_secid("109.LCPT") == "hf_CAD"
    assert tencent_symbol_from_secid("119.USDJPY") == "fxUSDJPY"
    assert tencent_symbol_from_secid("119.USDCHF") == "fxUSDCHF"
    assert tencent_symbol_from_secid("100.N225") is None
    assert tencent_symbol_from_secid("100.UDI") is None
    assert tencent_symbol_from_secid("133.USDCNH") is None
    assert tencent_symbol_from_secid("171.CN10Y") is None
    assert tencent_quote_only("122.XAU")
    assert not tencent_quote_only("1.600519")
    # 港股个股仍不覆盖；恒生指数走 hkHSI。
    assert tencent_symbol_from_secid("116.00700") is None
    assert tencent_symbol_from_secid("600519") is None


def test_qt_regex_keeps_underscore_symbols() -> None:
    text = 'v_hf_XAU="4190,伦敦金";v_sh600519="1~茅台";'
    found = {m.group("sym") for m in _QT_RE.finditer(text)}
    assert found == {"hf_XAU", "sh600519"}


def test_parse_all_weather_tencent_aliases() -> None:
    """2026-10-09 腾讯全天候符号实采。"""
    hk = (
        "100~恒生指数~HSI~24210.940~23785.790~23941.260~15031411~0~0~24210.940"
        "~0~0~0~0~0~0~0~0~0~24210.940~0~0~0~0~0~0~0~0~0~0.0"
        "~2026/10/09 14:15:52~425.150~1.79~24224.050~23941.260"
    )
    hk_q = parse_qt_line_hk(hk, symbol=_sym_us("100.HSI", "恒生指数"))
    assert isinstance(hk_q, Quote)
    assert hk_q.symbol.name == "恒生指数"
    assert hk_q.price == 24210.940
    assert hk_q.change_pct == 1.79
    assert hk_q.high == 24224.050
    assert hk_q.volume is None
    assert hk_q.as_of == datetime(2026, 10, 9, 14, 15, 52)

    xau = "4190.83,1.39,4190.83,4191.18,4207.46,4130.90,14:29:00,4133.46,4134.26,0,0,0,2026-10-09,伦敦金（现货黄金）"
    xau_q = parse_qt_line_hf(xau, symbol=_sym_us("122.XAU", "XAU"))
    assert isinstance(xau_q, Quote)
    assert xau_q.symbol.name == "伦敦金（现货黄金）"
    assert xau_q.price == 4190.83
    assert xau_q.prev_close == 4133.46
    assert xau_q.high == 4207.46
    assert xau_q.change_pct == 1.388
    assert xau_q.as_of == datetime(2026, 10, 9, 14, 29)

    fx = (
        "310~美元日元~USDJPY~158.2000~0~20261009143020~157.8200~157.8200~158.2100"
        "~157.7500~158.1900~158.2000~0.3800~0.24~0.58"
    )
    fx_q = parse_qt_line_fx(fx, symbol=_sym_us("119.USDJPY", "美元兑日元"))
    assert isinstance(fx_q, Quote)
    assert fx_q.symbol.name == "美元日元"
    assert fx_q.price == 158.2
    assert fx_q.prev_close == 157.82
    assert fx_q.change_pct == 0.24
    assert fx_q.high == 158.21
    assert fx_q.low == 157.75
    assert fx_q.as_of == datetime(2026, 10, 9, 14, 30, 20)


def test_parse_qt_line() -> None:
    q = parse_qt_line(QT_600519, symbol=_sym())
    assert not is_market_error(q)
    assert q.symbol.name == "贵州茅台"
    assert q.price == 1257.12
    assert q.prev_close == 1266.98
    assert q.open == 1262.99
    assert q.high == 1265.88
    assert q.low == 1256.10
    assert q.change_pct == -0.78
    assert q.change_amount == -9.86
    assert q.volume == 2489100.0  # 手 → 股
    assert q.amount == 313585 * 10000.0  # 万 → 元
    assert q.turnover_rate == 0.20
    assert q.pe == 19.30
    assert q.pb == 6.25
    assert q.market_cap == 15715.03 * 1e8
    assert q.float_market_cap == 15715.03 * 1e8
    assert q.limit_up == 1393.68
    assert q.limit_down == 1140.28
    assert q.as_of is not None and q.as_of.strftime("%Y%m%d%H%M%S") == "20260918161436"


def test_parse_qt_line_us() -> None:
    q = parse_qt_line_us(QT_US_BABA, symbol=_sym_us())
    assert not is_market_error(q)
    assert q.symbol.name == "阿里巴巴"
    assert q.price == 110.80
    assert q.prev_close == 116.31
    assert q.open == 112.00
    assert q.high == 112.28
    assert q.low == 110.59
    assert q.change_pct == -4.74
    assert q.change_amount == -5.51
    # 美股量额单位与东财一致（股/美元），不做手/万换算
    assert q.volume == 12223741.0
    assert q.amount == 1359611049.0
    assert q.turnover_rate == 0.49
    assert q.pe == 25.33
    assert q.pb == 1.45  # 美股 PB 在 43 列（A 股在 46 列）
    assert q.market_cap == 2754.07097 * 1e8
    assert q.float_market_cap == 2705.65972 * 1e8
    # 美股无涨跌停；as_of 为美东时间，与东财主源口径一致置空
    assert q.limit_up is None
    assert q.limit_down is None
    assert q.as_of is None


def test_parse_kline_payload_prefers_qfq_then_plain() -> None:
    # 个股：qfqday
    payload = {
        "code": 0,
        "data": {
            "sh600519": {
                "qfqday": [
                    ["2026-09-17", "1257.980", "1266.980", "1267.600", "1254.000", "17554.000"],
                    ["2026-09-18", "1262.99", "1257.12", "1265.88", "1256.10", "24891"],
                ]
            }
        },
    }
    series = parse_kline_payload(
        payload, symbol=_sym(), tencent_symbol="sh600519", period=KlinePeriod.D1, unit="day", adjusted=True
    )
    assert not is_market_error(series)
    assert series.adjusted is True
    assert len(series.bars) == 2
    # 注意腾讯列序：开、收、高、低
    assert series.bars[-1].open == 1262.99
    assert series.bars[-1].close == 1257.12
    assert series.bars[-1].high == 1265.88
    assert series.bars[-1].low == 1256.10
    assert series.bars[-1].volume == 24891 * 100.0
    assert series.bars[-1].ts.date() == date(2026, 9, 18)

    # 指数：无 qfq 前缀，回退 day/week 键
    index_payload = {
        "code": 0,
        "data": {
            "sh000001": {
                "week": [
                    ["2026-09-11", "3942.510", "3888.110", "3958.120", "3852.030", "2588670519.000"],
                ]
            }
        },
    }
    series_w = parse_kline_payload(
        index_payload,
        symbol=_sym("1.000001", "上证指数"),
        tencent_symbol="sh000001",
        period=KlinePeriod.W1,
        unit="week",
        adjusted=True,
    )
    assert not is_market_error(series_w)
    assert series_w.bars[0].close == 3888.110


def test_parse_minute_payload_diffs_cumulative() -> None:
    payload = {
        "code": 0,
        "data": {
            "sh600519": {
                "data": {"data": ["0930 1262.99 113 14271787.32", "0931 1259.18 642 81120790.58"]},
                "qt": {},
            }
        },
    }
    series = parse_minute_payload(
        payload, symbol=_sym(), tencent_symbol="sh600519", quote=None, trade_date="2026-09-18"
    )
    assert not is_market_error(series)
    assert len(series.points) == 2
    p0, p1 = series.points
    assert p0.ts.strftime("%H:%M") == "09:30"
    assert p0.price == 1262.99
    assert p0.open == 1262.99
    assert p0.volume == 113 * 100.0  # 首点累计量差分 = 全量
    assert p0.amount == 14271787.32
    # 第二分钟：量额取累计差分
    assert p1.volume == (642 - 113) * 100.0
    assert p1.amount == 81120790.58 - 14271787.32
    assert p1.high == 1262.99 and p1.low == 1259.18
    # 均价 = 累计额 / 累计量(股)
    assert p1.avg_price == 81120790.58 / (642 * 100.0)
