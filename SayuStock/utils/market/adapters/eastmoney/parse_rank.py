"""clist 排行 payload → RankSnapshot（f* 仅本文件内解析）。"""

from __future__ import annotations

from typing import Mapping, Sequence
from dataclasses import dataclass

from ...enums import RankBy
from ...errors import MarketError, empty_error, parse_error
from ...models import RANKING_CAVEAT, RankRow, RankSnapshot
from .json_util import opt_str, opt_float, as_mapping, require_mapping
from .map_fields import PROVIDER, RANK_SORT, RANK_FIELD


@dataclass(frozen=True, slots=True)
class RankSpecInternal:
    """adapter 内部排行规格（含 fid / 字段表）。"""

    key: RankBy
    sort_fid: str
    metric_field: str
    metric_label: str
    unit_hint: str
    default_high_first: bool
    extra_fields: tuple[str, ...] = ()


# 规范键 → 内部规格；f193=主力净比，f184=营收同比（勿混用）
RANK_SPECS_INTERNAL: dict[RankBy, RankSpecInternal] = {
    RankBy.MAIN_INFLOW: RankSpecInternal(
        key=RankBy.MAIN_INFLOW,
        sort_fid=RANK_SORT["main_net_inflow"],
        metric_field=RANK_FIELD["main_net_inflow"],
        metric_label="主力净流入",
        unit_hint="元（东财原值，常为净流入额）",
        default_high_first=True,
        extra_fields=(
            RANK_FIELD["main_net_inflow_pct"],
            RANK_FIELD["super_large_net"],
            RANK_FIELD["large_net"],
        ),
    ),
    RankBy.MAIN_OUTFLOW: RankSpecInternal(
        key=RankBy.MAIN_OUTFLOW,
        sort_fid=RANK_SORT["main_net_inflow"],
        metric_field=RANK_FIELD["main_net_inflow"],
        metric_label="主力净流入(升序=流出居前)",
        unit_hint="元；排序为升序，越靠前净流入越负/流出越大",
        default_high_first=False,
        extra_fields=(
            RANK_FIELD["main_net_inflow_pct"],
            RANK_FIELD["super_large_net"],
            RANK_FIELD["large_net"],
        ),
    ),
    RankBy.TURNOVER: RankSpecInternal(
        key=RankBy.TURNOVER,
        sort_fid=RANK_SORT["turnover_rate"],
        metric_field=RANK_FIELD["turnover_rate"],
        metric_label="换手率",
        unit_hint="%",
        default_high_first=True,
    ),
    RankBy.ROE: RankSpecInternal(
        key=RankBy.ROE,
        sort_fid=RANK_SORT["roe"],
        metric_field=RANK_FIELD["roe"],
        metric_label="ROE(净资产收益率)",
        unit_hint="%",
        default_high_first=True,
        extra_fields=(RANK_FIELD["debt_ratio"], RANK_FIELD["industry"]),
    ),
    RankBy.AMOUNT: RankSpecInternal(
        key=RankBy.AMOUNT,
        sort_fid=RANK_SORT["amount"],
        metric_field=RANK_FIELD["amount"],
        metric_label="成交额",
        unit_hint="元",
        default_high_first=True,
        extra_fields=(RANK_FIELD["volume"],),
    ),
    RankBy.VOLUME: RankSpecInternal(
        key=RankBy.VOLUME,
        sort_fid=RANK_SORT["volume"],
        metric_field=RANK_FIELD["volume"],
        metric_label="成交量",
        unit_hint="股/手（东财原值）",
        default_high_first=True,
        extra_fields=(RANK_FIELD["amount"],),
    ),
    RankBy.PROFIT_YOY: RankSpecInternal(
        key=RankBy.PROFIT_YOY,
        sort_fid=RANK_SORT["profit_yoy"],
        metric_field=RANK_FIELD["profit_yoy"],
        metric_label="净利润同比增长率",
        unit_hint="%",
        default_high_first=True,
        extra_fields=(
            RANK_FIELD["revenue_yoy"],
            RANK_FIELD["roe"],
            RANK_FIELD["industry"],
        ),
    ),
}

_BASE_FIELDS: tuple[str, ...] = (
    RANK_FIELD["code"],
    RANK_FIELD["name"],
    RANK_FIELD["price"],
    RANK_FIELD["change_pct"],
    RANK_FIELD["turnover_rate"],
    RANK_FIELD["amount"],
    RANK_FIELD["volume"],
    RANK_FIELD["industry"],
)


def rank_fields_csv(spec: RankSpecInternal) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for f in _BASE_FIELDS + (spec.metric_field,) + spec.extra_fields:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ",".join(ordered)


def resolve_rank_by(rank_by: RankBy | str) -> RankBy | None:
    if isinstance(rank_by, RankBy):
        return rank_by
    raw = (rank_by or "").strip()
    if not raw:
        return None
    for m in RankBy:
        if raw == m.value or raw.lower() == m.value:
            return m
    return None


def _iter_diff(diff: object) -> Sequence[Mapping[str, object]]:
    if isinstance(diff, list):
        return [r for r in diff if isinstance(r, dict)]
    if isinstance(diff, dict):
        return [v for v in diff.values() if isinstance(v, dict)]
    return []


def parse_rank_row(row: Mapping[str, object], *, spec: RankSpecInternal, rank: int) -> RankRow | None:
    code = (opt_str(row, RANK_FIELD["code"]) or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        return None
    metric = opt_float(row, spec.metric_field)
    if metric is None:
        return None
    name = opt_str(row, RANK_FIELD["name"]) or code
    main_inflow = main_pct = super_net = large_net = None
    debt = rev_yoy = roe_v = None
    if spec.key in (RankBy.MAIN_INFLOW, RankBy.MAIN_OUTFLOW):
        main_inflow = opt_float(row, RANK_FIELD["main_net_inflow"])
        main_pct = opt_float(row, RANK_FIELD["main_net_inflow_pct"])
        super_net = opt_float(row, RANK_FIELD["super_large_net"])
        large_net = opt_float(row, RANK_FIELD["large_net"])
    if spec.key == RankBy.ROE:
        debt = opt_float(row, RANK_FIELD["debt_ratio"])
    if spec.key == RankBy.PROFIT_YOY:
        rev_yoy = opt_float(row, RANK_FIELD["revenue_yoy"])
        roe_v = opt_float(row, RANK_FIELD["roe"])
    return RankRow(
        rank=rank,
        code=code,
        name=name,
        price=opt_float(row, RANK_FIELD["price"]),
        change_pct=opt_float(row, RANK_FIELD["change_pct"]),
        metric=metric,
        metric_label=spec.metric_label,
        turnover_pct=opt_float(row, RANK_FIELD["turnover_rate"]),
        amount=opt_float(row, RANK_FIELD["amount"]),
        volume=opt_float(row, RANK_FIELD["volume"]),
        sector=opt_str(row, RANK_FIELD["industry"]),
        main_net_inflow=main_inflow,
        main_net_inflow_pct=main_pct,
        super_large_net=super_net,
        large_net=large_net,
        debt_ratio=debt,
        revenue_yoy=rev_yoy,
        roe=roe_v,
    )


def parse_rank_payload(
    payload: object,
    *,
    spec: RankSpecInternal,
    high_first: bool,
    limit: int,
) -> RankSnapshot | MarketError:
    root = as_mapping(payload)
    if root is None:
        return parse_error("rank payload 非对象", provider=PROVIDER)
    data = require_mapping(root, "data")
    if data is None:
        return empty_error("rank data 为空", provider=PROVIDER)
    if "diff" not in data:
        return empty_error("rank 缺少 diff", provider=PROVIDER)
    rows: list[RankRow] = []
    for item in _iter_diff(data["diff"]):
        row = parse_rank_row(item, spec=spec, rank=len(rows) + 1)
        if row is None:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    if not rows:
        return empty_error("rank 行为空", provider=PROVIDER)
    return RankSnapshot(
        rank_by=spec.key.value,
        rank_by_label=spec.metric_label,
        unit_hint=spec.unit_hint,
        high_first=high_first,
        caveat=RANKING_CAVEAT,
        rows=tuple(rows),
    )
