"""单股交易卡片数据装配。"""

from __future__ import annotations

import re
import asyncio
from typing import Any
from dataclasses import field, dataclass

from gsuid_core.logger import logger

from .universe import fetch_industry_pct_map
from .technical import TechnicalReport, build_technical_report
from ..utils.eastmoney import EASTMONEY_REQUESTER
from ..utils.load_data import get_full_security_code
from ..utils.stock.request import get_gg
from ..utils.eastmoney_finance import get_financial_snapshot
from ..utils.stock.request_utils import get_code_id

_FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


@dataclass(slots=True)
class TradeCardData:
    name: str
    code: str
    secid: str
    price: float | None
    pct: float | None
    open_price: float | None
    prev_close: float | None
    amount: float | None
    turnover: float | None
    industry: str
    industry_pct: float | None
    technical: TechnicalReport | None
    finance: dict[str, Any] = field(default_factory=dict)
    pe: float | None = None
    pb: float | None = None
    mv: float | None = None
    high: float | None = None
    low: float | None = None


def _clean_name(name: str) -> str:
    return re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", name).strip() or name


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


def _pick(d: dict[str, Any], *keys: str) -> object:
    for k in keys:
        if k in d and d[k] not in (None, "", "-", "--"):
            return d[k]
    return None


async def _fetch_extra_quote(secid: str) -> dict[str, Any]:
    """补拉行业/PE/PB/市值/开盘/昨收等。"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    # f46 开盘, f60 昨收（见 doc/eastmoneyApi.md）
    fields = "f57,f58,f43,f170,f48,f168,f46,f60,f44,f45,f9,f23,f20,f100,f127,f162,f167"
    params = [
        ("secid", secid),
        ("fields", fields),
        ("fltt", "2"),
        ("invt", "2"),
    ]
    resp = await EASTMONEY_REQUESTER.stock_request(url, "GET", params=params)
    if isinstance(resp, int) or not isinstance(resp, dict):
        return {}
    if "data" not in resp or not isinstance(resp["data"], dict):
        return {}
    return resp["data"]


async def build_trade_card(query: str) -> TradeCardData | str:
    query = query.strip()
    if not query:
        return "❌请后跟股票代码或名称，例如：股票卡片 茅台"

    code_id = await get_code_id(query)
    if code_id is None:
        return f"❌未找到标的：{query}"
    try:
        secid = get_full_security_code(code_id[0])
    except ValueError:
        # QuoteID 已含市场前缀时原样使用
        secid = code_id[0] if "." in str(code_id[0]) else ""
    if not secid:
        return f"❌无法解析证券代码：{query}"
    pure_code = code_id[0].split(".")[-1] if "." in code_id[0] else code_id[0]
    raw_code = str(code_id[0])

    async def _kline() -> Any:
        return await get_gg(query, "single-stock-kline-101")

    async def _spot() -> Any:
        return await get_gg(query, "single-stock")

    async def _fin() -> dict[str, Any]:
        code6 = pure_code if pure_code.isdigit() else raw_code.split(".")[-1]
        if len(code6) > 6:
            code6 = code6[-6:]
        if not code6.isdigit():
            return {}
        try:
            snap = await get_financial_snapshot(code6)
        except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
            logger.warning(f"[card] finance fail: {e}")
            return {}
        return snap if isinstance(snap, dict) else {}

    spot, kline, extra, fin, ind_map = await asyncio.gather(
        _spot(),
        _kline(),
        _fetch_extra_quote(secid),
        _fin(),
        fetch_industry_pct_map(),
    )

    data: dict[str, Any] = {}
    if isinstance(spot, str):
        if isinstance(kline, str):
            return spot
        if isinstance(kline, dict) and "data" in kline and isinstance(kline["data"], dict):
            data = kline["data"]
    elif isinstance(spot, dict) and "data" in spot and isinstance(spot["data"], dict):
        data = spot["data"]

    name = _clean_name(str(_pick(data, "f58") or _pick(extra, "f58") or query))
    code = str(_pick(data, "f57") or _pick(extra, "f57") or pure_code)
    price = _ff(_pick(data, "f43")) or _ff(_pick(extra, "f43"))
    pct = _ff(_pick(data, "f170")) or _ff(_pick(extra, "f170"))
    open_price = _ff(_pick(data, "f46")) or _ff(_pick(extra, "f46"))
    prev_close = _ff(_pick(data, "f60")) or _ff(_pick(extra, "f60"))
    amount = _ff(_pick(data, "f48")) or _ff(_pick(extra, "f48"))
    turnover = _ff(_pick(data, "f168")) or _ff(_pick(extra, "f168"))
    high = _ff(_pick(data, "f44")) or _ff(_pick(extra, "f44"))
    low = _ff(_pick(data, "f45")) or _ff(_pick(extra, "f45"))
    pe = _ff(_pick(extra, "f9", "f162"))
    pb = _ff(_pick(extra, "f23", "f167"))
    mv = _ff(_pick(extra, "f20"))
    industry = str(_pick(extra, "f100", "f127") or _pick(data, "f100") or "未分类")
    industry_pct = ind_map[industry] if industry in ind_map else None

    technical: TechnicalReport | None = None
    if isinstance(kline, dict):
        kdata = kline["data"] if "data" in kline and isinstance(kline["data"], dict) else {}
        klines_raw = kdata["klines"] if "klines" in kdata and isinstance(kdata["klines"], list) else []
        klines = [str(x) for x in klines_raw]
        kname = str(kdata["name"] if "name" in kdata and kdata["name"] else name)
        rep = build_technical_report(name=kname, code=code, period_code="101", klines=klines)
        if isinstance(rep, TechnicalReport):
            technical = rep

    return TradeCardData(
        name=name,
        code=code,
        secid=secid,
        price=price,
        pct=pct,
        open_price=open_price,
        prev_close=prev_close,
        amount=amount,
        turnover=turnover,
        industry=industry,
        industry_pct=industry_pct,
        technical=technical,
        finance=fin,
        pe=pe,
        pb=pb,
        mv=mv,
        high=high,
        low=low,
    )
