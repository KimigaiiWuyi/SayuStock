"""股票池快照 —— 选股/组合行业用。"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from gsuid_core.logger import logger

from ..utils.constant import market_dict
from ..utils.eastmoney import EASTMONEY_REQUESTER
from ..utils.stock.request import get_menu, get_mtdata

SCREENER_FIELDS = [
    "f12",  # 代码
    "f14",  # 名称
    "f2",  # 最新价
    "f3",  # 涨跌幅%
    "f6",  # 成交额
    "f8",  # 换手%
    "f9",  # 市盈率
    "f10",  # 量比
    "f20",  # 总市值
    "f21",  # 流通市值
    "f100",  # 所属行业
]

_FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def _to_float(v: object) -> float | None:
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


def _dict_str(d: dict[str, Any], key: str) -> str:
    if key not in d or d[key] is None:
        return ""
    return str(d[key])


def rows_to_dataframe(diff: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for d in diff:
        if not isinstance(d, dict):
            continue
        code = _dict_str(d, "f12")
        name = _dict_str(d, "f14")
        if not code:
            continue
        industry = _dict_str(d, "f100") or "未分类"
        rows.append(
            {
                "code": code,
                "name": name,
                "price": _to_float(d["f2"] if "f2" in d else None),
                "pct": _to_float(d["f3"] if "f3" in d else None),
                "amount": _to_float(d["f6"] if "f6" in d else None),
                "turnover": _to_float(d["f8"] if "f8" in d else None),
                "pe": _to_float(d["f9"] if "f9" in d else None),
                "vol_ratio": _to_float(d["f10"] if "f10" in d else None),
                "mv": _to_float(d["f20"] if "f20" in d else None),
                "mv_circ": _to_float(d["f21"] if "f21" in d else None),
                "industry": industry,
            }
        )
    return pd.DataFrame(rows)


async def fetch_clist(
    fs: str,
    *,
    pz: int = 100,
    max_pages: int = 20,
    fid: str = "f20",
) -> pd.DataFrame:
    """按东财 fs 表达式拉取行情列表（可多页）。默认按总市值 f20 排序。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    all_diff: list[dict[str, Any]] = []
    for pn in range(1, max_pages + 1):
        params = [
            ("pz", str(pz)),
            ("po", "1"),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", fid),
            ("pn", str(pn)),
            ("fs", fs),
            ("fields", ",".join(SCREENER_FIELDS)),
        ]
        resp = await EASTMONEY_REQUESTER.stock_request(url, "GET", params=params)
        if isinstance(resp, int) or not isinstance(resp, dict):
            logger.warning(f"[stock_analysis] clist fail pn={pn} resp={resp}")
            break
        data = resp["data"] if "data" in resp and isinstance(resp["data"], dict) else {}
        diff: Any = data["diff"] if "diff" in data else []
        if not diff:
            break
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not isinstance(diff, list):
            break
        all_diff.extend([x for x in diff if isinstance(x, dict)])
        total = 0
        if "total" in data:
            tr = data["total"]
            if isinstance(tr, (int, float)):
                total = int(tr)
            elif isinstance(tr, str) and tr.isdigit():
                total = int(tr)
        if (total > 0 and len(all_diff) >= total) or len(diff) < pz:
            break
    return rows_to_dataframe(all_diff)


async def fetch_a_share_universe(*, max_pages: int = 20) -> pd.DataFrame:
    """沪深A 快照：按总市值降序分页（非涨幅榜，避免选股严重偏涨）。"""
    fs = market_dict["沪深A"] if "沪深A" in market_dict else "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"
    return await fetch_clist(fs, pz=100, max_pages=max_pages, fid="f20")


async def resolve_industry_fs(industry_name: str) -> tuple[str, str] | str:
    """行业名 → (板块名, fs)。找不到返回错误文本。"""
    menu = await get_menu(2)
    if not menu:
        return "❌无法获取行业板块列表"
    if industry_name in menu:
        code = menu[industry_name]
        fs = code if code.startswith("b:") else f"b:{code}"
        return industry_name, fs
    for name, code in menu.items():
        if industry_name in name or name in industry_name:
            fs = code if str(code).startswith("b:") else f"b:{code}"
            return name, fs
    return f"❌未找到行业「{industry_name}」，例如：半导体、白酒、银行"


async def resolve_concept_fs(concept_name: str) -> tuple[str, str] | str:
    menu = await get_menu(3)
    if not menu:
        return "❌无法获取概念板块列表"
    if concept_name in menu:
        code = menu[concept_name]
        fs = code if code.startswith("b:") else f"b:{code}"
        return concept_name, fs
    for name, code in menu.items():
        if concept_name in name or name in concept_name:
            fs = code if str(code).startswith("b:") else f"b:{code}"
            return name, fs
    return f"❌未找到概念「{concept_name}」"


async def fetch_board_members(board_fs: str) -> pd.DataFrame:
    return await fetch_clist(board_fs, pz=100, max_pages=10, fid="f3")


async def fetch_industry_pct_map() -> dict[str, float]:
    """行业名 → 当日涨跌幅%（板块指数）。"""
    resp = await get_mtdata("行业板块", is_loop=False, po=1, pz=100)
    out: dict[str, float] = {}
    if isinstance(resp, str) or not isinstance(resp, dict):
        return out
    data = resp["data"] if "data" in resp and isinstance(resp["data"], dict) else {}
    diff: Any = data["diff"] if "diff" in data else []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not isinstance(diff, list):
        return out
    for d in diff:
        if not isinstance(d, dict):
            continue
        name = _dict_str(d, "f14")
        pct = _to_float(d["f3"] if "f3" in d else None)
        if name and pct is not None:
            out[name] = pct
    return out
