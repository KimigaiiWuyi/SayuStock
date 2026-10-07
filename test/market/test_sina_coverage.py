"""新浪补齐接口的离线解析测试（payload 来自 2026-10-08 实网采样）。

覆盖本轮新增：概念板块（newFLJK param=class）、资金流排行、
全 A 涨跌分布、两市成交额。不含任何 Cookie / 账号信息。
"""

from __future__ import annotations

from datetime import datetime

from SayuStock.utils.market.enums import RankBy, BoardKind, AssetClass
from SayuStock.utils.market.errors import is_market_error
from SayuStock.utils.market.models import Quote, SymbolRef, MarketTurnover
from SayuStock.utils.market.adapters.sina.parse import (
    industry_menu,
    parse_breadth_rows,
    _limit_threshold_pct,
    parse_money_flow_rank,
    parse_turnover_quotes,
    parse_industry_summary,
)

# newFLJK.php?param=class 实网采样（变量赋值文本解析后的 dict 形态）
FLJK_CLASS: dict[str, str] = {
    "gn_hwqc": "gn_hwqc,华为汽车,97,23.956875,-0.28739583333333,-1.1854175170251,2069201242,"
    "31890354954,sz002454,10.000,5.610,0.510,松芝股份",
    "gn_cxy": "gn_cxy,创新药,52,35.11,1.28,3.6221951858069,300266423,53116148987,sh688185,20.005,90.20,8.900,康希诺",
}

# MoneyFlow.ssl_bkzj_ssggzj 实网采样（fenlei=0，sort=netamount）
MONEY_FLOW_IN: list[dict[str, object]] = [
    {
        "symbol": "sh511010",
        "name": "国债ETF国泰",
        "trade": "140.7350",
        "changeratio": "-0.0000852594",
        "turnover": "7385.15",
        "amount": "5253821760.0000",
        "inamount": "4123889125.1000",
        "outamount": "972666738.8000",
        "netamount": "3151222386.3000",
    },
    {
        "symbol": "sz002402",
        "name": "江淮汽车",
        "trade": "41.83",
        "changeratio": "0.05492",
        "turnover": "2034.11",
        "amount": "2451664000.0000",
        "inamount": "2361664000.0000",
        "outamount": "1252000000.0000",
        "netamount": "1196600000.0000",
    },
]

# Market_Center.getHQNodeData?node=hs_a 实网采样（精简字段）
NODE_HS_A: list[dict[str, object]] = [
    {"code": "600000", "name": "浦发银行", "trade": "9.48", "changepercent": 3.27, "mktcap": 3157.39},
    {"code": "000001", "name": "平安银行", "trade": "11.20", "changepercent": -0.42, "mktcap": 2170.00},
    {"code": "600519", "name": "ST某某", "trade": "5.00", "changepercent": 4.90, "mktcap": 100.00},
    {"code": "300750", "name": "宁德时代", "trade": "260.00", "changepercent": 19.98, "mktcap": 11400.00},
    {"code": "920000", "name": "安徽凤凰", "trade": "14.33", "changepercent": 0.56, "mktcap": 30.00},
    {"code": "600001", "name": "某停牌", "trade": "0", "changepercent": 0.00, "mktcap": 10.00},
]


def test_fljk_concept_payload_parses_to_board_snapshot() -> None:
    snap = parse_industry_summary(FLJK_CLASS, kind=BoardKind.CONCEPT, title="概念板块")
    assert not is_market_error(snap)
    assert snap.kind is BoardKind.CONCEPT
    # 创新药涨幅高于华为汽车，应排在前
    assert snap.rows[0].code == "gn_cxy"
    assert snap.rows[0].name == "创新药"
    assert snap.rows[0].change_pct == 3.6221951858069
    assert snap.rows[0].amount == 53116148987.0
    assert snap.rows[0].lead_name == "康希诺"
    assert snap.rows[0].lead_change_pct == 20.005


def test_fljk_class_payload_builds_concept_menu() -> None:
    menu = industry_menu(FLJK_CLASS)
    assert not is_market_error(menu)
    assert menu["华为汽车"] == "gn_hwqc"
    assert menu["创新药"] == "gn_cxy"


def test_money_flow_rank_scales_changeratio_to_percent() -> None:
    """该接口 changeratio 是小数比例，必须 ×100 才能对齐内部模型百分数口径。"""
    snap = parse_money_flow_rank(MONEY_FLOW_IN, rank_by=RankBy.MAIN_INFLOW, high_first=True, limit=5)
    assert not is_market_error(snap)
    assert snap.unit_hint == "元"
    by_code = {r.code: r for r in snap.rows}
    # -0.0000852594 → -0.009%（四舍五入 3 位）
    assert by_code["sh511010"].change_pct == -0.009
    assert by_code["sz002402"].change_pct == 5.492
    assert by_code["sz002402"].metric == 1196600000.0
    assert by_code["sz002402"].metric_label == "主力净流入"


def test_money_flow_rank_rejects_non_money_flow_rank_by() -> None:
    snap = parse_money_flow_rank(MONEY_FLOW_IN, rank_by=RankBy.ROE, high_first=True, limit=5)
    assert is_market_error(snap)


def test_breadth_rows_count_by_board_limit_threshold() -> None:
    bar = parse_breadth_rows(NODE_HS_A)
    assert not is_market_error(bar)
    counts = {b.label: b.count for b in bar.buckets}
    # 浦发 +3.27 → 3~5；平安 -0.42 → 0~-1；ST某某 +4.90 → 主板阈值 5 → 涨停
    # 宁德 +19.98 → 创业板阈值 20 → 涨停；安徽凤凰 +0.56 → 0~1；停牌 0 → 平
    assert counts["涨停"] == 2
    assert counts["3~5"] == 1
    assert counts["0~1"] == 1
    assert counts["0~-1"] == 1
    assert counts["平"] == 1
    assert counts["跌停"] == 0
    assert sum(counts.values()) == len(NODE_HS_A)


def test_breadth_rows_empty_input_is_error() -> None:
    bar = parse_breadth_rows([])
    assert is_market_error(bar)


def test_limit_threshold_covers_each_board() -> None:
    assert _limit_threshold_pct("600000", "浦发银行") == 10.0
    assert _limit_threshold_pct("688001", "某科创") == 20.0
    assert _limit_threshold_pct("300750", "宁德时代") == 20.0
    assert _limit_threshold_pct("920000", "安徽凤凰") == 30.0
    assert _limit_threshold_pct("600001", "ST某某") == 5.0
    assert _limit_threshold_pct("600001", "*ST某某") == 5.0


def test_turnover_sums_sh_and_sz_and_leaves_prev_none() -> None:
    def _idx(secid: str, amount: float, day: str) -> Quote:
        return Quote(
            symbol=SymbolRef(
                code=secid.split(".")[-1],
                name="指数",
                asset_class=AssetClass.INDEX,
                exchange="CN",
                provider_symbol=secid,
                sec_type="指数",
            ),
            price=1.0,
            open=None,
            high=None,
            low=None,
            prev_close=1.0,
            change_pct=0.0,
            change_amount=0.0,
            volume=None,
            amount=amount,
            turnover_rate=None,
            pe=None,
            pb=None,
            market_cap=None,
            float_market_cap=None,
            industry=None,
            limit_up=None,
            limit_down=None,
            as_of=datetime.strptime(f"{day} 15:00:00", "%Y-%m-%d %H:%M:%S"),
        )

    mt = parse_turnover_quotes(
        [
            _idx("1.000001", 679398992445.0, "2026-09-30"),
            _idx("0.399001", 758590436937.0, "2026-09-30"),
        ]
    )
    assert not is_market_error(mt)
    assert mt.amount == 679398992445.0 + 758590436937.0
    # 单市场源拿不到昨成交额，必须是 None 而不是 0 填充
    assert mt.prev_amount is None
    assert isinstance(mt, MarketTurnover)
    assert mt.last_trade_date == datetime(2026, 9, 30, 15, 0, 0)
