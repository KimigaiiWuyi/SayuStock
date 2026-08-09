"""AI 模拟盘候选池单测（mock 6 路源，验证合并/截断/去重）。"""

import sys
import importlib.util
from types import ModuleType
from typing import List
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_pool_test"


def _ensure_pkg():
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        PKG_ROOT / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT)],
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
    sub_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade",
        PKG_ROOT / "stock_papertrade" / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT / "stock_papertrade")],
    )
    assert sub_spec is not None
    sub = importlib.util.module_from_spec(sub_spec)
    sub.__path__ = [str(PKG_ROOT / "stock_papertrade")]
    sys.modules[f"{PKG_NAME}.stock_papertrade"] = sub


def _load(name: str, file_name: str) -> ModuleType:
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade.{name}",
        PKG_ROOT / "stock_papertrade" / file_name,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# 加载 db（依赖 papertrade_models）
db = _load("db", "db.py")
# 加载 candidate_pool
pool = _load("candidate_pool", "candidate_pool.py")

build_candidate_pool = pool.build_candidate_pool
post_decision_pool_update = pool.post_decision_pool_update
SOURCE_CAPS = pool.SOURCE_CAPS
TOTAL_CAP = pool.TOTAL_CAP
# 其它用例会 mock 掉源函数；质量缓存单测直接调真实实现，避免污染模块属性类型
_REAL_FROM_QUALITY_ROE = getattr(pool, "_from_quality_roe")


# ============================================================
# 测试 build_candidate_pool 的合并 / 截断 / 去重逻辑
# （用 monkey-patch 替换 6 路源函数）
# ============================================================
def _make_fake_sources(
    position: List[str] | None = None,
    watchlist: List[str] | None = None,
    agent_pool: List[str] | None = None,
    sector: List[str] | None = None,
    concept: List[str] | None = None,
    hotmap: List[str] | None = None,
    gainer: List[str] | None = None,
    laggard: List[str] | None = None,
    amount: List[str] | None = None,
    quality: List[str] | None = None,
    news: List[str] | None = None,
):
    """生成假数据源闭包（async，与真实接口一致；默认全空，避免单测打真网）"""

    async def _pos(gid, bid):
        return list(position or [])

    async def _wl(gid, bid):
        return list(watchlist or [])

    async def _ap(gid, bid):
        return list(agent_pool or [])

    async def _sec(*a, **kw):
        return list(sector or [])

    async def _con(*a, **kw):
        return list(concept or [])

    async def _hot(*a, **kw):
        return list(hotmap or [])

    async def _gain(*a, **kw):
        return list(gainer or [])

    async def _lag(*a, **kw):
        return list(laggard or [])

    async def _amt(*a, **kw):
        return list(amount or [])

    async def _qual(*a, **kw):
        return list(quality or [])

    async def _news(*a, **kw):
        return list(news or [])

    return {
        "_from_position": _pos,
        "_from_watchlist": _wl,
        "_from_agent_pool": _ap,
        "_from_sector_top_picks": _sec,
        "_from_concept_top_picks": _con,
        "_from_hotmap_top_n": _hot,
        "_from_market_gainers": _gain,
        "_from_market_laggards": _lag,
        "_from_amount_leaders": _amt,
        "_from_quality_roe": _qual,
        "_from_news_extract_tickers": _news,
    }


def test_pool_empty():
    """多源都空 → 候选池空"""
    fake = _make_fake_sources()
    for name, fn in fake.items():
        setattr(pool, name, fn)
    out = []
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    assert out == []
    print("[OK] 多源空 → 候选池空")


def test_pool_single_source():
    """单路有数据 → 全保留"""
    fake = _make_fake_sources(position=["600519", "000001", "300750"])
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    assert out == ["600519", "000001", "300750"]
    print(f"[OK] 单路持仓 → {len(out)} 只")


def test_pool_dedup():
    """持仓和关注都包含 600519 → 只出现一次"""
    fake = _make_fake_sources(
        position=["600519", "000001"],
        watchlist=["600519", "300750"],
    )
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    assert out.count("600519") == 1
    # 顺序：position 先，watchlist 后 → position 在前
    assert out == ["600519", "000001", "300750"]
    print(f"[OK] 去重 + 保序 → {out}")


def test_pool_priority_order():
    """position > watchlist > agent_pool > sector > hotmap > news（6 位数字代码）"""
    fake = _make_fake_sources(
        position=["600001", "600002"],  # P1, P2
        watchlist=["600003", "600004"],  # W1, W2
        agent_pool=["600005"],  # A1
        sector=["600006", "600007"],  # S1, S2
        hotmap=["600008"],  # H1
        news=["600009"],  # N1
    )
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    # 期望顺序：position 先，watchlist 后，依次类推
    expected = ["600001", "600002", "600003", "600004", "600005", "600006", "600007", "600008", "600009"]
    assert out == expected, f"got {out}, expected {expected}"
    print(f"[OK] 多源优先级顺序 → {out}")


def test_amount_leaders_uses_rank_list():
    """成交额源走 Port.rank_list(AMOUNT)，保持 API 返回顺序（非涨跌幅 board）。"""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import SayuStock.stock_papertrade.candidate_pool as real_pool
    from SayuStock.utils.market.enums import RankBy
    from SayuStock.utils.market.models import RANKING_CAVEAT, RankRow, RankSnapshot

    rows = (
        RankRow(
            rank=1,
            code="601318",
            name="中国平安",
            price=50.0,
            change_pct=0.1,
            metric=3e10,
            metric_label="成交额",
            turnover_pct=1.0,
            amount=3e10,
            volume=1e7,
            sector="保险",
        ),
        RankRow(
            rank=2,
            code="600519",
            name="茅台",
            price=1600.0,
            change_pct=-0.5,
            metric=2e10,
            metric_label="成交额",
            turnover_pct=0.5,
            amount=2e10,
            volume=1e6,
            sector="白酒",
        ),
    )
    snap = RankSnapshot(
        rank_by="amount",
        rank_by_label="成交额",
        unit_hint="元",
        high_first=True,
        caveat=RANKING_CAVEAT,
        rows=rows,
    )

    class _FakeMarket:
        rank_list = AsyncMock(return_value=snap)

    fake = _FakeMarket()
    with patch("SayuStock.utils.market.get_market", return_value=fake):
        out = asyncio.run(real_pool._from_amount_leaders(n=2))

    assert out == ["601318", "600519"]
    fake.rank_list.assert_awaited()
    assert fake.rank_list.await_args.args[0] == RankBy.AMOUNT
    print(f"[OK] amount leaders via rank_list → {out}")


def test_pool_total_cap_50():
    """总池上限 50"""
    # 各路都返回 30 只（超过单路上限），合并后应被截断到 50
    pos = [f"{600000 + i:06d}" for i in range(30)]
    wl = [f"{600100 + i:06d}" for i in range(30)]
    ap = [f"{600200 + i:06d}" for i in range(30)]
    sec = [f"{600300 + i:06d}" for i in range(30)]
    hot = [f"{600400 + i:06d}" for i in range(30)]
    news = [f"{600500 + i:06d}" for i in range(30)]
    fake = _make_fake_sources(
        position=pos,
        watchlist=wl,
        agent_pool=ap,
        sector=sec,
        hotmap=hot,
        news=news,
    )
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    assert len(out) == TOTAL_CAP
    assert len(out) == 50
    print(f"[OK] 总池上限 50 截断：{len(out)} 只")


def test_pool_single_source_cap():
    """单路上限：position ≤ 20"""
    pos = [f"{600000 + i:06d}" for i in range(50)]
    fake = _make_fake_sources(position=pos)
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    assert len(out) == SOURCE_CAPS["position"]  # 20
    print(f"[OK] position 单路限 {SOURCE_CAPS['position']}：{len(out)} 只")


def test_pool_invalid_codes_filtered():
    """非法代码（< 6 位、非数字）被过滤"""
    fake = _make_fake_sources(position=["600519", "abc", "12", "000001", "1.5", "300750"])
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(build_candidate_pool("g1", "b1"))
    assert out == ["600519", "000001", "300750"]
    print(f"[OK] 非法代码过滤 → {out}")


def test_pool_exclude_sources():
    """include_sector/include_hotmap/include_news/extra=False 时禁用"""
    fake = _make_fake_sources(
        position=["600001"],
        sector=["600002"],
        hotmap=["600003"],
        news=["600004"],
    )
    for name, fn in fake.items():
        setattr(pool, name, fn)
    import asyncio

    out = asyncio.run(
        build_candidate_pool(
            "g1",
            "b1",
            include_sector=False,
            include_hotmap=False,
            include_news=False,
            include_extra_momentum=False,
        )
    )
    assert out == ["600001"]
    print("[OK] exclude_sources 正确禁用")


def test_interleave_source_pairs():
    """多源轮询交织，避免某一路占满"""
    pairs = [
        ("600001", "sector"),
        ("600002", "sector"),
        ("600003", "sector"),
        ("600101", "hotmap"),
        ("600102", "hotmap"),
        ("600201", "news"),
    ]
    out = pool.interleave_source_pairs(pairs)
    # 轮询：sector, concept(空跳过), hotmap, ... quality(空), news, ...
    codes = [c for c, _ in out]
    assert codes[0] == "600001"
    assert "600101" in codes[:4]  # hotmap 应较早出现，而非等 sector 全写完
    # sector 不应连占前三（中间会插入其他源）
    assert not (codes[0].startswith("60000") and codes[1].startswith("60000") and codes[2].startswith("60000"))
    print(f"[OK] interleave → {out}")


def test_quality_roe_cache_hit():
    """质量池进程缓存命中时不再重筛（直接调真实实现，不依赖模块当前 mock）"""
    import time
    import asyncio

    cache = getattr(pool, "_QUALITY_ROE_CACHE")
    cache["expire_ts"] = time.time() + 3600
    cache["codes"] = ["600519", "000858", "300760"]
    out = asyncio.run(_REAL_FROM_QUALITY_ROE(n=2))
    assert out == ["600519", "000858"]
    cache["expire_ts"] = 0.0
    cache["codes"] = []
    print(f"[OK] quality cache hit → {out}")


def test_post_decision_pool_update_buy():
    """buy 决策后 agent_pool 应加入（7 天过期）"""
    import asyncio
    from datetime import datetime, timedelta

    async def _check():
        # mock upsert / remove
        upserted = []
        removed = []

        async def fake_upsert(*args, **kwargs):
            upserted.append((args, kwargs))
            return None

        async def fake_remove(*args, **kwargs):
            removed.append((args, kwargs))
            return True

        # 替换 db.PaperAgentPoolRepo 的方法
        db.PaperAgentPoolRepo.upsert = classmethod(lambda cls, *a, **kw: fake_upsert(*a, **kw))
        db.PaperAgentPoolRepo.remove = classmethod(lambda cls, *a, **kw: fake_remove(*a, **kw))

        decisions = [
            {
                "action": "buy",
                "code": "600519",
                "name": "茅台",
                "secid": "1.600519",
                "score": 0.45,
                "reason": "test",
            }
        ]
        await post_decision_pool_update("g1", "b1", decisions)
        assert len(upserted) == 1
        assert len(removed) == 0
        # expires_at 应在 7 天后
        args, kwargs = upserted[0]
        # kwargs["expires_at"] 是 timedelta(days=7)
        # 由于直接传 datetime 对象，验证类型
        assert "expires_at" in kwargs
        exp = kwargs["expires_at"]
        delta = exp - datetime.now()
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)
        print("[OK] buy → 写入 agent_pool (expires_in ~7d)")

    asyncio.run(_check())


def test_post_decision_pool_update_sell():
    """sell 决策后 agent_pool 应移除"""
    import asyncio

    async def _check():
        upserted = []
        removed = []

        async def fake_upsert(*a, **kw):
            upserted.append((a, kw))
            return None

        async def fake_remove(*a, **kw):
            removed.append((a, kw))
            return True

        db.PaperAgentPoolRepo.upsert = classmethod(lambda cls, *a, **kw: fake_upsert(*a, **kw))
        db.PaperAgentPoolRepo.remove = classmethod(lambda cls, *a, **kw: fake_remove(*a, **kw))

        decisions = [
            {
                "action": "sell",
                "code": "600519",
                "name": "茅台",
                "score": 0.0,
            }
        ]
        await post_decision_pool_update("g1", "b1", decisions)
        assert len(removed) == 1
        assert len(upserted) == 0
        print("[OK] sell → 从 agent_pool 移除")

    asyncio.run(_check())


def test_post_decision_pool_update_hold_strong_signal():
    """hold（含强信号）→ 不动 agent_pool（2026-07-02 修正）。

    旧行为：hold+score>0.1 会 upsert 续期 3 天 → 反而把标的钉死在池里，成为
    "每轮嚼同一批"锚定的成因之一。现在 hold 一律不动池，让 auto 候选自然老化 +
    被 candidate_refresh 轮换淘汰。
    """
    import asyncio

    async def _check():
        upserted = []
        removed = []

        async def fake_upsert(*a, **kw):
            upserted.append((a, kw))
            return None

        async def fake_remove(*a, **kw):
            removed.append((a, kw))
            return True

        db.PaperAgentPoolRepo.upsert = classmethod(lambda cls, *a, **kw: fake_upsert(*a, **kw))
        db.PaperAgentPoolRepo.remove = classmethod(lambda cls, *a, **kw: fake_remove(*a, **kw))

        decisions = [
            {"action": "hold", "code": "600519", "name": "茅台", "score": 0.15},
        ]
        await post_decision_pool_update("g1", "b1", decisions)
        assert len(upserted) == 0
        assert len(removed) == 0
        print("[OK] hold(强信号) → 不动 agent_pool（不再续期锚定）")

    asyncio.run(_check())


def test_post_decision_pool_update_hold_weak_no_action():
    """hold + 弱信号 → 不入池"""
    import asyncio

    async def _check():
        upserted = []
        removed = []

        async def fake_upsert(*a, **kw):
            upserted.append((a, kw))
            return None

        async def fake_remove(*a, **kw):
            removed.append((a, kw))
            return True

        db.PaperAgentPoolRepo.upsert = classmethod(lambda cls, *a, **kw: fake_upsert(*a, **kw))
        db.PaperAgentPoolRepo.remove = classmethod(lambda cls, *a, **kw: fake_remove(*a, **kw))

        decisions = [
            {"action": "hold", "code": "600519", "name": "茅台", "score": 0.05},  # < 0.1 弱信号
        ]
        await post_decision_pool_update("g1", "b1", decisions)
        assert len(upserted) == 0
        assert len(removed) == 0
        print("[OK] hold(弱信号) → 不动 agent_pool")

    asyncio.run(_check())


if __name__ == "__main__":
    test_pool_empty()
    test_pool_single_source()
    test_pool_dedup()
    test_pool_priority_order()
    test_pool_total_cap_50()
    test_pool_single_source_cap()
    test_pool_invalid_codes_filtered()
    test_pool_exclude_sources()
    test_post_decision_pool_update_buy()
    test_post_decision_pool_update_sell()
    test_post_decision_pool_update_hold_strong_signal()
    test_post_decision_pool_update_hold_weak_no_action()
    print("\n[SUCCESS] candidate_pool 全部 11 个测试通过！")
