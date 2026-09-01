"""场外基金路由粗判：无 HTTP，供 Composite / data 层共用。"""

from __future__ import annotations

import re

from .enums import AssetClass
from .models import SymbolRef, KlineSeries

_LISTED_ETF_LOF = re.compile(r"^(15|16|50|51|52|56|58)\d{4}$")
_A_SHARE_PREFIX = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "302",
    "600",
    "601",
    "603",
    "605",
    "688",
)
_FUND_NAME_MARKERS = (
    "混合",
    "债券",
    "货币",
    "灵活配置",
    "股票型",
    "指数型",
    "联接",
    "FOF",
    "定开",
    "持有期",
)


def extract_fund_code(query: str) -> str | None:
    text = query.strip()
    if re.fullmatch(r"150\.\d{6}", text):
        return text.split(".", 1)[1]
    if re.fullmatch(r"\d{6}", text):
        return text
    return None


def maybe_otc_fund_query(query: str) -> bool:
    """明显是股票/场内 ETF 的直接否，避免对比链路多一次搜索。"""
    text = query.strip()
    if not text:
        return False
    if re.fullmatch(r"150\.\d{6}", text):
        return True
    if re.fullmatch(r"\d{6}", text):
        if _LISTED_ETF_LOF.match(text):
            return False
        if text.startswith(_A_SHARE_PREFIX):
            return False
        if text.startswith(("43", "83", "87", "88", "92")):
            return False
        return True
    return any(marker in text for marker in _FUND_NAME_MARKERS)


def is_otc_fund_symbol(ref: SymbolRef) -> bool:
    return ref.asset_class == AssetClass.FUND or ref.provider_symbol.startswith("150.")


def is_otc_fund_series(series: KlineSeries) -> bool:
    return is_otc_fund_symbol(series.symbol)
