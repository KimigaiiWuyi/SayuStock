"""自动选股：简单 DSL + DataFrame 过滤。"""

from __future__ import annotations

import re
from dataclasses import field, dataclass

import pandas as pd

from .universe import (
    resolve_concept_fs,
    fetch_board_members,
    resolve_industry_fs,
    fetch_a_share_universe,
)

# 中文条件名 → 列名（不含「跌幅」，避免 跌幅>2 被当成涨跌幅>2）
FIELD_MAP: dict[str, str] = {
    "涨跌幅": "pct",
    "涨幅": "pct",
    "市盈率": "pe",
    "pe": "pe",
    "PE": "pe",
    "市值": "mv_yi",
    "总市值": "mv_yi",
    "换手": "turnover",
    "换手率": "turnover",
    "量比": "vol_ratio",
    "成交额": "amount_yi",
    "价格": "price",
    "现价": "price",
}

_OP_NORMALIZE = {"＝": "=", "＞": ">", "＜": "<"}


@dataclass(slots=True)
class ScreenerResult:
    query: str
    scope: str
    total_pool: int
    matched: int
    shown: int
    df: pd.DataFrame
    filters_desc: list[str] = field(default_factory=list)
    error: str = ""


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    out["mv_yi"] = out["mv"].apply(lambda x: x / 1e8 if x is not None and pd.notna(x) else None)
    out["amount_yi"] = out["amount"].apply(lambda x: x / 1e8 if x is not None and pd.notna(x) else None)
    return out


def parse_screener_query(text: str) -> tuple[str, str | None, str | None, list[tuple[str, str, float, float | None]]]:
    """解析选股语句。

    返回 (剩余条件原文, 行业名|None, 概念名|None, filters)
    filter: (col, op, value, value2)  value2 用于区间 a-b
    """
    raw = text.strip()
    industry: str | None = None
    concept: str | None = None

    m_ind = re.search(r"(?:^|\s)行业\s+(\S+)", raw)
    if m_ind:
        industry = m_ind.group(1)
        raw = raw[: m_ind.start()] + " " + raw[m_ind.end() :]
    m_con = re.search(r"(?:^|\s)概念\s+(\S+)", raw)
    if m_con:
        concept = m_con.group(1)
        raw = raw[: m_con.start()] + " " + raw[m_con.end() :]

    raw = raw.strip()
    filters: list[tuple[str, str, float, float | None]] = []

    for name, col in FIELD_MAP.items():
        for m in re.finditer(rf"{re.escape(name)}\s*(\d+(?:\.\d+)?)\s*[-~～到至]\s*(\d+(?:\.\d+)?)", raw):
            filters.append((col, "between", float(m.group(1)), float(m.group(2))))
            raw = raw.replace(m.group(0), " ", 1)

    for name, col in sorted(FIELD_MAP.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(
            rf"{re.escape(name)}\s*(>=|<=|>|<|=|＝|＞|＜)\s*(-?\d+(?:\.\d+)?)",
            raw,
        ):
            op = m.group(1)
            op = _OP_NORMALIZE[op] if op in _OP_NORMALIZE else op
            filters.append((col, op, float(m.group(2)), None))
            raw = raw.replace(m.group(0), " ", 1)

    return raw.strip(), industry, concept, filters


def apply_filters(df: pd.DataFrame, filters: list[tuple[str, str, float, float | None]]) -> pd.DataFrame:
    if df.empty or not filters:
        return df
    mask = pd.Series(True, index=df.index)
    for col, op, v, v2 in filters:
        series = df[col] if col in df.columns else pd.Series([None] * len(df), index=df.index)
        s = pd.to_numeric(series, errors="coerce")
        if col == "pe":
            s = s.where(s > 0)
        if op == "between" and v2 is not None:
            lo, hi = (v, v2) if v <= v2 else (v2, v)
            mask &= s.ge(lo) & s.le(hi)
        elif op == ">":
            mask &= s.gt(v)
        elif op == ">=":
            mask &= s.ge(v)
        elif op == "<":
            mask &= s.lt(v)
        elif op == "<=":
            mask &= s.le(v)
        elif op == "=":
            mask &= (s - v).abs() < 1e-6
    return df.loc[mask].copy()


def filters_to_desc(filters: list[tuple[str, str, float, float | None]]) -> list[str]:
    inv: dict[str, str] = {}
    for k, v in FIELD_MAP.items():
        if v not in inv:
            inv[v] = k
    out: list[str] = []
    for col, op, v, v2 in filters:
        label = inv[col] if col in inv else col
        if op == "between" and v2 is not None:
            out.append(f"{label} {v}-{v2}")
        else:
            out.append(f"{label}{op}{v}")
    return out


def _empty_error(text: str, error: str) -> ScreenerResult:
    return ScreenerResult(
        query=text,
        scope="",
        total_pool=0,
        matched=0,
        shown=0,
        df=pd.DataFrame(),
        error=error,
    )


async def run_screener(text: str, *, top_n: int = 20) -> ScreenerResult:
    leftover, industry, concept, filters = parse_screener_query(text)
    if leftover and not filters and not industry and not concept:
        industry = leftover.split()[0] if leftover else None

    if industry and concept:
        return _empty_error(
            text,
            "❌行业与概念请二选一（暂不支持同时筛选），例如：\n"
            "自动选股 行业 半导体 PE<30\n"
            "自动选股 概念 人工智能 涨跌幅>3",
        )

    if not filters and not industry and not concept:
        return _empty_error(
            text,
            "❌请给出筛选条件，例如：\n"
            "自动选股 市值50-200 PE<30 涨跌幅>2\n"
            "自动选股 行业 半导体 换手>1 量比>1.2\n"
            "自动选股 概念 人工智能 涨跌幅>3\n"
            "说明：未指定行业/概念时，股票池为沪深A按市值排序的前约2000只（非全市场）。",
        )

    scope = "沪深A"
    df = pd.DataFrame()

    if industry:
        resolved = await resolve_industry_fs(industry)
        if isinstance(resolved, str):
            return _empty_error(text, resolved)
        scope, fs = resolved
        scope = f"行业·{scope}"
        df = await fetch_board_members(fs)
    elif concept:
        resolved = await resolve_concept_fs(concept)
        if isinstance(resolved, str):
            return _empty_error(text, resolved)
        scope, fs = resolved
        scope = f"概念·{scope}"
        df = await fetch_board_members(fs)
    else:
        df = await fetch_a_share_universe(max_pages=20)
        scope = "沪深A(按市值前约2000)"

    if df.empty:
        return _empty_error(text, "❌股票池为空，请稍后重试")

    prepared = _prepare_df(df)
    total = len(prepared)
    filtered = apply_filters(prepared, filters)
    matched = len(filtered)
    if "pct" in filtered.columns and not filtered.empty:
        filtered = filtered.sort_values("pct", ascending=False, na_position="last")
    shown_df = filtered.head(top_n)

    return ScreenerResult(
        query=text,
        scope=scope,
        total_pool=total,
        matched=matched,
        shown=len(shown_df),
        df=shown_df,
        filters_desc=filters_to_desc(filters),
    )
