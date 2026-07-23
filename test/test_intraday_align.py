"""跨天分时时间轴对齐回归测试 —— 不依赖标的中文名/昵称特判。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from SayuStock.utils.render_data import (
    build_multi_stock_render_data,
    _resolve_trend_absolute_datetimes,
    _rows_from_resolved_trends,
)
from SayuStock.utils.time_range import (
    MARKET_SESSIONS,
    Market,
    get_session_anchor_date,
    get_trading_datetimes_bjt,
    is_market_active_now,
)

DATA = Path(r"F:\gsuid_core\data\SayuStock\data")
CACHE_FILES = [
    "100.KS11_single-stock_None_data.json",
    "116.07709_single-stock_None_data.json",
    "103.NQ00Y_single-stock_None_data.json",
    "103.ES00Y_single-stock_None_data.json",
]


def test_us_future_session_is_bjt_not_et() -> None:
    sess = MARKET_SESSIONS[Market.US_FUTURE]
    assert sess[0][0] in {"06:00", "07:00"}
    assert sess[0][1] in {"05:00", "06:00"}
    assert sess[0] != ("18:00", "17:00")


def test_session_anchor_after_midnight() -> None:
    """跨天会话过 0 点后，锚定开盘日（用 secid，不是名字）。"""
    now = dt.datetime(2026, 7, 24, 1, 5)
    assert get_session_anchor_date("103.NQ00Y", now) == dt.date(2026, 7, 23)
    arr = get_trading_datetimes_bjt("103.NQ00Y", now)
    assert arr[0] == dt.datetime(2026, 7, 23, 6, 0)
    assert arr[-1] == dt.datetime(2026, 7, 24, 5, 0)
    assert is_market_active_now("103.NQ00Y", now) is True

    midday = dt.datetime(2026, 7, 24, 12, 0)
    assert get_session_anchor_date("103.NQ00Y", midday) == dt.date(2026, 7, 24)


def test_resolve_hhmm_overnight_anchor() -> None:
    """仅 HH:MM：按序列回绕 + 墙钟锚定，与品种名无关。"""
    trends: list[dict[str, object]] = []
    for h in list(range(6, 24)) + [0, 1]:
        for m in range(60):
            if h == 1 and m > 0:
                break
            trends.append({"datetime": f"{h:02d}:{m:02d}", "price": float(h * 60 + m)})

    resolved = _resolve_trend_absolute_datetimes(
        trends, now_bjt=dt.datetime(2026, 7, 24, 1, 5)
    )
    assert resolved[0][1] == dt.datetime(2026, 7, 23, 6, 0)
    assert resolved[-1][1] == dt.datetime(2026, 7, 24, 1, 0)


def test_resolve_full_datetime_passthrough() -> None:
    trends = [
        {"datetime": "2026-07-23 06:00", "price": 1.0},
        {"datetime": "2026-07-24 01:00", "price": 2.0},
    ]
    resolved = _resolve_trend_absolute_datetimes(
        trends, now_bjt=dt.datetime(2026, 7, 24, 1, 5)
    )
    assert [ts.strftime("%Y-%m-%d %H:%M") for _, ts in resolved] == [
        "2026-07-23 06:00",
        "2026-07-24 01:00",
    ]


def test_rows_never_remap_history_via_wrong_session() -> None:
    """即使会话模板是「今天 18:00 起」的错误配置，历史点也必须留在原始绝对时间。"""
    trends = [
        {"datetime": "2026-07-23 06:00", "price": 100.0, "money": 1},
        {"datetime": "2026-07-23 12:00", "price": 101.0, "money": 1},
        {"datetime": "2026-07-24 01:00", "price": 99.0, "money": 1},
    ]
    resolved = _resolve_trend_absolute_datetimes(
        trends, now_bjt=dt.datetime(2026, 7, 24, 1, 5)
    )
    rows = _rows_from_resolved_trends(
        resolved,
        code_id="103.NQ00Y",
        now_bjt=dt.datetime(2026, 7, 24, 1, 5),
        fill_session_future=True,
        fill_session_gaps=False,
    )
    priced = [
        pd_ts
        for row in rows
        if row.get("price") is not None
        for pd_ts in [__import__("pandas").Timestamp(row["datetime"])]
    ]
    assert priced[0] == dt.datetime(2026, 7, 23, 6, 0)
    assert priced[1] == dt.datetime(2026, 7, 23, 12, 0)
    assert priced[2] == dt.datetime(2026, 7, 24, 1, 0)
    # 绝不出现「次日 06:00/12:00」这种被模板甩过去的幽灵点
    assert all(not (t.date() == dt.date(2026, 7, 25)) for t in priced)
    assert all(not (t.date() == dt.date(2026, 7, 24) and t.hour in {6, 12}) for t in priced)


def test_multi_stock_synthetic_overnight_not_shifted() -> None:
    """合成跨天分时 + 日盘：对齐只看绝对时间，不看 f58 名字。"""
    day_session = [
        {
            "datetime": f"2026-07-23 {h:02d}:{m:02d}",
            "price": 10.0 + h * 0.01,
            "money": 100.0,
        }
        for h in range(9, 12)
        for m in (0, 30)
    ]
    overnight = []
    for h in list(range(18, 24)) + [0, 1]:
        for m in (0, 30):
            if h == 1 and m > 0:
                break
            day = "2026-07-23" if h >= 18 else "2026-07-24"
            overnight.append(
                {
                    "datetime": f"{day} {h:02d}:{m:02d}",
                    "price": 100.0 - h * 0.1,
                    "money": 50.0,
                }
            )

    raw_list = [
        {
            "file_name": "1.600000_single-stock_None_data.json",
            "data": {"f58": "甲", "f60": 10.0, "f170": 1.0, "f48": 1.0, "f43": 10.1, "f168": 1.0},
            "trends": day_session,
        },
        {
            "file_name": "103.FAKE_single-stock_None_data.json",
            "data": {"f58": "乙", "f60": 100.0, "f170": -1.0, "f48": 1.0, "f43": 99.0, "f168": 1.0},
            "trends": overnight,
        },
    ]
    result = build_multi_stock_render_data(raw_list)
    assert not isinstance(result, str), result
    assert len(result.stocks) == 2

    overnight_checked = False
    for stock in result.stocks:
        priced = stock.df.loc[stock.df["price"].notna(), "dt"]
        assert priced.max() < dt.datetime(2026, 7, 25)
        # 结构判定跨午夜序列：不看中文名
        if priced.min().date() < priced.max().date():
            overnight_checked = True
            evening = priced[priced.dt.hour >= 18]
            late = priced[priced.dt.hour < 5]
            assert set(evening.dt.date.astype(str)) == {"2026-07-23"}
            assert set(late.dt.date.astype(str)) == {"2026-07-24"}
            day_side = priced[(priced.dt.date == dt.date(2026, 7, 24)) & (priced.dt.hour >= 6)]
            assert len(day_side) == 0
    assert overnight_checked


@pytest.mark.skipif(
    not all((DATA / f).exists() for f in CACHE_FILES),
    reason="本地缓存 JSON 不存在",
)
def test_multi_stock_cache_prefix_103_overnight() -> None:
    """真实缓存：用 file_name 前缀 103. 识别美期，不用中文名。"""
    raw_list = [json.loads((DATA / f).read_text(encoding="utf-8")) for f in CACHE_FILES]
    for raw in raw_list:
        file_name = str(raw.get("file_name", ""))
        secid = file_name.split("_")[0]
        # 统一：按 trends 序列本身还原日期（不按品种名分支不同 now）
        # 缓存采集于 7/24 ~01:00 会话；给一个覆盖「末点 ≤ now」的 now 即可
        now = dt.datetime(2026, 7, 24, 1, 5)
        stamped = _resolve_trend_absolute_datetimes(raw["trends"], now_bjt=now)
        # 若是日盘 HH:MM 且 last_clock > 01:05，会锚到 7/23 —— 正是我们要的
        raw["trends"] = [
            {**item, "datetime": ts.strftime("%Y-%m-%d %H:%M")} for item, ts in stamped
        ]
        raw["_secid"] = secid

    result = build_multi_stock_render_data(raw_list)
    assert not isinstance(result, str), result

    for stock in result.stocks:
        priced = stock.df.loc[stock.df["price"].notna(), "dt"]
        assert len(priced) > 0
        assert priced.max() < dt.datetime(2026, 7, 25)

    # 任一跨越午夜的序列：上午段与午夜后必须分属相邻两天
    crossed = False
    for stock in result.stocks:
        priced = stock.df.loc[stock.df["price"].notna(), "dt"]
        if priced.min().date() < priced.max().date():
            crossed = True
            morning = priced[(priced.dt.hour >= 6) & (priced.dt.hour < 18)]
            late = priced[priced.dt.hour < 5]
            assert morning.min().date() == priced.min().date()
            if len(late):
                assert late.max().date() == priced.max().date()
            # 关键：6–17 点不得出现在 max 日（那就是「甩到次日」）
            bad = morning[morning.dt.date == priced.max().date()]
            assert len(bad) == 0, f"{stock.name} morning leaked to end day"
    assert crossed, "缓存里应至少有一条跨午夜序列"
