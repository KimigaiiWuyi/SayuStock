"""新浪适配器离线解析测试（payload 来自真实接口采样）。"""

from __future__ import annotations

from datetime import date

from SayuStock.utils.market.enums import RankBy, BoardKind, AssetClass, KlinePeriod
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import SymbolRef
from SayuStock.utils.market.adapters.sina.parse import (
    industry_menu,
    parse_hq_line,
    parse_rank_rows,
    parse_kline_rows,
    parse_node_board,
    node_for_industry,
    parse_minline_rows,
    parse_industry_summary,
    sina_symbol_from_secid,
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
    "485712507,994169450166,"
    + ",".join(["0"] * 20)
    + ",2026-09-18,15:43:32,00"
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


def test_sina_symbol_from_secid() -> None:
    assert sina_symbol_from_secid("1.600519") == "sh600519"
    assert sina_symbol_from_secid("0.000001") == "sz000001"
    assert sina_symbol_from_secid("1.000001") == "sh000001"
    assert sina_symbol_from_secid("116.00700") is None
    assert sina_symbol_from_secid("600519") is None


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
