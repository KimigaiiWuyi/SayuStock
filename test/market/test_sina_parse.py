"""新浪适配器离线解析测试（payload 来自真实接口采样）。"""

from __future__ import annotations

from datetime import date, datetime

from SayuStock.utils.market.enums import RankBy, BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import SymbolRef
from SayuStock.utils.market.adapters.sina.parse import (
    _et_to_bj,
    industry_menu,
    parse_hq_line,
    parse_rank_rows,
    parse_hq_line_us,
    parse_kline_rows,
    parse_node_board,
    node_for_industry,
    parse_minline_rows,
    parse_us_mink_rows,
    parse_us_daily_rows,
    parse_industry_summary,
    parse_us_mink_intraday,
    sina_symbol_from_secid,
    sina_us_mink_symbol_from_secid,
)

# hq.sinajs.cn list=sh600519（2026-09-18 收盘后采样）
HQ_600519 = (
    "贵州茅台,1262.990,1266.980,1257.120,1265.880,1256.100,1257.120,1257.130,"
    "2489087,3135849108.000,831,1257.120,200,1257.110,100,1257.080,200,1257.060,"
    "200,1257.050,100,1257.130,200,1257.240,100,1257.280,1600,1258.000,100,"
    "1258.280,2026-09-18,15:34:59,00"
)

HQ_SH000001 = (
    "上证指数,3891.9608,3875.6044,3911.8714,3919.6691,3888.4959,0,0,"
    "485712507,994169450166," + ",".join(["0"] * 20) + ",2026-09-18,15:43:32,00"
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


def test_sina_symbol_from_secid() -> None:
    assert sina_symbol_from_secid("1.600519") == "sh600519"
    assert sina_symbol_from_secid("0.000001") == "sz000001"
    assert sina_symbol_from_secid("1.000001") == "sh000001"


def test_sina_symbol_from_secid_maps_bse_to_bj_prefix() -> None:
    """北交所 secid 前缀与深市同为 0.，但新浪行情中心用 bj 符号。

    映射成 sz 会让 hq 返回空串 → 被误报 not_found → 短路整条优先级链，
    使北交所股票在东财不可用时彻底取不到价。
    """
    assert sina_symbol_from_secid("0.920000") == "bj920000"
    assert sina_symbol_from_secid("0.830799") == "bj830799"
    assert sina_symbol_from_secid("0.430047") == "bj430047"
    assert sina_symbol_from_secid("0.871981") == "bj871981"
    # 深市普通股票不受影响
    assert sina_symbol_from_secid("0.000001") == "sz000001"
    assert sina_symbol_from_secid("0.300750") == "sz300750"

    # 美股 → gb_ 前缀且必须小写（gb_QQQ 返回空）
    assert sina_symbol_from_secid("105.QQQ") == "gb_qqq"
    assert sina_symbol_from_secid("106.BABA") == "gb_baba"
    assert sina_symbol_from_secid("153.TCEHY") == "gb_tcehy"
    # 美股指数（注意东财 NDX 实为纳斯达克综合 → ixic）
    assert sina_symbol_from_secid("100.SPX") == "gb_inx"
    assert sina_symbol_from_secid("100.DJIA") == "gb_dji"
    assert sina_symbol_from_secid("100.NDX") == "gb_ixic"
    assert sina_symbol_from_secid("100.RUT") is None
    # 港股暂不覆盖
    assert sina_symbol_from_secid("116.00700") is None
    assert sina_symbol_from_secid("600519") is None


def test_sina_us_mink_symbol_from_secid() -> None:
    # 分钟K/日K接口符号与盘口不同：股票裸代码、指数带前导点
    assert sina_us_mink_symbol_from_secid("105.QQQ") == "QQQ"
    assert sina_us_mink_symbol_from_secid("153.TCEHY") == "TCEHY"
    assert sina_us_mink_symbol_from_secid("100.SPX") == ".inx"
    assert sina_us_mink_symbol_from_secid("100.NDX") == ".ixic"
    assert sina_us_mink_symbol_from_secid("1.600519") is None
    assert sina_us_mink_symbol_from_secid("116.00700") is None


def test_et_to_bj_dst_and_rollover() -> None:
    # 夏令时 EDT(+12)：09:31 → 当日 21:31
    assert _et_to_bj(datetime(2026, 9, 23, 9, 31)) == datetime(2026, 9, 23, 21, 31)
    # 夏令时跨日：15:20 → 次日 03:20
    assert _et_to_bj(datetime(2026, 9, 3, 15, 20)) == datetime(2026, 9, 4, 3, 20)
    # 冬令时 EST(+13)：1 月 09:31 → 当日 22:31
    assert _et_to_bj(datetime(2026, 1, 15, 9, 31)) == datetime(2026, 1, 15, 22, 31)


def test_parse_hq_line_stock() -> None:
    q = parse_hq_line(HQ_600519, symbol=_sym())
    assert not is_market_error(q)
    assert q.symbol.name == "贵州茅台"
    assert q.open == 1262.99
    assert q.prev_close == 1266.98
    assert q.price == 1257.12
    assert q.high == 1265.88
    assert q.low == 1256.10
    assert q.volume == 2489087.0
    assert q.amount == 3135849108.0
    assert q.change_pct == round((1257.12 - 1266.98) / 1266.98 * 100, 3)
    assert q.as_of is not None and q.as_of.year == 2026


def test_parse_hq_line_index() -> None:
    q = parse_hq_line(HQ_SH000001, symbol=_sym("1.000001", "上证指数"))
    assert not is_market_error(q)
    assert q.price == 3911.8714
    assert q.prev_close == 3875.6044
    assert q.volume == 485712507.0
    assert q.amount == 994169450166.0
    assert q.change_pct == round((3911.8714 - 3875.6044) / 3875.6044 * 100, 3)


# hq.sinajs.cn list=gb_baba（2026-09-24 采样；量单位为股、额/市值为美元）
HQ_US_BABA = (
    "阿里巴巴,110.8000,-4.74,2026-09-24 16:08:34,-5.5100,112.0000,112.2800,110.5910,"
    "191.6200,91.9900,12223741,9314764,275407096431,6.41,17.290000,0.00,0.00,0.00,0.00,"
    "2485623614,40,112.2500,1.31,1.45,Sep 24 04:08AM EDT,Sep 23 04:03PM EDT,116.3100,"
    "157210,1,2026,1360968048.1589,113.1800,111.9700,17657831.0876,112.0800,110.8000"
)


def test_parse_hq_line_us() -> None:
    q = parse_hq_line_us(HQ_US_BABA, symbol=_sym_us())
    assert not is_market_error(q)
    assert q.symbol.name == "阿里巴巴"
    assert q.price == 110.80
    assert q.prev_close == 116.31  # 26 列
    assert q.open == 112.00
    assert q.high == 112.28
    assert q.low == 110.591
    assert q.change_pct == round((110.80 - 116.31) / 116.31 * 100, 3)  # 按昨收计算
    assert q.change_amount == -5.51
    # 量(10列,股) / 额(30列,美元) / 总市值(12列,美元)，与东财美股口径一致
    assert q.volume == 12223741.0
    assert q.amount == 1360968048.1589
    assert q.market_cap == 275407096431.0
    # 美股时间为美东串，与东财主源口径一致置空
    assert q.as_of is None


# stock.finance.sina.com.cn US_MinKService.getDailyK?symbol=QQQ（2026-09-24 采样）
US_DAILY_QQQ = [
    {"d": "2001-01-02", "o": "58.56", "h": "58.69", "l": "52.44", "c": "53.44", "v": "61893300", "a": "0"},
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


def test_parse_us_daily_rows_tail_and_filter() -> None:
    series = parse_us_daily_rows(US_DAILY_QQQ, symbol=_sym_us("105.QQQ", "纳指100ETF"), period=KlinePeriod.D1, limit=2)
    assert not is_market_error(series)
    assert series.adjusted is False
    # 全量 3 根取尾部 2 根
    assert len(series.bars) == 2
    assert series.bars[-1].close == 741.21
    assert series.bars[-1].ts.date() == date(2026, 9, 23)
    assert series.bars[-1].open == 746.97
    assert series.bars[-1].high == 747.13
    assert series.bars[-1].low == 738.19
    assert series.bars[-1].volume == 33191773.0
    assert series.bars[-1].amount == 24601000000.0

    # start/end 过滤：只剩 2026-09-22 一根
    series_f = parse_us_daily_rows(
        US_DAILY_QQQ,
        symbol=_sym_us("105.QQQ", "纳指100ETF"),
        period=KlinePeriod.D1,
        limit=10,
        start=date(2026, 9, 22),
        end=date(2026, 9, 22),
    )
    assert not is_market_error(series_f)
    assert len(series_f.bars) == 1
    assert series_f.bars[0].close == 747.46


def test_parse_kline_rows_daily_and_minute() -> None:
    daily = [
        {
            "day": "2026-09-17",
            "open": "1257.980",
            "high": "1267.600",
            "low": "1254.000",
            "close": "1266.980",
            "volume": "1755380",
        },
        {
            "day": "2026-09-18",
            "open": "1262.990",
            "high": "1265.880",
            "low": "1256.100",
            "close": "1257.120",
            "volume": "2489087",
        },
    ]
    series = parse_kline_rows(daily, symbol=_sym(), period=KlinePeriod.D1)
    assert not is_market_error(series)
    assert series.period == KlinePeriod.D1
    assert series.adjusted is False
    assert len(series.bars) == 2
    assert series.bars[-1].close == 1257.12
    assert series.bars[-1].ts.date() == date(2026, 9, 18)

    minute = [
        {
            "day": "2026-09-18 14:50:00",
            "open": "1261.630",
            "high": "1262.990",
            "low": "1261.200",
            "close": "1261.700",
            "volume": "75600",
            "amount": "95420887.3555",
        },
    ]
    series_m = parse_kline_rows(minute, symbol=_sym(), period=KlinePeriod.M5)
    assert not is_market_error(series_m)
    assert series_m.bars[0].ts.hour == 14 and series_m.bars[0].ts.minute == 50
    assert series_m.bars[0].amount == 95420887.3555


def test_parse_minline_rows() -> None:
    rows = [
        {"m": "09:25:00", "v": "11332", "p": "1262.99", "avg_p": "1262.99"},
        {"m": "09:30:00", "v": "43000", "p": "1259.19", "avg_p": "1260.923"},
        {"m": "09:31:00", "v": "38700", "p": "1261.37", "avg_p": "1260.937"},
    ]
    series = parse_minline_rows(rows, symbol=_sym(), quote=None, trade_date="2026-09-18")
    assert not is_market_error(series)
    assert series.ndays == 1
    assert len(series.points) == 3
    first = series.points[0]
    assert first.ts.strftime("%H:%M:%S") == "09:25:00"
    assert first.price == 1262.99
    assert first.open == 1262.99  # 首点即开盘
    assert first.volume == 11332.0
    assert first.amount == 11332.0 * 1262.99
    last = series.points[-1]
    assert last.high == 1262.99  # 分钟高点取自开盘后价格
    assert last.low == 1259.19
    assert last.avg_price == 1260.937


def test_parse_node_board_and_rank() -> None:
    rows = [
        {
            "symbol": "sz300308",
            "code": "300308",
            "name": "中际旭创",
            "trade": "926.430",
            "pricechange": 30.43,
            "changepercent": 3.396,
            "settlement": "896.000",
            "open": "910.000",
            "high": "947.600",
            "low": "893.080",
            "volume": 29505475,
            "amount": 27079558745,
            "ticktime": "16:29:45",
            "per": 94.534,
            "pb": 27.378,
            "mktcap": 109125082.87116,  # 万元
            "nmc": 102827893.76893,
            "turnoverratio": 2.6583,
        }
    ]
    board = parse_node_board(rows, kind=BoardKind.A_SHARE, title="沪深A")
    assert not is_market_error(board)
    row = board.rows[0]
    assert row.code == "300308" and row.name == "中际旭创"
    assert row.price == 926.43
    assert row.change_pct == 3.396
    assert row.amount == 27079558745.0
    assert row.market_cap == 109125082.87116 * 10000.0  # 万元 → 元

    rank = parse_rank_rows(rows, rank_by=RankBy.AMOUNT, high_first=True, limit=10)
    assert not is_market_error(rank)
    assert rank.rank_by == "amount"
    assert rank.rows[0].metric == 27079558745.0
    assert rank.rows[0].turnover_pct == 2.6583

    err = parse_rank_rows(rows, rank_by=RankBy.MAIN_INFLOW, high_first=True, limit=10)
    assert is_market_error(err)


def test_parse_industry_summary_and_menu() -> None:
    blhy_row = (
        "new_blhy,玻璃行业,19,17.661578947368,-0.11789473684211,-0.66309463899825,"
        "1003647974,26401819337,sz002623,9.976,18.520,1.680,亚玛顿"
    )
    cbzz_row = (
        "new_cbzz,船舶制造,8,15.054285714286,0.15428571428571,1.0354745925216,"
        "312926974,6270828039,sh601890,1.861,9.850,0.180,亚星锚链"
    )
    payload = {"new_blhy": blhy_row, "new_cbzz": cbzz_row}
    snap = parse_industry_summary(payload, kind=BoardKind.INDUSTRY, title="行业板块")
    assert not is_market_error(snap)
    assert len(snap.rows) == 2
    # 按涨跌幅降序：船舶制造(+1.035) 在前
    assert snap.rows[0].name == "船舶制造"
    assert snap.rows[0].change_pct == 1.0354745925216
    assert snap.rows[0].lead_name == "亚星锚链"
    assert snap.rows[0].lead_change_pct == 1.861
    assert snap.rows[1].name == "玻璃行业"

    menu = industry_menu(payload)
    assert not is_market_error(menu)
    assert menu["玻璃行业"] == "new_blhy"
    assert node_for_industry(menu, "玻璃行业") == "new_blhy"
    assert node_for_industry(menu, "new_blhy") == "new_blhy"
    assert node_for_industry(menu, "不存在的板块") is None


# US_MinKService.getMinK?symbol=QQQ&type=5（2026-09-24 采样；时间戳为美东，跨日 bar 采样于头部）
US_MINK5_QQQ = [
    {
        "d": "2026-09-03 15:20:00",
        "o": "717.8200",
        "h": "717.9550",
        "l": "717.6300",
        "c": "717.9550",
        "v": "268138",
        "a": "192449000",
    },
    {
        "d": "2026-09-23 09:35:00",
        "o": "746.9700",
        "h": "747.1300",
        "l": "745.0850",
        "c": "745.0850",
        "v": "745040",
        "a": "555810000",
    },
    {
        "d": "2026-09-23 09:40:00",
        "o": "745.0900",
        "h": "745.8000",
        "l": "744.4500",
        "c": "744.8000",
        "v": "450572",
        "a": "335689000",
    },
]


def test_parse_us_mink_rows() -> None:
    sym = _sym_us("105.QQQ", "纳指100ETF")
    series = parse_us_mink_rows(US_MINK5_QQQ, symbol=sym, period=KlinePeriod.M5, limit=10)
    assert not is_market_error(series)
    assert series.period == KlinePeriod.M5
    assert len(series.bars) == 3
    # 美东 → 北京：15:20 跨日到次日 03:20；09:35 当日 21:35
    assert series.bars[0].ts == datetime(2026, 9, 4, 3, 20)
    assert series.bars[1].ts == datetime(2026, 9, 23, 21, 35)
    # 腾讯/东财同款末时刻标注：09:35 bar 覆盖 09:30-09:35
    assert series.bars[1].open == 746.97
    assert series.bars[1].high == 747.13
    assert series.bars[1].low == 745.085
    assert series.bars[1].close == 745.085
    assert series.bars[1].volume == 745040.0
    assert series.bars[1].amount == 555810000.0

    # start 过滤掉 09-03 的跨日 bar
    series_f = parse_us_mink_rows(US_MINK5_QQQ, symbol=sym, period=KlinePeriod.M5, limit=10, start=date(2026, 9, 23))
    assert not is_market_error(series_f)
    assert len(series_f.bars) == 2

    # limit 取尾部
    series_t = parse_us_mink_rows(US_MINK5_QQQ, symbol=sym, period=KlinePeriod.M5, limit=2)
    assert len(series_t.bars) == 2
    assert series_t.bars[0].ts == datetime(2026, 9, 23, 21, 35)


# US_MinKService.getMinK?symbol=QQQ&type=1（2026-09-24 采样；含前一交易日末 bar）
US_MINK1_QQQ = [
    {
        "d": "2026-09-22 16:00:00",
        "o": "747.1700",
        "h": "747.5900",
        "l": "747.0900",
        "c": "747.4650",
        "v": "597220",
        "a": "446315000",
    },
    {
        "d": "2026-09-23 09:31:00",
        "o": "746.9700",
        "h": "747.1300",
        "l": "745.9200",
        "c": "746.0000",
        "v": "300258",
        "a": "224227000",
    },
    {
        "d": "2026-09-23 09:32:00",
        "o": "746.0500",
        "h": "746.1100",
        "l": "745.4200",
        "c": "745.4980",
        "v": "122022",
        "a": "90995800",
    },
    {
        "d": "2026-09-23 16:00:00",
        "o": "740.8900",
        "h": "741.4200",
        "l": "740.6200",
        "c": "741.1700",
        "v": "542074",
        "a": "401667000",
    },
]


def test_parse_us_mink_intraday_last_session() -> None:
    sym = _sym_us("105.QQQ", "纳指100ETF")
    series = parse_us_mink_intraday(US_MINK1_QQQ, symbol=sym, quote=None, today=date(2026, 9, 24))
    assert not is_market_error(series)
    assert series.ndays == 1
    # 只保留最近一个交易日（09-23），前一交易日（09-22）末 bar 被滤掉
    assert len(series.points) == 3
    p0, p1, p2 = series.points
    # 美东 09:31 → 北京 21:31；16:00 → 次日 04:00
    assert p0.ts == datetime(2026, 9, 23, 21, 31)
    assert p2.ts == datetime(2026, 9, 24, 4, 0)
    assert p0.price == 746.00
    assert p0.open == 746.97
    assert p0.high == 747.13
    assert p0.low == 745.92
    # 逐 bar 量额（非累计）
    assert p0.volume == 300258.0
    assert p0.amount == 224227000.0
    # 均价 = 累计额/累计量
    assert p0.avg_price == 224227000.0 / 300258.0
    assert p1.avg_price == (224227000.0 + 90995800.0) / (300258.0 + 122022.0)
    # 日内高低为累计极值
    assert p1.high == 747.13
    assert p1.low == 745.42
    assert p2.high == 747.13
    assert p2.low == 740.62


def test_parse_us_mink_intraday_stale_guard() -> None:
    # 新浪对 OTC/美股指数的 1 分钟数据停更于 2020 年：超期 → unsupported 回落东财
    stale = [
        {"d": "2020-06-10 15:58:00", "o": "52.20", "h": "52.25", "l": "52.10", "c": "52.15", "v": "1000", "a": "52150"},
        {"d": "2020-06-10 15:59:00", "o": "52.15", "h": "52.20", "l": "52.10", "c": "52.18", "v": "800", "a": "41744"},
    ]
    result = parse_us_mink_intraday(stale, symbol=_sym_us("153.TCEHY"), quote=None, today=date(2026, 9, 24))
    assert is_market_error(result)
    assert result.code == "unsupported"
    # 空数据同样 unsupported
    result_empty = parse_us_mink_intraday([], symbol=_sym_us(), quote=None, today=date(2026, 9, 24))
    assert is_market_error(result_empty)
    assert result_empty.code == "unsupported"


def test_parse_us_mink_intraday_zero_amount_fallback() -> None:
    # 指数（.ixic）type=1 的 a 恒为 0：均价回退到当前价而非 0
    rows = [
        {
            "d": "2026-09-23 09:31:00",
            "o": "26986.31",
            "h": "26986.31",
            "l": "26975.52",
            "c": "26978.42",
            "v": "10759600",
            "a": "0",
        },
        {
            "d": "2026-09-23 09:32:00",
            "o": "26979.23",
            "h": "26981.16",
            "l": "26976.17",
            "c": "26980.54",
            "v": "10938363",
            "a": "0",
        },
    ]
    series = parse_us_mink_intraday(rows, symbol=_sym_us("100.NDX", "纳斯达克"), quote=None, today=date(2026, 9, 24))
    assert not is_market_error(series)
    assert series.points[0].avg_price == 26978.42
    assert series.points[1].avg_price == 26980.54
