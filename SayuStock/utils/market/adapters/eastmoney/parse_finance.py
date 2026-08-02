"""F10 main_financial 行 → FinancialSnapshot。"""

from __future__ import annotations

from typing import Mapping

from ...errors import MarketError, empty_error, parse_error
from ...models import FinancialSnapshot
from .json_util import opt_str, opt_float, as_mapping
from .map_fields import PROVIDER

_MAIN_KEYS = {
    "roe": "ROEJQ",
    "revenue_yoy": "TOTALOPERATEREVETZ",
    "profit_yoy": "PARENTNETPROFITTZ",
    "gross_margin": "XSMLL",
    "net_margin": "XSJLL",
    "debt_ratio": "ZCFZL",
    "eps": "EPSJB",
    "bps": "BPS",
    "net_interest_margin": "NET_INTEREST_MARGIN",
}


def parse_financial_snapshot_row(code: str, latest: Mapping[str, object]) -> FinancialSnapshot:
    fields: dict[str, float | None] = {k: opt_float(latest, v) for k, v in _MAIN_KEYS.items()}
    industry_type: str = "bank" if fields["net_interest_margin"] is not None else "standard"
    report_date = opt_str(latest, "REPORT_DATE") or ""
    report_date = report_date[:10]
    missing = tuple(sorted(k for k, v in fields.items() if v is None and k != "net_interest_margin"))
    if industry_type != "bank":
        missing = tuple(k for k in missing)
    return FinancialSnapshot(
        code=code,
        report_date=report_date,
        roe=fields["roe"],
        revenue_yoy=fields["revenue_yoy"],
        profit_yoy=fields["profit_yoy"],
        gross_margin=fields["gross_margin"],
        net_margin=fields["net_margin"],
        debt_ratio=fields["debt_ratio"],
        eps=fields["eps"],
        bps=fields["bps"],
        net_interest_margin=fields["net_interest_margin"] if industry_type == "bank" else None,
        industry_type="bank" if industry_type == "bank" else "standard",
        missing_fields=missing,
    )


def parse_financial_snapshot_payload(code: str, rows: object) -> FinancialSnapshot | MarketError:
    if not isinstance(rows, list) or not rows:
        return empty_error("财务行为空", provider=PROVIDER)
    first = rows[0]
    mapping = as_mapping(first)
    if mapping is None:
        return parse_error("财务行非对象", provider=PROVIDER)
    return parse_financial_snapshot_row(code, mapping)
