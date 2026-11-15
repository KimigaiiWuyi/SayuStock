"""行业云图、持仓分析、选股技术分析、宏观事件、快讯分发与通用排行核心测试。"""

from __future__ import annotations

import asyncio
from typing import Literal
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from SayuStock.utils import market_ranking as mr, sector_resolve as sector_resolve_mod
from SayuStock.stock_news import _DIGEST_BATCH, _send_digest, _digest_payload, _throttled_send
from SayuStock.utils.news import (
    NewsFeed,
    NewsItem,
    id_newer,
    should_rebase,
    split_watermark,
)
from SayuStock.stock_macro import (
    repo as macro_repo,
    prompts as macro_prompts,
    knowledge as macro_kb,
)
from SayuStock.stock_analysis import screener, technical
from SayuStock.stock_cloudmap import data as cloudmap_data
from SayuStock.utils.constant import chinese_stocks
from SayuStock.utils.news.errors import NewsError, is_news_error, network_error
from SayuStock.utils.render_data import CloudmapRenderData, build_cloudmap_render_data
from SayuStock.utils.market.enums import BoardKind
from SayuStock.utils.market.models import (
    RANKING_CAVEAT,
    RankRow,
    BoardRow,
    RankSnapshot,
    BoardSnapshot,
)
from SayuStock.utils.news.fallback import FallbackNews, resolve_order
from SayuStock.utils.update_stocks import merge_industry_levels
from SayuStock.utils.sector_resolve import (
    INDUSTRY_MEMBER_CACHE_MINUTES,
    match_sector_menu,
    normalize_sector_query,
    filter_snapshot_by_codes,
    local_industry_member_codes,
)
from SayuStock.stock_holdings_analysis import parse as ha_parse, quota as ha_quota, service as ha_service
from SayuStock.utils.database.macro_models import (
    MACRO_STATUS_OPEN,
    MACRO_STATUS_SETTLED,
    SayuMacroEvent,
)


def test_industry_cloudmap_sector_resolution_without_bk_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """行业云图使用本地申万三级/板块菜单过滤热图成分，禁止每次请求拉全量 BK 行情。"""
    assert INDUSTRY_MEMBER_CACHE_MINUTES >= 30 * 24 * 60
    assert normalize_sector_query("水泥板块") == "水泥"
    menu = {"建筑材料": "BK1208", "水泥": "BK0424", "半导体": "BK1036"}
    assert match_sector_menu("水泥", menu) == ("水泥", "BK0424")
    assert match_sector_menu("不存在", menu) is None

    codes = local_industry_member_codes("水泥")
    assert codes is not None and "600585" in codes

    # 历史事故：l1 被 f100 细分名污染出伪小簇时，并集查询仍须返回完整 l2 成分
    semi = local_industry_member_codes("半导体")
    assert semi is not None and len(semi) >= 180
    assert {"688256", "603061", "688981", "002371"}.issubset(semi)
    med = local_industry_member_codes("医疗器械")
    assert med is not None and len(med) >= 130 and "688712" in med
    monkeypatch.setitem(
        chinese_stocks,
        "999999",
        {"name": "污染测试股", "industry_l1": "半导体", "industry_l2": "消费电子", "industry_l3": "消费电子"},
    )
    polluted = local_industry_member_codes("半导体")
    assert polluted is not None and {"688256", "688981"}.issubset(polluted)

    def _row(code: str, name: str, industry: str) -> BoardRow:
        return BoardRow(code, name, 20.0, 1.5, 1e8, 2e11, industry, None, None)

    hotmap = BoardSnapshot(
        BoardKind.HOTMAP,
        "大盘云图",
        (_row("600585", "海螺水泥", "建筑材料"), _row("600519", "贵州茅台", "食品饮料")),
    )
    filtered = filter_snapshot_by_codes(hotmap, ["600585"], title="水泥")
    assert isinstance(filtered, BoardSnapshot) and [r.code for r in filtered.rows] == ["600585"]
    cloud = build_cloudmap_render_data(
        BoardSnapshot(BoardKind.INDUSTRY, "水泥", filtered.rows), "行业云图", "水泥", layer=1
    )
    assert isinstance(cloud, CloudmapRenderData) and [leaf.name for leaf in cloud.leaves] == ["海螺水泥"]

    class _MenuPort:
        def __init__(self) -> None:
            self.board_calls = 0
            self.hotmap_calls = 0

        async def sector_menu(self, kind: Literal["industry", "concept"]) -> dict[str, str]:
            _ = kind
            return dict(menu)

        async def board(self, *_args: object, **_kwargs: object) -> BoardSnapshot:
            self.board_calls += 1
            return hotmap

        async def hotmap(self) -> BoardSnapshot:
            self.hotmap_calls += 1
            return hotmap

    fake = _MenuPort()
    monkeypatch.setattr(sector_resolve_mod, "get_market", lambda: fake)
    monkeypatch.setattr(cloudmap_data, "get_market", lambda: fake)
    result = asyncio.run(cloudmap_data.CLOUDMAP_DATA_SERVICE.fetch("行业云图", "水泥", None, None))
    assert isinstance(result.raw_data, BoardSnapshot)
    assert [r.name for r in result.raw_data.rows] == ["海螺水泥"]
    assert fake.board_calls == 0 and fake.hotmap_calls == 1


def test_update_stocks_merge_backfills_l1_without_f100_pollution() -> None:
    """l1_map 漏抓的股票按 l2 多数票回补一级行业，f100 细分名不能越级进 industry_l1。"""
    merged = merge_industry_levels(
        base_mapping={"688981": "中芯国际", "688256": "寒武纪", "688000": "同业新股", "688001": "未知细类"},
        f100_map={"688981": "半导体", "688256": "半导体", "688000": "半导体", "688001": "冷门细分"},
        l1_map={"688981": "电子"},
        l2_map={"688981": "半导体", "688256": "半导体"},
        l3_map={"688981": "集成电路制造", "688256": "数字芯片设计"},
    )
    assert merged["688256"]["industry_l1"] == "电子"
    assert merged["688000"]["industry_l1"] == "电子"
    assert merged["688001"]["industry_l1"] == "未知"


def test_holdings_analysis_symbols_and_daily_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """持仓分析手动输入优先于自选并截断上限，自然日与白名单限额隔离。"""
    assert ha_parse.split_manual_symbols("600519, 300750\n000001") == ["600519", "300750", "000001"]
    capped, hit_cap = ha_parse.cap_symbols([str(i) for i in range(25)], limit=10)
    assert hit_cap and len(capped) == 10

    res = ha_parse.build_symbol_list(manual_text="600519 000001", watchlist=["300750"])
    assert res is not None and res.symbols == ["600519", "000001"] and res.source == "manual"
    assert ha_parse.build_symbol_list(manual_text="", watchlist=[]) is None

    day1 = date(2026, 9, 1)
    day2 = day1 + timedelta(days=1)
    ha_quota.clear_quota_for_test("u_q", "b_q", day1)
    ha_quota.clear_quota_for_test("u_q", "b_q", day2)
    try:
        assert ha_quota.try_claim_quota("u_q", "b_q", day1) is True
        assert ha_quota.try_claim_quota("u_q", "b_q", day1) is False
        assert ha_quota.try_claim_quota("u_q", "b_q", day2) is True
        ha_quota.release_quota("u_q", "b_q", day2)
        assert ha_quota.is_quota_available("u_q", "b_q", day2) is True
    finally:
        ha_quota.clear_quota_for_test("u_q", "b_q", day1)
        ha_quota.clear_quota_for_test("u_q", "b_q", day2)

    assert len(ha_service.extract_res_handles("结果 res_abcdef12 已出")) == 1
    assert "NO_BROADCAST" not in ha_service.normalize_analysis_markdown("分析完成\n<<NO_BROADCAST>>")


def test_stock_analysis_period_technical_and_screener() -> None:
    """技术分析周期识别不吞首字，合成 K 线评分及选股查询解析与过滤。"""
    assert technical.parse_period_and_query("日k 茅台") == ("101", "茅台")
    assert technical.parse_period_and_query("周k 600519") == ("102", "600519")
    assert technical.parse_period_and_query("日经225") == ("101", "日经225")
    assert technical.parse_period_and_query("周大福") == ("101", "周大福")

    klines: list[str] = []
    price = 100.0
    for i in range(80):
        day = (i % 28) + 1
        c = price * (1.01 if i % 3 else 0.995)
        klines.append(
            f"2024-01-{day:02d},{price:.2f},{c:.2f},"
            f"{max(price, c) * 1.01:.2f},{min(price, c) * 0.99:.2f},1000,1e7,1,0.5,0.5,1.0"
        )
        price = c
    rep = technical.build_technical_report(name="测试", code="000001", period_code="101", klines=klines)
    assert not isinstance(rep, str) and 0 <= rep.score <= 100 and rep.trend in ("偏多", "偏空", "震荡")

    leftover, ind, con, filters = screener.parse_screener_query("行业 半导体 概念 人工智能 PE<30 涨跌幅>2 市值50-200")
    assert ind == "半导体" and con == "人工智能" and leftover == ""
    df = pd.DataFrame(
        [
            {"code": "1", "name": "A", "pe": 20, "pct": 3, "mv_yi": 100, "turnover": 1, "vol_ratio": 1},
            {"code": "2", "name": "B", "pe": 40, "pct": 5, "mv_yi": 100, "turnover": 1, "vol_ratio": 1},
            {"code": "3", "name": "C", "pe": 15, "pct": 1, "mv_yi": 100, "turnover": 1, "vol_ratio": 1},
        ]
    )
    assert list(screener.apply_filters(df, filters)["code"]) == ["1"]
    assert not any(f[0] == "pct" for f in screener.parse_screener_query("跌幅>2")[3])


def test_macro_events_normalization_knowledge_and_sqlite_roundtrip(tmp_path: Path) -> None:
    """宏观事件规范化、到期/高危判断、知识库分片及临时 SQLite 增改闭环。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import gsuid_core.utils.database.base_models as base_models

    assert macro_repo.normalize_slug("US-China Tariff 2026!") == "us_china_tariff_2026"
    assert macro_repo.normalize_category("贸易") == "trade"
    assert macro_repo.normalize_direction("利空") == "risk_off"
    assert macro_repo.normalize_status("已结束") == MACRO_STATUS_SETTLED
    assert macro_repo.normalize_severity(99) == 5 and macro_repo.normalize_severity("bad") == 3
    assert macro_prompts.normalize_macro_regime("防守") == "防御"

    sections = macro_kb.split_markdown_sections("## 一\n正文A\n## 二\n正文B", doc_slug="d", fallback_title="t")
    assert len(sections) == 2

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/macro.db")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _run() -> None:
        macro_table = SayuMacroEvent.metadata.tables["sayumacroevent"]
        async with engine.begin() as conn:
            await conn.run_sync(SayuMacroEvent.metadata.create_all, tables=[macro_table])
        original = base_models.async_maker
        base_models.async_maker = maker
        try:
            first = macro_repo.MacroEventInput.from_raw(
                slug="tariff_2026",
                title="关税事件",
                category="trade",
                severity=4,
                direction="risk_off",
                summary="初始",
                result="加征",
            )
            row = await macro_repo.MacroEventRepo.upsert(first, touch_checked=False)
            assert row.id is not None and macro_repo.is_severe_risk_off(row)
            assert [e.slug for e in await macro_repo.MacroEventRepo.list_open_severe()] == ["tariff_2026"]

            downgrade = macro_repo.MacroEventInput.from_raw(
                slug="tariff_2026", title="关税事件", severity=2, direction="mixed"
            )
            row2 = await macro_repo.MacroEventRepo.upsert(downgrade, touch_checked=True)
            assert row2.id == row.id and row2.summary == "初始"
            assert await macro_repo.MacroEventRepo.list_open_severe() == []

            settled = macro_repo.MacroEventInput.from_raw(slug="tariff_2026", title="关税事件", status="settled")
            row3 = await macro_repo.MacroEventRepo.upsert(settled, touch_checked=True)
            assert row3.status == MACRO_STATUS_SETTLED and macro_repo.is_due_for_check(row3) is False
            assert await macro_repo.MacroEventRepo.list_by_status(MACRO_STATUS_OPEN) == []
            assert await macro_repo.MacroEventRepo.delete_by_slug("tariff_2026") is True
        finally:
            base_models.async_maker = original
            await engine.dispose()

    asyncio.run(_run())


def test_news_port_fallback_and_digest_watermark_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """快讯多源顺延与水位线对齐；汇总发送失败 (-1) 不记已发送也不推进水位线。"""
    assert split_watermark("eastmoney:123") == ("eastmoney", "123")
    assert should_rebase("999999", "eastmoney") is True
    assert id_newer("10", "2") is True
    assert resolve_order(("a", "b", "c"), ["c"]) == ("c", "a", "b")

    class _Scripted:
        def __init__(self, name: str, result: NewsFeed | NewsError) -> None:
            self.name = name
            self._result = result
            self.calls = 0

        async def fetch(self, *, limit: int, cover_ms: int) -> NewsFeed | NewsError:
            _ = limit, cover_ms
            self.calls += 1
            return self._result

    bad = _Scripted("eastmoney", network_error("down", provider="eastmoney"))
    good = _Scripted(
        "wallstreetcn", NewsFeed("wallstreetcn", (NewsItem("wallstreetcn", "1", "正文", 1_700_000_000_000, True),))
    )
    port = FallbackNews((bad, good), order=("eastmoney", "wallstreetcn"))
    res = asyncio.run(port.latest())
    assert isinstance(res, NewsFeed) and res.source == "wallstreetcn"
    err = asyncio.run(FallbackNews((bad,), order=("eastmoney",)).latest())
    assert is_news_error(err) and err.provider == "eastmoney"

    class _FakeSubscribe:
        def __init__(self, send_result: int | None = None) -> None:
            self.group_id = "10001"
            self.bot_id = "bot"
            self.extra_message = "eastmoney:0"
            self.send_result = send_result
            self.sent: list[object] = []

        async def send(self, reply: object = None) -> int | None:
            self.sent.append(reply)
            return self.send_result

    sent_ids: list[str] = []
    watermarks: list[str] = []

    async def _no_sleep(_d: float) -> None:
        return None

    async def _record_wm(_sub: object, val: str) -> None:
        watermarks.append(val)

    monkeypatch.setattr("SayuStock.stock_news.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("SayuStock.stock_news.random.random", lambda: 0.0)
    monkeypatch.setattr("SayuStock.stock_news._mark_sent", lambda _g, nid: sent_ids.append(nid))
    monkeypatch.setattr("SayuStock.stock_news._update_watermark", _record_wm)

    assert asyncio.run(_throttled_send(_FakeSubscribe(-1), "hi")) is False
    items = [NewsItem("eastmoney", str(i), f"新闻{i}", 1_700_000_000_000 + i * 60_000, True) for i in range(1, 4)]
    asyncio.run(_send_digest(_FakeSubscribe(-1), items, "小时汇总"))
    assert sent_ids == [] and watermarks == []

    sub_ok = _FakeSubscribe(None)
    asyncio.run(_send_digest(sub_ok, items, "小时汇总"))
    assert sent_ids == ["eastmoney:1", "eastmoney:2", "eastmoney:3"] and watermarks == ["eastmoney:3"]
    assert isinstance(_digest_payload(["a", "b"]), str | list)
    assert _DIGEST_BATCH > 0


def test_market_ranking_aliases_and_port_facade() -> None:
    """通用排行别名规范化、质量池宽度上限及经 MarketDataPort 返回口径。"""
    from SayuStock.stock_papertrade.candidate_pool import QUALITY_RANK_LIMIT

    assert mr.normalize_rank_by("资金流入") == "main_inflow"
    assert mr.normalize_rank_by("净资产收益率") == "roe"
    assert mr.normalize_rank_by("unknown_xyz") is None
    assert 1 <= QUALITY_RANK_LIMIT <= 100
    assert mr.RANK_SPECS["main_outflow"].default_high_first is False

    bad = asyncio.run(mr.fetch_market_ranking("unknown_xyz", n=5))
    assert bad["ok"] is False and bad["items"] == []

    snap = RankSnapshot(
        rank_by="amount",
        rank_by_label="成交额",
        unit_hint="元",
        high_first=True,
        caveat=RANKING_CAVEAT,
        rows=(RankRow(1, "600519", "贵州茅台", 1600.0, 1.2, 1.5e10, "成交额", 0.5, 1.5e10, 1e6, "白酒"),),
    )

    class _FakeMarket:
        async def rank_list(self, _rb: object, *, limit: int = 20, high_first: bool | None = None) -> RankSnapshot:
            _ = limit, high_first
            return snap

    with patch.object(mr, "get_market", return_value=_FakeMarket()):
        out = asyncio.run(mr.fetch_market_ranking("成交额", n=5))
    assert out["ok"] is True and out["items"][0]["code"] == "600519"
