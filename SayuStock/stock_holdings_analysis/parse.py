"""持仓分析 · 标的列表解析（命令参数 / 自选）。"""

from __future__ import annotations

import re
from typing import List, Sequence
from dataclasses import dataclass

from .quota import MAX_SYMBOLS

_SPLIT_RE = re.compile(r"[\s,，、;；|/]+")


@dataclass(frozen=True)
class SymbolListResult:
    symbols: List[str]
    total_before_cap: int
    source: str  # "manual" | "watchlist"
    truncated: bool

    @property
    def cap_warning(self) -> str:
        if not self.truncated:
            return ""
        return f"列表共 {self.total_before_cap} 只，本次仅分析前 {MAX_SYMBOLS} 只（{self.source}）。"


def split_manual_symbols(text: str) -> List[str]:
    """拆分命令后缀为标的 token（保序去重）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SPLIT_RE.split(raw) if p and p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def cap_symbols(symbols: Sequence[str], *, limit: int = MAX_SYMBOLS) -> tuple[List[str], bool]:
    lst = list(symbols)
    if len(lst) <= limit:
        return lst, False
    return lst[:limit], True


def build_symbol_list(
    *,
    manual_text: str,
    watchlist: Sequence[str] | None,
) -> SymbolListResult | None:
    """优先手填；否则自选。都空则 None。"""
    manual = split_manual_symbols(manual_text)
    if manual:
        capped, truncated = cap_symbols(manual)
        return SymbolListResult(
            symbols=capped,
            total_before_cap=len(manual),
            source="manual",
            truncated=truncated,
        )
    wl = [str(x).strip() for x in (watchlist or []) if str(x).strip()]
    # 自选也可能用 _ 拼接片段，上层应已 convert_list
    if not wl:
        return None
    capped, truncated = cap_symbols(wl)
    return SymbolListResult(
        symbols=capped,
        total_before_cap=len(wl),
        source="watchlist",
        truncated=truncated,
    )
