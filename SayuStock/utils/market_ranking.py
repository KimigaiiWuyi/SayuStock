"""沪深 A 通用排行：业务门面（只经 MarketDataPort.rank_list，无 f*）。

供 AI 工具 ``get_market_ranking`` 与候选池等复用。
榜单只作扫描线索，**不能**单独作为买卖依据。
"""

from __future__ import annotations

from typing import Dict, List
from dataclasses import dataclass

from gsuid_core.logger import logger

from .market import RankBy, RankRow, RankSnapshot, get_market, is_market_error
from .market.models import RANKING_CAVEAT

# 再导出 caveat，兼容旧 import
__all__ = [
    "RANKING_CAVEAT",
    "RankSpec",
    "RANK_SPECS",
    "normalize_rank_by",
    "list_rank_by_options",
    "fetch_market_ranking",
    "rank_snapshot_to_payload",
]


@dataclass(frozen=True)
class RankSpec:
    """业务侧排行规格（无供应商字段）。"""

    key: str
    metric_label: str
    unit_hint: str
    default_high_first: bool


# 规范键 → 业务规格（与 Port RankBy 对齐）
RANK_SPECS: Dict[str, RankSpec] = {
    "main_inflow": RankSpec(
        key="main_inflow",
        metric_label="主力净流入",
        unit_hint="元（东财原值，常为净流入额）",
        default_high_first=True,
    ),
    "main_outflow": RankSpec(
        key="main_outflow",
        metric_label="主力净流入(升序=流出居前)",
        unit_hint="元；排序为升序，越靠前净流入越负/流出越大",
        default_high_first=False,
    ),
    "turnover": RankSpec(
        key="turnover",
        metric_label="换手率",
        unit_hint="%",
        default_high_first=True,
    ),
    "roe": RankSpec(
        key="roe",
        metric_label="ROE(净资产收益率)",
        unit_hint="%",
        default_high_first=True,
    ),
    "amount": RankSpec(
        key="amount",
        metric_label="成交额",
        unit_hint="元",
        default_high_first=True,
    ),
    "volume": RankSpec(
        key="volume",
        metric_label="成交量",
        unit_hint="股/手（东财原值）",
        default_high_first=True,
    ),
    "profit_yoy": RankSpec(
        key="profit_yoy",
        metric_label="净利润同比增长率",
        unit_hint="%",
        default_high_first=True,
    ),
}

_RANK_ALIASES: Dict[str, str] = {
    "main_inflow": "main_inflow",
    "fund_inflow": "main_inflow",
    "inflow": "main_inflow",
    "资金流入": "main_inflow",
    "主力净流入": "main_inflow",
    "主力流入": "main_inflow",
    "main_outflow": "main_outflow",
    "fund_outflow": "main_outflow",
    "outflow": "main_outflow",
    "资金流出": "main_outflow",
    "主力净流出": "main_outflow",
    "主力流出": "main_outflow",
    "turnover": "turnover",
    "换手率": "turnover",
    "换手": "turnover",
    "roe": "roe",
    "ROE": "roe",
    "净资产收益率": "roe",
    "净资产收益": "roe",
    "amount": "amount",
    "成交额": "amount",
    "volume": "volume",
    "成交量": "volume",
    "profit_yoy": "profit_yoy",
    "profit_growth": "profit_yoy",
    "net_profit_growth": "profit_yoy",
    "净利润增长率": "profit_yoy",
    "净利润同比": "profit_yoy",
    "净利同比": "profit_yoy",
}


def normalize_rank_by(rank_by: str) -> str | None:
    """将用户/模型输入规范为 RANK_SPECS 键；无法识别返回 None。"""
    raw = (rank_by or "").strip()
    if not raw:
        return None
    if raw in _RANK_ALIASES:
        return _RANK_ALIASES[raw]
    lower = raw.lower()
    if lower in _RANK_ALIASES:
        return _RANK_ALIASES[lower]
    if raw in RANK_SPECS:
        return raw
    if lower in RANK_SPECS:
        return lower
    return None


def list_rank_by_options() -> List[str]:
    return list(RANK_SPECS.keys())


def _row_to_item(row: RankRow) -> dict[str, object]:
    item: dict[str, object] = {
        "rank": row.rank,
        "code": row.code,
        "name": row.name,
        "price": row.price,
        "change_pct": row.change_pct,
        "metric": row.metric,
        "metric_label": row.metric_label,
        "turnover_pct": row.turnover_pct,
        "amount": row.amount,
        "volume": row.volume,
        "sector": row.sector,
    }
    if row.main_net_inflow is not None:
        item["main_net_inflow"] = row.main_net_inflow
    if row.main_net_inflow_pct is not None:
        item["main_net_inflow_pct"] = row.main_net_inflow_pct
    if row.super_large_net is not None:
        item["super_large_net"] = row.super_large_net
    if row.large_net is not None:
        item["large_net"] = row.large_net
    if row.debt_ratio is not None:
        item["debt_ratio"] = row.debt_ratio
    if row.revenue_yoy is not None:
        item["revenue_yoy"] = row.revenue_yoy
    if row.roe is not None:
        item["roe"] = row.roe
    return item


def rank_snapshot_to_payload(snap: RankSnapshot) -> dict[str, object]:
    return {
        "ok": True,
        "rank_by": snap.rank_by,
        "rank_by_label": snap.rank_by_label,
        "unit_hint": snap.unit_hint,
        "high_first": snap.high_first,
        "count": len(snap.rows),
        "caveat": snap.caveat or RANKING_CAVEAT,
        "items": [_row_to_item(r) for r in snap.rows],
    }


async def fetch_market_ranking(
    rank_by: str,
    *,
    n: int = 20,
    high_first: bool | None = None,
) -> dict[str, object]:
    """经 ``get_market().rank_list`` 拉取排行，返回工具用 dict。"""
    key = normalize_rank_by(rank_by)
    if key is None:
        return {
            "ok": False,
            "error": f"未知 rank_by={rank_by!r}",
            "supported": list_rank_by_options(),
            "aliases_hint": "资金流入/流出、换手率、ROE/净资产收益率、成交额、成交量、净利润增长率",
            "caveat": RANKING_CAVEAT,
            "items": [],
        }
    limit = max(1, min(int(n), 50))
    try:
        result = await get_market().rank_list(RankBy(key), limit=limit, high_first=high_first)
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[market_ranking] rank_list 异常 rank_by={key}: {e}")
        return {
            "ok": False,
            "error": str(e),
            "rank_by": key,
            "caveat": RANKING_CAVEAT,
            "items": [],
        }
    if is_market_error(result):
        return {
            "ok": False,
            "error": result.message,
            "rank_by": key,
            "caveat": RANKING_CAVEAT,
            "items": [],
        }
    return rank_snapshot_to_payload(result)
