"""股票池快照 —— 选股/组合行业用。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from gsuid_core.logger import logger

from ..utils.market import get_market, board_to_df, is_market_error
from ..utils.constant import market_dict
from ..utils.eastmoney import EASTMONEY_REQUESTER
from ..utils.market.enums import BoardKind
from ..utils.market.models import BoardSnapshot
from ..utils.stock.request import get_menu
from ..utils.market.adapters.eastmoney.map_fields import CLIST_SCREENER_FIELDS
from ..utils.market.adapters.eastmoney.parse_board import parse_board_row


def rows_to_dataframe(diff: list[dict[str, Any]]) -> pd.DataFrame:
    """东财 clist diff → 语义 DataFrame（解析仅在 adapter parse_board_row）。"""
    board_rows = []
    for d in diff:
        if not isinstance(d, dict):
            continue
        row = parse_board_row(d)
        if row is not None:
            board_rows.append(row)
    if not board_rows:
        return pd.DataFrame()
    snap = BoardSnapshot(kind=BoardKind.CUSTOM, title="clist", rows=tuple(board_rows))
    df = board_to_df(snap)
    # 选股期望列名
    rename = {}
    if "mv" in df.columns:
        rename["mv"] = "mv"
    return df


async def fetch_clist(
    fs: str,
    *,
    pz: int = 100,
    max_pages: int = 20,
    sort_by_market_cap: bool = True,
) -> pd.DataFrame:
    """按 fs 表达式拉取行情列表（可多页）。默认按总市值排序。"""
    # sort field id 仅在 EM transport 参数中使用（adapter 字段表）
    fid = CLIST_SCREENER_FIELDS[8] if sort_by_market_cap else CLIST_SCREENER_FIELDS[3]
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
            ("fields", ",".join(CLIST_SCREENER_FIELDS)),
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
    return await fetch_clist(fs, pz=100, max_pages=max_pages, sort_by_market_cap=True)


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
    return await fetch_clist(board_fs, pz=100, max_pages=10, sort_by_market_cap=False)


async def fetch_industry_pct_map() -> dict[str, float]:
    """行业名 → 当日涨跌幅%（板块指数）。"""
    snap = await get_market().board("行业板块", limit=100, sort_asc=False)
    out: dict[str, float] = {}
    if is_market_error(snap):
        return out
    for row in snap.rows:
        if row.name and row.change_pct is not None:
            out[row.name] = row.change_pct
    return out
