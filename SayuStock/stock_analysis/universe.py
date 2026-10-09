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
from ..utils.sector_resolve import match_sector_menu
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
    """按 fs 表达式拉取行情列表（可多页）。默认按总市值排序。

    只剩 :func:`fetch_a_share_universe` 在用：全 A「按市值排序取前 N 只」这条
    取数语义端口没有等价能力（``board`` 单页上限 100，``limit=None`` 要全市场
    翻 50+ 页），理由见 ``doc/provider_coverage_matrix.md`` §11。
    """
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
    """沪深A 快照：按总市值降序分页（非涨幅榜，避免选股严重偏涨）。

    **唯一保留的东财直连**：端口 ``board`` 的 ``limit`` 会被 clist 的 ``pz``
    上限截到 100 行，``limit=None`` 又要全市场翻 50+ 页（实测 49s 且限流时中途
    断流），拿不到「按市值排序的前 ~2000 只」。属于端口能力缺口，非绕过。
    """
    fs = market_dict["沪深A"] if "沪深A" in market_dict else "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"
    df = await fetch_clist(fs, pz=100, max_pages=max_pages, sort_by_market_cap=True)
    # 这条仍直连东财 clist，不是端口链，来源固定。
    if not df.empty:
        df.attrs["provider"] = "eastmoney"
    return df


async def resolve_industry_fs(industry_name: str) -> tuple[str, str] | str:
    """行业名 → (菜单原名, 板块代码)。找不到返回错误文本。

    走 ``MarketDataPort.sector_menu``（东财 497 个行业）；匹配复用
    :func:`match_sector_menu`，比旧的「子串命中即返回」更能落到申万一级。
    """
    menu = await get_market().sector_menu("industry")
    if is_market_error(menu):
        return menu.message
    matched = match_sector_menu(industry_name, menu)
    if matched is None:
        return f"❌未找到行业「{industry_name}」，例如：半导体、白酒、银行"
    return matched


async def resolve_concept_fs(concept_name: str) -> tuple[str, str] | str:
    """概念名 → (菜单原名, 板块代码)。找不到返回错误文本。"""
    menu = await get_market().sector_menu("concept")
    if is_market_error(menu):
        return menu.message
    matched = match_sector_menu(concept_name, menu)
    if matched is None:
        return f"❌未找到概念「{concept_name}」"
    return matched


async def fetch_board_members(board_code: str) -> pd.DataFrame:
    """板块代码（``sector_menu`` 返回值）→ 成分股 DataFrame。

    代码与 ``sector_menu`` 同源，故 ``gn_xxx`` / ``hangye_xxx`` / ``BKxxxx``
    会被对应的源认下；不再直连 ``stock_request``，也不再被 10 页截断。

    注意：**板块代码空间是各源私有的**——东财限流时新浪/腾讯认不出 ``BKxxxx``，
    成分仍会整条失败（见 ``doc/provider_coverage_matrix.md`` §11.5）。
    """
    snap = await get_market().board(board_code, limit=None, sort_asc=False)
    if is_market_error(snap):
        logger.warning(f"[stock_analysis] 板块 {board_code} 成分拉取失败: {snap.message}")
        return pd.DataFrame()
    df = board_to_df(snap)
    if snap.provider:
        df.attrs["provider"] = snap.provider
    return df


async def fetch_industry_pct_map() -> tuple[dict[str, float], str | None]:
    """行业名 → 当日涨跌幅%，以及这条榜的 sourceBy。"""
    snap = await get_market().board("行业板块", limit=100, sort_asc=False)
    out: dict[str, float] = {}
    if is_market_error(snap):
        return out, None
    for row in snap.rows:
        if row.name and row.change_pct is not None:
            out[row.name] = row.change_pct
    return out, snap.provider
