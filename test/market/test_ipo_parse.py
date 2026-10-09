"""IPO 日历解析与渲染数据（全部走 fixtures，不打真网）。"""

from __future__ import annotations

import re
import json
import asyncio
from pathlib import Path
from datetime import date

import pytest

from SayuStock.utils.render_data import IpoCalendarRow, build_ipo_calendar_render_data
from SayuStock.stock_ipo.draw_ipo import parse_ipo_market_filter
from SayuStock.utils.market.enums import IpoStage, IpoMarket
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import IpoEvent
from SayuStock.utils.market.adapters.nasdaq.parse import (
    dedupe_ipo_events,
    months_for_window,
    parse_nasdaq_calendar,
)
from SayuStock.utils.market.adapters.aastocks.parse import (
    HkIpoExtra,
    parse_mainpage,
    parse_upcoming,
    merge_hk_ipo_extra,
)
from SayuStock.utils.market.adapters.eastmoney.parse_ipo import (
    parse_ipo_apply_payload,
    parse_ipo_clist_payload,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# -- IpoEvent.stage_on ------------------------------------------------------


def test_stage_on_rules() -> None:
    a = date(2026, 9, 21)
    assert IpoEvent(market=IpoMarket.CN, code="1", name="n", apply_date=date(2026, 9, 28)).stage_on(a) == IpoStage.APPLY
    assert (
        IpoEvent(market=IpoMarket.CN, code="1", name="n", apply_date=date(2026, 9, 28)).stage_on(date(2026, 9, 28))
        == IpoStage.APPLY
    )
    assert (
        IpoEvent(
            market=IpoMarket.CN, code="1", name="n", apply_date=date(2026, 9, 8), listing_date=date(2026, 9, 28)
        ).stage_on(a)
        == IpoStage.PENDING
    )
    assert (
        IpoEvent(market=IpoMarket.HK, code="2", name="n", listing_date=date(2026, 9, 22)).stage_on(a)
        == IpoStage.PENDING
    )
    assert (
        IpoEvent(market=IpoMarket.HK, code="2", name="n", listing_date=date(2026, 9, 21)).stage_on(a) == IpoStage.LISTED
    )
    assert IpoEvent(market=IpoMarket.US, code="3", name="n", filed_date=date(2026, 9, 20)).stage_on(a) == IpoStage.FILED


# -- 东财：A股申购表 ----------------------------------------------------------


def test_parse_ipo_apply_pending() -> None:
    events = parse_ipo_apply_payload(_load("ipo_apply_cn.json"))
    assert isinstance(events, list)
    by_code = {e.code: e for e in events}
    lianya = by_code["301569"]
    assert lianya.name == "联亚药业"
    assert lianya.market == IpoMarket.CN
    assert lianya.apply_date == date(2026, 9, 28)
    assert lianya.listing_date is None
    assert lianya.board == "创业板"
    # 北交所 92 开头记录 MARKET 字段为空，按代码前缀推断板块
    yute = by_code["920157"]
    assert yute.board == "北交所"
    assert yute.issue_price == pytest.approx(13.75)


def test_parse_ipo_apply_listed() -> None:
    events = parse_ipo_apply_payload(_load("ipo_apply_cn_listed.json"))
    assert isinstance(events, list)
    by_code = {e.code: e for e in events}
    shengu = by_code["601091"]
    assert shengu.name == "沈鼓集团"
    assert shengu.listing_date == date(2026, 9, 17)
    assert shengu.first_day_change == pytest.approx(373.8041)
    assert shengu.raise_yi == pytest.approx(13.652911)
    assert shengu.board == "沪主板"
    xinuo = by_code["688837"]
    assert xinuo.board == "科创板"


def test_parse_ipo_apply_invalid() -> None:
    result = parse_ipo_apply_payload({"success": False, "message": "报表配置不存在"})
    assert result.code == "empty"
    result = parse_ipo_apply_payload(None)
    assert result.code == "parse"


# -- 东财：港美股 clist -------------------------------------------------------


def test_parse_ipo_clist_hk_filters_noise() -> None:
    events = parse_ipo_clist_payload(_load("ipo_clist_hk.json"), IpoMarket.HK)
    assert isinstance(events, list)
    by_code = {e.code: e for e in events}
    # 多币柜台（-R/-U 后缀）被剔除；无 ETF 字样的基础柜台行（03599 广发港美科技）
    # 依赖服务端 fs=m:116 t:3,t:4 排除，parse 层兜底只拦后缀与名称关键词
    assert "83599" not in by_code and "41599" not in by_code
    tongcheng = by_code["09607"]
    assert tongcheng.name == "彤程新材"
    assert tongcheng.listing_date == date(2026, 9, 29)
    assert tongcheng.market == IpoMarket.HK
    # f13=116 区分不了板块，未传入 board 时留空
    assert tongcheng.board is None


def test_parse_ipo_clist_dict_diff_and_board() -> None:
    payload = {
        "data": {
            "diff": {
                "0": {"f12": "09607", "f13": 116, "f14": "彤程新材", "f26": 20260929},
                "1": {"f12": "02533", "f13": 116, "f14": "某创业板", "f26": 20260930},
            }
        }
    }
    events = parse_ipo_clist_payload(payload, IpoMarket.HK, board="港交所创业板")
    assert isinstance(events, list)
    assert [e.code for e in events] == ["09607", "02533"]
    assert all(e.board == "港交所创业板" for e in events)
    single = {"data": {"diff": {"f12": "09607", "f13": 116, "f14": "彤程新材", "f26": 20260929}}}
    one = parse_ipo_clist_payload(single, IpoMarket.HK, board="港交所主板")
    assert isinstance(one, list) and len(one) == 1 and one[0].board == "港交所主板"


def test_parse_ipo_clist_us_filters_noise() -> None:
    events = parse_ipo_clist_payload(_load("ipo_clist_us.json"), IpoMarket.US)
    assert isinstance(events, list)
    by_code = {e.code: e for e in events}
    # 权证（XIIIW，名称 Wt）与单位（LEDRU 尾缀 U）、ETF 被剔除；普通股保留
    assert "XIIIW" not in by_code and "LEDRU" not in by_code and "TSEE" not in by_code
    assert "XIII" in by_code and "EMI" in by_code and "ETRA" in by_code
    emi = by_code["EMI"]
    assert emi.listing_date == date(2026, 9, 30)
    assert emi.board == "美交所"
    assert by_code["AMRO"].board == "纳斯达克"
    assert by_code["BMB"].board == "纽交所"


# -- 纳斯达克 ------------------------------------------------------------------


def test_parse_nasdaq_calendar() -> None:
    events = parse_nasdaq_calendar(_load("ipo_nasdaq_202609.json"))
    assert isinstance(events, list)
    by_code = {e.code: e for e in events}
    hyacu = by_code["HYACU"]
    assert hyacu.name == "Haymaker Acquisition Corp V"
    assert hyacu.listing_date == date(2026, 9, 17)
    assert hyacu.issue_price == pytest.approx(10.0)
    assert hyacu.raise_yi == pytest.approx(2.5)
    assert hyacu.currency == "USD"
    assert hyacu.board == "纽交所"
    ptt = by_code["PTT"]
    assert ptt.listing_date == date(2026, 9, 30)
    # filed 只有申报日，无上市日；withdrawn 不进日历
    lca = by_code["LCACU"]
    assert lca.listing_date is None
    assert lca.filed_date == date(2026, 9, 16)
    assert by_code["BMB"].issue_price is None
    assert by_code["BMB"].issue_price_text == "18.00-20.00"
    assert by_code["HNUC"].issue_price_text == "15.00-18.00"


def test_dedupe_prefers_priced_over_range() -> None:
    upcoming = IpoEvent(
        market=IpoMarket.US,
        code="BMB",
        name="Bamboo",
        listing_date=date(2026, 9, 30),
        issue_price_text="18.00-20.00",
        currency="USD",
    )
    priced = IpoEvent(
        market=IpoMarket.US,
        code="bmb",
        name="Bamboo",
        listing_date=date(2026, 10, 2),
        issue_price=19.0,
        currency="USD",
    )
    merged = dedupe_ipo_events([upcoming, priced])
    assert len(merged) == 1
    assert merged[0].issue_price == pytest.approx(19.0)
    assert merged[0].listing_date == date(2026, 10, 2)
    assert merged[0].issue_price_text == "18.00-20.00"


def test_parse_ipo_market_filter_tokens() -> None:
    assert parse_ipo_market_filter("") is None
    assert parse_ipo_market_filter("沪港通") is None
    assert parse_ipo_market_filter("focus") is None
    assert parse_ipo_market_filter("美股") == [IpoMarket.US]
    assert parse_ipo_market_filter("a股 港股") == [IpoMarket.CN, IpoMarket.HK]
    assert parse_ipo_market_filter("us") == [IpoMarket.US]


def test_nasdaq_keeps_parsed_month_when_next_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from SayuStock.utils.market.adapters.nasdaq import provider as nasdaq_provider
    from SayuStock.utils.market.adapters.nasdaq.provider import NasdaqMarketData

    calls = {"n": 0}

    async def _fake(month: str) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return _load("ipo_nasdaq_202609.json")
        return "HTTP 429"

    monkeypatch.setattr(nasdaq_provider, "fetch_calendar_month", _fake)
    monkeypatch.setattr(nasdaq_provider, "months_for_window", lambda _anchor: ["2026-09", "2026-10"])
    result = asyncio.run(NasdaqMarketData().ipo_calendar(IpoMarket.US))
    assert isinstance(result, list)
    assert any(ev.code == "HYACU" for ev in result)
    assert any(ev.code == "BMB" and ev.issue_price_text == "18.00-20.00" for ev in result)


def test_nasdaq_all_months_failed_is_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from SayuStock.utils.market.adapters.nasdaq import provider as nasdaq_provider
    from SayuStock.utils.market.adapters.nasdaq.provider import NasdaqMarketData

    async def _fake(_month: str) -> str:
        return "HTTP 429"

    monkeypatch.setattr(nasdaq_provider, "fetch_calendar_month", _fake)
    monkeypatch.setattr(nasdaq_provider, "months_for_window", lambda _anchor: ["2026-09", "2026-10"])
    result = asyncio.run(NasdaqMarketData().ipo_calendar(IpoMarket.US))
    assert is_market_error(result)
    assert result.code == "network"


def test_months_for_window() -> None:
    assert months_for_window(date(2026, 9, 21)) == ["2026-09"]
    assert months_for_window(date(2026, 9, 1)) == ["2026-08", "2026-09"]
    assert months_for_window(date(2026, 10, 29)) == ["2026-10", "2026-11"]
    assert months_for_window(date(2026, 12, 30)) == ["2026-12", "2027-01"]


# -- 渲染数据 ------------------------------------------------------------------


def _sample_events() -> list[IpoEvent]:
    return [
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
        IpoEvent(market=IpoMarket.HK, code="09607", name="彤程新材", listing_date=date(2026, 9, 24)),
        IpoEvent(market=IpoMarket.US, code="LCACU", name="Lower Cross", filed_date=date(2026, 9, 23)),
        # 窗口外：旧上市 与 远期上市都不进窗口
        IpoEvent(market=IpoMarket.US, code="OLD", name="Old Co", listing_date=date(2026, 8, 1)),
        IpoEvent(market=IpoMarket.HK, code="FAR", name="Far Co", listing_date=date(2026, 10, 20)),
    ]


def test_build_ipo_calendar_render_data_window() -> None:
    data = build_ipo_calendar_render_data(_sample_events(), anchor=date(2026, 9, 21))
    assert data.window_start == date(2026, 9, 19)
    assert data.window_end == date(2026, 9, 28)
    assert len(data.days) == 10
    assert data.total == 4
    groups = {g.label: g for g in data.groups}
    assert {g.label for g in data.groups} == {"A股", "港股", "美股"}
    cn = groups["A股"]
    assert [r.code for r in cn.rows] == ["601091", "301569"]  # 按主日期升序
    shengu = cn.rows[0]
    assert shengu.stage == IpoStage.LISTED
    # 申购日越左窗被夹到 0，上市日 09-20 → 索引 1
    assert shengu.apply_pos == 0.0 and shengu.listing_pos == 1.0
    lianya = cn.rows[1]
    assert lianya.stage == IpoStage.APPLY
    assert lianya.apply_pos == 3.0
    assert lianya.listing_pos == 9.0  # 10-10 越右窗夹到边缘
    assert groups["港股"].rows[0].stage == IpoStage.PENDING
    assert groups["美股"].rows[0].stage == IpoStage.FILED


def test_build_ipo_calendar_render_data_market_filter() -> None:
    data = build_ipo_calendar_render_data(_sample_events(), anchor=date(2026, 9, 21), markets=["港股"])
    assert [g.label for g in data.groups] == ["港股"]
    assert data.total == 1


def test_ipo_calendar_text() -> None:
    from SayuStock.utils.render_text import ipo_calendar_text

    data = build_ipo_calendar_render_data(_sample_events(), anchor=date(2026, 9, 21))
    text = ipo_calendar_text(data)
    assert "IPO日历" in text and "共4只" in text
    assert "沈鼓集团(601091)" in text and "首日+373.8%" in text
    assert "联亚药业(301569)" in text and "09-22申购" in text
    assert "彤程新材(09607)" in text
    assert "Lower Cross(LCACU)" in text and "已申报" in text
    empty = build_ipo_calendar_render_data([], anchor=date(2026, 9, 21))
    assert "无新股" in ipo_calendar_text(empty)


# -- 港股招股期阶段 ----------------------------------------------------------


def test_stage_on_hk_apply_window() -> None:
    ev = IpoEvent(
        market=IpoMarket.HK,
        code="09607",
        name="n",
        apply_end_date=date(2026, 9, 24),
        listing_date=date(2026, 9, 29),
    )
    assert ev.stage_on(date(2026, 9, 21)) == IpoStage.APPLY
    assert ev.stage_on(date(2026, 9, 24)) == IpoStage.APPLY  # 截止日当天仍可申购
    assert ev.stage_on(date(2026, 9, 25)) == IpoStage.PENDING


# -- A股中签/缴款里程碑 --------------------------------------------------------


def test_parse_ipo_apply_ballot_pay() -> None:
    events = parse_ipo_apply_payload(_load("ipo_apply_cn_listed.json"))
    by_code = {e.code: e for e in events}
    zhongsu = by_code["301686"]
    assert zhongsu.ballot_date == date(2026, 9, 14)
    assert zhongsu.pay_date == date(2026, 9, 14)
    shiji = by_code["920229"]
    assert shiji.ballot_date == date(2026, 9, 14)
    assert shiji.pay_date is None  # 北交所不填缴款日


# -- AAStocks 港股增强 ---------------------------------------------------------


def test_parse_aastocks_mainpage() -> None:
    html = (FIXTURES / "aastocks_mainpage.html").read_text(encoding="utf-8")
    extras = parse_mainpage(html)
    # 招股中：区间招股价
    tc = extras["09607"]
    assert tc.apply_end_date == date(2026, 9, 24)
    assert tc.listing_date == date(2026, 9, 29)
    assert tc.issue_price_text == "39-44"
    assert tc.issue_price is None
    # 招股中：单值招股价
    assert extras["06802"].issue_price == pytest.approx(58.85)
    # 已上市：上市价/超购/首日表现
    xh = extras["06727"]
    assert xh.listing_date == date(2026, 9, 21)
    assert xh.issue_price == pytest.approx(49.0)
    assert xh.oversubscription == pytest.approx(72.9)
    assert xh.first_day_change == pytest.approx(-9.18)
    # 上市当天首日表现 N/A → None
    assert extras["09856"].first_day_change is None
    assert extras["09856"].issue_price == pytest.approx(32.96)


def test_parse_aastocks_upcoming() -> None:
    html = (FIXTURES / "aastocks_upcoming.html").read_text(encoding="utf-8")
    extras = parse_upcoming(html)
    jw = extras["03228"]
    assert jw.apply_end_date == date(2026, 9, 24)
    assert jw.grey_market_date == date(2026, 9, 28)
    assert jw.listing_date == date(2026, 9, 29)


def test_merge_hk_ipo_extra() -> None:
    ev = IpoEvent(market=IpoMarket.HK, code="09607", name="彤程新材", listing_date=date(2026, 9, 29))
    main = {"09607": HkIpoExtra(code="09607", apply_end_date=date(2026, 9, 24), issue_price_text="39-44")}
    up = {"09607": HkIpoExtra(code="09607", grey_market_date=date(2026, 9, 28))}
    merged = merge_hk_ipo_extra(merge_hk_ipo_extra([ev], main), up)[0]
    assert merged.apply_end_date == date(2026, 9, 24)
    assert merged.grey_market_date == date(2026, 9, 28)
    assert merged.name == "彤程新材"
    assert merged.listing_date == date(2026, 9, 29)  # 东财字段不被覆盖
    # 首日 0.0 是有效值，不能被 None 语义吞掉
    ev0 = IpoEvent(market=IpoMarket.HK, code="06727", name="x", first_day_change=0.0)
    m0 = merge_hk_ipo_extra([ev0], {"06727": HkIpoExtra(code="06727", first_day_change=-9.18)})[0]
    assert m0.first_day_change == 0.0


# -- 渲染数据：港股申购窗口/里程碑位置 --------------------------------------------


def test_build_ipo_calendar_hk_apply_window() -> None:
    ev = IpoEvent(
        market=IpoMarket.HK,
        code="09607",
        name="彤程新材",
        apply_end_date=date(2026, 9, 24),
        grey_market_date=date(2026, 9, 28),
        listing_date=date(2026, 9, 29),
        issue_price_text="39-44",
        currency="HKD",
    )
    data = build_ipo_calendar_render_data([ev], anchor=date(2026, 9, 21))
    row = data.groups[1].rows[0]
    assert row.stage == IpoStage.APPLY
    # 招股窗口 = 截止日前推 3 天：09-21 → 起点 2，截止 09-24 → 5
    assert row.apply_start_pos == 2.0
    assert row.apply_end_pos == 5.0
    assert row.grey_pos == 9.0
    assert row.listing_pos == 9.0  # 09-29 越窗夹取


def test_ipo_calendar_text_new_fields() -> None:
    from SayuStock.utils.render_text import ipo_calendar_text

    evs = [
        IpoEvent(
            market=IpoMarket.HK,
            code="09607",
            name="彤程新材",
            apply_end_date=date(2026, 9, 24),
            grey_market_date=date(2026, 9, 28),
            listing_date=date(2026, 9, 29),
            issue_price_text="39-44",
            currency="HKD",
        ),
        IpoEvent(
            market=IpoMarket.CN,
            code="301686",
            name="中塑股份",
            apply_date=date(2026, 9, 10),
            ballot_date=date(2026, 9, 14),
            pay_date=date(2026, 9, 14),
            listing_date=date(2026, 9, 22),
            issue_price=55.28,
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
    text = ipo_calendar_text(build_ipo_calendar_render_data(evs, anchor=date(2026, 9, 21)))
    assert "申购至09-24" in text and "暗盘09-28" in text and "招股价39-44港元" in text
    assert "中签缴款09-14" in text
    assert "首日-9.2%" in text and "超购72.9倍" in text and "发行价49港元" in text


def test_build_ipo_calendar_display_name() -> None:
    evs = [
        IpoEvent(market=IpoMarket.US, code="EMI", name="Encore Medical Inc", listing_date=date(2026, 9, 22)),
        IpoEvent(market=IpoMarket.CN, code="301686", name="中塑股份", apply_date=date(2026, 9, 22)),
    ]
    data = build_ipo_calendar_render_data(evs, anchor=date(2026, 9, 21))
    us_row = data.groups[2].rows[0]
    # 美股主名用 ticker，公司全名保留在 name（副标题/提示用）
    assert us_row.display_name == "EMI"
    assert us_row.name == "Encore Medical Inc"
    assert data.groups[0].rows[0].display_name == "中塑股份"


def test_ipo_html_two_line_subline_and_ticker() -> None:
    from SayuStock.stock_ipo.ipo_html import build_ipo_calendar_html

    evs = [
        IpoEvent(market=IpoMarket.US, code="EMI", name="Encore Medical Inc", listing_date=date(2026, 9, 22)),
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
    html = build_ipo_calendar_html(build_ipo_calendar_render_data(evs, anchor=date(2026, 9, 21)))
    # 美股：ticker 作主名，全名进日期行
    assert ">EMI</span>" in html
    assert "Encore Medical Inc" in html
    # 双行副标题：日期行(l2) 与数字行(l3) 分离
    # 港股/美股两行各带日期行+数字行；A股空分组行只有提示文字（l2）
    assert html.count('class="l2"') == 3 and html.count('class="l3"') == 2
    assert "首日-9.2%" in html and "超购72.9倍" in html


def test_parse_aastocks_names() -> None:
    html = (FIXTURES / "aastocks_mainpage.html").read_text(encoding="utf-8")
    extras = parse_mainpage(html)
    # 名称从「公司名+代码+N日後截止招股」单元格里剥离；繁体原样保留
    assert extras["09607"].name == "彤程新材料"
    assert extras["03228"].name == "深圳市景旺電子"
    # 已上市的「跌穿上市價」后缀剔除
    assert extras["06727"].name == "星環科技"


def test_hk_ipo_events_from_extras_fallback() -> None:
    from SayuStock.utils.market.adapters.aastocks.parse import hk_ipo_events_from_extras

    main = parse_mainpage((FIXTURES / "aastocks_mainpage.html").read_text(encoding="utf-8"))
    up = parse_upcoming((FIXTURES / "aastocks_upcoming.html").read_text(encoding="utf-8"))
    extras = dict(main)
    for code, extra in up.items():
        base = extras.get(code)
        if base is None:
            extras[code] = extra
        else:
            base.grey_market_date = base.grey_market_date or extra.grey_market_date
            base.apply_end_date = base.apply_end_date or extra.apply_end_date
            base.listing_date = base.listing_date or extra.listing_date
    events = hk_ipo_events_from_extras(extras)
    by_code = {e.code: e for e in events}
    assert by_code["09607"].name == "彤程新材料"
    assert by_code["09607"].apply_end_date == date(2026, 9, 24)
    assert by_code["09607"].grey_market_date == date(2026, 9, 28)
    assert by_code["09607"].listing_date == date(2026, 9, 29)
    assert by_code["06727"].first_day_change == pytest.approx(-9.18)
    assert by_code["06727"].currency == "HKD"
    assert all(e.listing_date is not None for e in events)


def test_apply_pre_window_not_drawn_as_tick() -> None:
    """越窗申购日不能被夹到窗口起点画条（误导），应改为左缘摘要。"""
    from SayuStock.stock_ipo.ipo_html import build_ipo_calendar_html

    ev = IpoEvent(
        market=IpoMarket.CN,
        code="301686",
        name="中塑股份",
        apply_date=date(2026, 9, 10),
        ballot_date=date(2026, 9, 14),
        pay_date=date(2026, 9, 14),
        listing_date=date(2026, 9, 22),
    )
    data = build_ipo_calendar_render_data([ev], anchor=date(2026, 9, 21))
    html = build_ipo_calendar_html(data)
    row = html.split("中塑股份")[1].split("</div>\n</div>")[0]
    # 不应有画在 0% 的申购实心条
    assert 'class="tick heavy" style="left:0.00%' not in row
    # 应有左缘彩色摘要：申购 + 中签缴款
    assert "申购09-10" in html and "中签缴款09-14" in html


def test_parse_aastocks_name_deadline_variants() -> None:
    from SayuStock.utils.market.adapters.aastocks.parse import _row_name

    # AAStocks 按剩余天数换措辞：N日後/明天/今日 截止招股
    assert _row_name(["深圳市景旺電子03228.HK2日後截止招股"]) == "深圳市景旺電子"
    assert _row_name(["歡創科技06802.HK明天截止招股"]) == "歡創科技"
    assert _row_name(["本末動力（北京）科技06731.HK今日截止招股"]) == "本末動力（北京）科技"
    assert _row_name(["星環科技06727.HK跌穿上市價"]) == "星環科技"


def test_ipo_html_lines_fit_info_column() -> None:
    """三行副标题的每一行都必须在 324px 信息列内（CJK 12.5px / ASCII 6.5px 估算）。"""
    from SayuStock.stock_ipo.ipo_html import (
        _row_date_line,
        _row_numbers_line,
        _row_identity_line,
    )

    worst = IpoCalendarRow(
        market=IpoMarket.US,
        stage=IpoStage.PENDING,
        name="Yorkville America 2X Inverse Market Solution",
        display_name="MNGB",
        code="MNGB",
        board="纽交所",
        apply_date=None,
        apply_end_date=None,
        ballot_date=None,
        pay_date=None,
        grey_market_date=None,
        listing_date=date(2026, 9, 22),
        filed_date=None,
        issue_price=10.0,
        issue_price_text=None,
        raise_yi=2.5,
        currency="USD",
        first_day_change=None,
        oversubscription=None,
        apply_start_pos=None,
        apply_pos=None,
        apply_end_pos=None,
        ballot_pos=None,
        pay_pos=None,
        grey_pos=None,
        listing_pos=5.0,
        filed_pos=None,
    )
    for line in (_row_identity_line(worst), _row_date_line(worst), _row_numbers_line(worst)):
        text = re.sub(r"<[^>]+>", "", line)
        width = sum(12.5 if ord(ch) > 0x2E80 else 6.5 for ch in text)
        assert width < 324, f"副标题行超宽: {text!r} ≈ {width:.0f}px"
    # 美股：身份行=公司全名独占；交易所挪到日期行
    assert _row_identity_line(worst) == "Yorkville America 2X Inverse Market Solution"
    assert _row_date_line(worst).startswith("纽交所 ·")

    hk = IpoCalendarRow(
        market=IpoMarket.HK,
        stage=IpoStage.APPLY,
        name="深圳市景旺電子",
        display_name="深圳市景旺電子",
        code="03228",
        board="港交所主板",
        apply_date=None,
        apply_end_date=date(2026, 9, 24),
        ballot_date=None,
        pay_date=None,
        grey_market_date=date(2026, 9, 28),
        listing_date=date(2026, 9, 29),
        filed_date=None,
        issue_price=None,
        issue_price_text="最高 69.88",
        raise_yi=None,
        currency="HKD",
        first_day_change=None,
        oversubscription=72.9,
        apply_start_pos=3.0,
        apply_pos=None,
        apply_end_pos=6.0,
        ballot_pos=None,
        pay_pos=None,
        grey_pos=11.0,
        listing_pos=10.0,
        filed_pos=None,
    )
    for line in (_row_identity_line(hk), _row_date_line(hk), _row_numbers_line(hk)):
        text = re.sub(r"<[^>]+>", "", line)
        width = sum(12.5 if ord(ch) > 0x2E80 else 6.5 for ch in text)
        assert width < 324, f"副标题行超宽: {text!r} ≈ {width:.0f}px"


def test_us_without_ticker_falls_back_to_name() -> None:
    """纳斯达克已申报行可能未分到代码：主名回退公司全名，身份行不重复。"""
    from SayuStock.stock_ipo.ipo_html import _row_identity_line, build_ipo_calendar_html

    no_ticker = IpoEvent(
        market=IpoMarket.US,
        code="",
        name="Early Filing Corp",
        filed_date=date(2026, 9, 22),
    )
    data = build_ipo_calendar_render_data([no_ticker], anchor=date(2026, 9, 21))
    row = data.groups[2].rows[0]
    assert row.display_name == "Early Filing Corp"
    assert _row_identity_line(row) == ""
    html = build_ipo_calendar_html(data)
    assert "Early Filing Corp" in html
    # 有代码时维持原行为：主名=ticker，身份行=公司全名
    with_ticker = IpoEvent(
        market=IpoMarket.US,
        code="CPU",
        name="Amplify Top 10 Semiconductors ETF",
        listing_date=date(2026, 9, 22),
    )
    row2 = build_ipo_calendar_render_data([with_ticker], anchor=date(2026, 9, 21)).groups[2].rows[0]
    assert row2.display_name == "CPU"
    assert _row_identity_line(row2) == "Amplify Top 10 Semiconductors ETF"
