"""把用户输入的行业/概念名对上东财板块菜单。

行业云图：成分代码优先仓库内申万三级表；L3 缺表时才走 BK 名单 30 天缓存。
行情只复用大盘 hotmap，禁止每次按 BK 翻页拉成分行情。
"""

from __future__ import annotations

from typing import Literal

from .market import get_market, is_market_error
from .constant import ErroText, chinese_stocks
from .stock.utils import async_file_cache
from .market.models import BoardSnapshot

_QUERY_TAILS = ("云图", "板块", "行业", "概念")
_LEVEL_TAILS = ("Ⅲ", "Ⅱ", "III", "II")
_INDUSTRY_L1 = frozenset(info["industry_l1"] for info in chinese_stocks.values() if info["industry_l1"] != "未知")
# 成分股名单几乎不改；30 天够用，避免行业云图反复打 clist 翻页
INDUSTRY_MEMBER_CACHE_MINUTES = 30 * 24 * 60
_member_mem: dict[str, list[str]] = {}


def _strip_known_tails(text: str, tails: tuple[str, ...]) -> str:
    stripped = text.strip()
    changed = True
    while changed:
        changed = False
        for tail in tails:
            if len(stripped) > len(tail) and stripped.endswith(tail):
                stripped = stripped[: -len(tail)].strip()
                changed = True
                break
    return stripped


def normalize_sector_query(query: str) -> str:
    text = _strip_known_tails(query.strip(), _QUERY_TAILS)
    text = _strip_known_tails(text, _LEVEL_TAILS)
    return text.casefold()


def match_sector_menu(query: str, menu: dict[str, str]) -> tuple[str, str] | None:
    """返回 (菜单原名, 板块代码)；精确名优先于「水泥制品」这类前缀扩展。"""
    raw = query.strip()
    if not raw:
        return None

    probes = [raw, raw.upper()]
    stripped = _strip_known_tails(raw, _QUERY_TAILS)
    if stripped and stripped not in probes:
        probes.append(stripped)
        probes.append(stripped.upper())

    for probe in probes:
        if probe in menu:
            return probe, menu[probe]
        probe_upper = probe.upper()
        for name, code in menu.items():
            if name.upper() == probe_upper:
                return name, code

    needle = normalize_sector_query(raw)
    if not needle:
        return None

    ranked: list[tuple[int, int, int, str, str]] = []
    for name, code in menu.items():
        key = normalize_sector_query(name)
        if not key:
            continue
        # 前缀模糊时优先申万一级，避免「医药」落到「医药商业」
        l1_penalty = 0 if name in _INDUSTRY_L1 else 1
        if key == needle:
            ranked.append((0, 0, len(name), name, code))
        elif key.startswith(needle):
            ranked.append((1, l1_penalty, len(name), name, code))
        elif needle in key:
            ranked.append((2, l1_penalty, len(name), name, code))
    if not ranked:
        return None
    ranked.sort()
    best = ranked[0]
    return best[3], best[4]


def local_industry_member_codes(canonical_name: str) -> list[str] | None:
    """申万一/二/三级用仓库内 chinese_stocks，不打东财成分接口。"""
    l1_codes = [code for code, info in chinese_stocks.items() if info["industry_l1"] == canonical_name]
    if l1_codes:
        return l1_codes
    l2_codes = [code for code, info in chinese_stocks.items() if info["industry_l2"] == canonical_name]
    if l2_codes:
        return l2_codes
    l3_codes = [
        code for code, info in chinese_stocks.items() if "industry_l3" in info and info["industry_l3"] == canonical_name
    ]
    if l3_codes:
        return l3_codes
    needle = normalize_sector_query(canonical_name)
    if not needle:
        return None
    fuzzy_l2 = [code for code, info in chinese_stocks.items() if normalize_sector_query(info["industry_l2"]) == needle]
    if fuzzy_l2:
        return fuzzy_l2
    fuzzy_l3 = [
        code
        for code, info in chinese_stocks.items()
        if "industry_l3" in info and normalize_sector_query(info["industry_l3"]) == needle
    ]
    if fuzzy_l3:
        return fuzzy_l3
    return None


def _codes_from_payload(payload: object) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    if "codes" not in payload:
        return None
    raw_codes = payload["codes"]
    if not isinstance(raw_codes, list):
        return None
    codes = [item for item in raw_codes if isinstance(item, str) and item]
    if not codes:
        return None
    return codes


@async_file_cache(
    market="industry-members",
    sector="{bk_code}",
    suffix="json",
    minutes=INDUSTRY_MEMBER_CACHE_MINUTES,
)
async def _fetch_board_member_payload(bk_code: str) -> dict[str, object] | str:
    """只缓存代码列表；涨跌幅不进这份缓存。错误 str 不会落盘。"""
    snap = await get_market().board(bk_code, limit=None, sort_asc=False)
    if is_market_error(snap):
        return snap.message
    codes = [row.code for row in snap.rows if row.code]
    if not codes:
        return ErroText["notData"]
    return {"codes": codes}


async def load_board_member_codes(bk_code: str) -> list[str] | str:
    if bk_code in _member_mem:
        return _member_mem[bk_code]
    payload = await _fetch_board_member_payload(bk_code)
    if isinstance(payload, str):
        return payload
    codes = _codes_from_payload(payload)
    if codes is None:
        return ErroText["notData"]
    _member_mem[bk_code] = codes
    return codes


def filter_snapshot_by_codes(snap: BoardSnapshot, codes: list[str], title: str) -> BoardSnapshot | str:
    wanted = frozenset(codes)
    rows = tuple(row for row in snap.rows if row.code in wanted)
    if not rows:
        return ErroText["notData"]
    return BoardSnapshot(kind=snap.kind, title=title, rows=rows)


async def fetch_industry_from_hotmap(query: str) -> tuple[str, BoardSnapshot | str]:
    """成分名单（本地或 30 天缓存）∩ 大盘 hotmap，不并发拉个股。"""
    port = get_market()
    menu = await port.sector_menu("industry")
    if is_market_error(menu):
        return query.strip(), menu.message
    matched = match_sector_menu(query, menu)
    if matched is None:
        return query.strip(), ErroText["typemap"]
    name, bk_code = matched
    local = local_industry_member_codes(name)
    if local is not None:
        codes = local
    else:
        loaded = await load_board_member_codes(bk_code)
        if isinstance(loaded, str):
            return name, loaded
        codes = loaded
    hot = await port.hotmap()
    if is_market_error(hot):
        return name, hot.message
    return name, filter_snapshot_by_codes(hot, codes, title=name)


async def fetch_named_board(
    kind: Literal["industry", "concept"],
    query: str,
) -> tuple[str, BoardSnapshot | str]:
    if kind == "industry":
        return await fetch_industry_from_hotmap(query)
    port = get_market()
    menu = await port.sector_menu(kind)
    if is_market_error(menu):
        return query.strip(), menu.message
    matched = match_sector_menu(query, menu)
    if matched is None:
        return query.strip(), ErroText["typemap"]
    name, code = matched
    snap = await port.board(code, limit=None, sort_asc=False)
    if is_market_error(snap):
        return name, snap.message
    return name, snap
