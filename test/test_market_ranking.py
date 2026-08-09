"""通用排行：别名规范化 + Port 门面（不打真网）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import patch

from SayuStock.utils import market_ranking as mr
from SayuStock.utils.market.errors import MarketError
from SayuStock.utils.market.models import RANKING_CAVEAT, RankRow, RankSnapshot


def test_normalize_rank_by_aliases():
    assert mr.normalize_rank_by("资金流入") == "main_inflow"
    assert mr.normalize_rank_by("资金流出") == "main_outflow"
    assert mr.normalize_rank_by("换手率") == "turnover"
    assert mr.normalize_rank_by("ROE") == "roe"
    assert mr.normalize_rank_by("净资产收益率") == "roe"
    assert mr.normalize_rank_by("成交额") == "amount"
    assert mr.normalize_rank_by("成交量") == "volume"
    assert mr.normalize_rank_by("净利润增长率") == "profit_yoy"
    assert mr.normalize_rank_by("profit_growth") == "profit_yoy"
    assert mr.normalize_rank_by("") is None
    assert mr.normalize_rank_by("unknown_xyz") is None
    print("[OK] normalize_rank_by aliases")


def test_rank_specs_cover_user_metrics():
    keys = set(mr.RANK_SPECS.keys())
    assert keys == {
        "main_inflow",
        "main_outflow",
        "turnover",
        "roe",
        "amount",
        "volume",
        "profit_yoy",
    }
    # 业务层规格无供应商 f* 字段
    for spec in mr.RANK_SPECS.values():
        assert not hasattr(spec, "fid")
        assert "f" not in spec.metric_label.lower() or "ROE" in spec.metric_label
    assert mr.RANK_SPECS["main_outflow"].default_high_first is False
    print("[OK] RANK_SPECS business keys")


def test_quality_rank_limit_within_provider_cap():
    """质量池宽度不得超过 eastmoney rank_list 单页上限 100。"""
    from SayuStock.stock_papertrade.candidate_pool import QUALITY_RANK_LIMIT

    assert 1 <= QUALITY_RANK_LIMIT <= 100
    print(f"[OK] QUALITY_RANK_LIMIT={QUALITY_RANK_LIMIT} <= 100")


def test_caveat_mentions_not_sole_basis():
    text = mr.RANKING_CAVEAT
    assert "不是" in text or "绝非" in text or "不能" in text or "依据" in text
    assert "调节" in text or "做" in text or "失真" in text
    print("[OK] caveat")


def test_fetch_market_ranking_unknown():
    out = asyncio.run(mr.fetch_market_ranking("not_a_real_rank", n=5))
    assert out["ok"] is False
    assert out["items"] == []
    assert "supported" in out
    print("[OK] unknown rank_by")


def test_fetch_market_ranking_via_port():
    row = RankRow(
        rank=1,
        code="600519",
        name="贵州茅台",
        price=1600.0,
        change_pct=1.2,
        metric=1.5e10,
        metric_label="成交额",
        turnover_pct=0.5,
        amount=1.5e10,
        volume=1e6,
        sector="白酒",
    )
    snap = RankSnapshot(
        rank_by="amount",
        rank_by_label="成交额",
        unit_hint="元",
        high_first=True,
        caveat=RANKING_CAVEAT,
        rows=(row,),
    )

    @dataclass
    class _FakeMarket:
        async def rank_list(self, rank_by, *, limit=20, high_first=None):
            return snap

    with patch.object(mr, "get_market", return_value=_FakeMarket()):
        out = asyncio.run(mr.fetch_market_ranking("成交额", n=5))
    assert out["ok"] is True
    assert out["rank_by"] == "amount"
    items = out["items"]
    assert isinstance(items, list) and items
    first = items[0]
    assert isinstance(first, dict)
    assert first["code"] == "600519"
    assert first["metric"] == 1.5e10
    print("[OK] fetch via Port")


def test_fetch_market_ranking_port_error():
    class _FakeMarket:
        async def rank_list(self, rank_by, *, limit=20, high_first=None):
            return MarketError(code="network", message="down", provider="eastmoney")

    with patch.object(mr, "get_market", return_value=_FakeMarket()):
        out = asyncio.run(mr.fetch_market_ranking("amount", n=5))
    assert out["ok"] is False
    assert out["items"] == []
    print("[OK] port error")


def test_adapter_main_inflow_uses_f193_not_f184():
    """主力净比必须是 f193，营收同比才是 f184。"""
    from SayuStock.utils.market.enums import RankBy
    from SayuStock.utils.market.adapters.eastmoney.map_fields import RANK_FIELD
    from SayuStock.utils.market.adapters.eastmoney.parse_rank import (
        RANK_SPECS_INTERNAL,
        parse_rank_row,
    )

    assert RANK_FIELD["main_net_inflow_pct"] == "f193"
    assert RANK_FIELD["revenue_yoy"] == "f184"
    spec = RANK_SPECS_INTERNAL[RankBy.MAIN_INFLOW]
    assert RANK_FIELD["main_net_inflow_pct"] in spec.extra_fields
    row = parse_rank_row(
        {
            "f12": "600000",
            "f14": "浦发银行",
            "f2": 10.0,
            "f3": 1.0,
            "f62": 1e8,
            "f193": 3.5,
            "f184": 12.0,
            "f66": 1.0,
            "f69": 2.0,
            "f8": 1.0,
            "f6": 1e9,
            "f5": 100,
            "f100": "银行",
        },
        spec=spec,
        rank=1,
    )
    assert row is not None
    assert row.main_net_inflow_pct == 3.5
    assert row.metric == 1e8
    print("[OK] f193 net ratio mapping")


def test_parse_rank_payload_amount_order():
    from SayuStock.utils.market.enums import RankBy
    from SayuStock.utils.market.errors import is_market_error
    from SayuStock.utils.market.adapters.eastmoney.parse_rank import (
        RANK_SPECS_INTERNAL,
        parse_rank_payload,
    )

    payload = {
        "data": {
            "diff": [
                {"f12": "000001", "f14": "平安银行", "f2": 10, "f3": 0.1, "f6": 9e9, "f5": 1, "f8": 1, "f100": "银行"},
                {"f12": "600519", "f14": "茅台", "f2": 1600, "f3": 0.2, "f6": 2e10, "f5": 1, "f8": 1, "f100": "白酒"},
            ]
        }
    }
    snap = parse_rank_payload(
        payload,
        spec=RANK_SPECS_INTERNAL[RankBy.AMOUNT],
        high_first=True,
        limit=10,
    )
    assert not is_market_error(snap)
    assert snap.rows[0].code == "000001"
    assert snap.rows[0].metric == 9e9
    assert snap.rows[1].code == "600519"
    print("[OK] parse amount rows")
