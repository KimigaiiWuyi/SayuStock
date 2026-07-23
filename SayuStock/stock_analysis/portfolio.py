"""组合行业集中度风险。"""

from __future__ import annotations

import re
import asyncio
from typing import Any
from dataclasses import field, dataclass

from gsuid_core.logger import logger

from .universe import fetch_industry_pct_map
from ..utils.load_data import get_full_security_code
from ..utils.stock.request import get_gg
from ..utils.stock.request_utils import get_code_id

_FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


@dataclass(slots=True)
class HoldingRow:
    code: str
    name: str
    industry: str
    weight: float
    pct: float | None
    price: float | None


@dataclass(slots=True)
class PortfolioRiskReport:
    holdings: list[HoldingRow]
    industry_weights: dict[str, float]
    industry_pct: dict[str, float]
    hhi: float
    top1_name: str
    top1_weight: float
    top3_weight: float
    effective_n: float
    risk_level: str
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hhi": self.hhi,
            "top1": {"industry": self.top1_name, "weight": self.top1_weight},
            "top3_weight": self.top3_weight,
            "effective_n": self.effective_n,
            "risk_level": self.risk_level,
            "industry_weights": dict(self.industry_weights),
            "messages": list(self.messages),
            "holdings": [
                {
                    "code": h.code,
                    "name": h.name,
                    "industry": h.industry,
                    "weight": h.weight,
                    "pct": h.pct,
                }
                for h in self.holdings
            ],
        }


def _ff(v: object) -> float | None:
    if v is None or v == "" or v == "-" or v == "--":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s or s in {"-", "--"}:
            return None
        if _FLOAT_RE.fullmatch(s):
            return float(s)
        return None
    return None


def _risk_level(hhi: float, top1: float) -> str:
    # 等权 n 行业时 HHI=1/n：3 行业≈0.33 视为适中，勿误报「集中」
    if hhi >= 0.50 or top1 >= 0.55:
        return "极集中"
    if hhi >= 0.38 or top1 >= 0.40:
        return "集中"
    if hhi >= 0.22 or top1 >= 0.30:
        return "适中"
    return "分散"


async def _fetch_extra_industry(secid: str) -> str:
    """个股 get 默认字段未必含 f100，单独补拉行业。"""
    from ..utils.eastmoney import EASTMONEY_REQUESTER

    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = [
        ("secid", secid),
        ("fields", "f57,f58,f100,f127"),
        ("fltt", "2"),
        ("invt", "2"),
    ]
    resp = await EASTMONEY_REQUESTER.stock_request(url, "GET", params=params)
    if isinstance(resp, int) or not isinstance(resp, dict):
        return "未分类"
    if "data" not in resp or not isinstance(resp["data"], dict):
        return "未分类"
    data = resp["data"]
    ind = ""
    if "f100" in data and data["f100"] not in (None, "", "-", "--"):
        ind = str(data["f100"]).strip()
    elif "f127" in data and data["f127"] not in (None, "", "-", "--"):
        ind = str(data["f127"]).strip()
    if not ind or ind in {"-", "--", "None"}:
        return "未分类"
    return ind


async def _fetch_one(code: str) -> HoldingRow | None:
    try:
        raw = await get_gg(code, "single-stock")
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[portfolio] fetch {code} fail: {e}")
        return None

    if isinstance(raw, str) or not isinstance(raw, dict):
        return None
    if "data" not in raw or not isinstance(raw["data"], dict):
        return None
    data = raw["data"]

    name = str(data["f58"] if "f58" in data and data["f58"] else code)
    if " (" in name:
        name = name.split(" (")[0]
    price_f = _ff(data["f43"] if "f43" in data else None)
    pct_f = _ff(data["f170"] if "f170" in data else None)

    industry = ""
    if "f100" in data and data["f100"] not in (None, "", "-", "--"):
        industry = str(data["f100"]).strip()
    elif "f127" in data and data["f127"] not in (None, "", "-", "--"):
        industry = str(data["f127"]).strip()

    if not industry or industry in {"-", "--", "None", "未分类"}:
        cid = await get_code_id(code)
        if cid:
            try:
                secid = get_full_security_code(cid[0])
            except ValueError:
                secid = cid[0] if "." in str(cid[0]) else ""
            if secid:
                industry = await _fetch_extra_industry(secid)
        if not industry or industry in {"-", "--"}:
            industry = "未分类"

    stock_code = str(data["f57"] if "f57" in data and data["f57"] else code)
    return HoldingRow(
        code=stock_code,
        name=name,
        industry=industry,
        weight=0.0,
        pct=pct_f,
        price=price_f,
    )


async def analyze_portfolio(codes: list[str]) -> PortfolioRiskReport | str:
    codes = [c.strip() for c in codes if c and c.strip()]
    if not codes:
        return "❌组合为空，请先「添加自选」或后跟股票代码"
    if len(codes) > 30:
        codes = codes[:30]

    rows = await asyncio.gather(*[_fetch_one(c) for c in codes])
    holdings = [r for r in rows if r is not None]
    if not holdings:
        return "❌无法获取组合行情"

    n = len(holdings)
    w = 1.0 / n
    for h in holdings:
        h.weight = w

    industry_weights: dict[str, float] = {}
    for h in holdings:
        if h.industry in industry_weights:
            industry_weights[h.industry] += h.weight
        else:
            industry_weights[h.industry] = h.weight

    hhi = sum(x * x for x in industry_weights.values())
    sorted_ind = sorted(industry_weights.items(), key=lambda x: -x[1])
    top1_name, top1_w = sorted_ind[0]
    top3_w = sum(x[1] for x in sorted_ind[:3])
    effective_n = (1.0 / hhi) if hhi > 0 else 0.0
    level = _risk_level(hhi, top1_w)

    ind_pct = await fetch_industry_pct_map()
    industry_day: dict[str, float] = {}
    for ind_name in industry_weights:
        if ind_name in ind_pct:
            industry_day[ind_name] = ind_pct[ind_name]
        else:
            members = [h for h in holdings if h.industry == ind_name and h.pct is not None]
            if members:
                industry_day[ind_name] = sum(h.pct or 0 for h in members) / len(members)

    messages: list[str] = [
        f"持仓 {n} 只（等权），覆盖 {len(industry_weights)} 个行业标签",
        f"HHI={hhi:.3f}，有效行业数≈{effective_n:.1f}，风险等级【{level}】",
        f"最大行业：{top1_name} {top1_w * 100:.1f}%；Top3 累计 {top3_w * 100:.1f}%",
    ]
    if level in ("集中", "极集中"):
        messages.append(f"注意：行业过度集中于「{top1_name}」，同涨同跌风险偏高")
    elif level == "分散":
        messages.append("行业分布较分散")

    return PortfolioRiskReport(
        holdings=holdings,
        industry_weights=dict(sorted_ind),
        industry_pct=industry_day,
        hhi=hhi,
        top1_name=top1_name,
        top1_weight=top1_w,
        top3_weight=top3_w,
        effective_n=effective_n,
        risk_level=level,
        messages=messages,
    )
