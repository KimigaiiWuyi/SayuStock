"""通用排行榜领域模型（业务只消费本结构，不碰供应商 f*）。"""

from __future__ import annotations

from dataclasses import dataclass

RANKING_CAVEAT = (
    "榜单仅作扫描线索，绝非买卖依据。"
    "增长率/ROE/换手/资金流靠前≠优质；财务与量价均可人为调节或短期失真，"
    "须结合估值、现金流质量、行业周期、技术与事件等多维交叉验证。"
)


@dataclass(frozen=True, slots=True)
class RankRow:
    """单行排行结果；metric 为当前榜主指标。"""

    rank: int
    code: str
    name: str
    price: float | None
    change_pct: float | None
    metric: float | None
    metric_label: str
    turnover_pct: float | None
    amount: float | None
    volume: float | None
    sector: str | None
    main_net_inflow: float | None = None
    main_net_inflow_pct: float | None = None
    super_large_net: float | None = None
    large_net: float | None = None
    debt_ratio: float | None = None
    revenue_yoy: float | None = None
    roe: float | None = None


@dataclass(frozen=True, slots=True)
class RankSnapshot:
    rank_by: str
    rank_by_label: str
    unit_hint: str
    high_first: bool
    caveat: str
    rows: tuple[RankRow, ...]
