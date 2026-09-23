"""AAStocks 港股 IPO 表格解析 + 合并增强。

页面为服务端渲染的 HTML 表格（UTF-8）。按**表头签名**定位目标表、
按**表头列名**取列，不依赖表格顺序；代码统一取 ``03228.HK`` 中的 5 位数字。
"""

from __future__ import annotations

import re
from datetime import date
from dataclasses import replace, dataclass

from ...enums import IpoMarket
from ...models import IpoEvent

_CODE_RE = re.compile(r"(\d{4,5})\.HK")
_TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


@dataclass(slots=True)
class HkIpoExtra:
    """AAStocks 侧的港股 IPO 增强字段。"""

    code: str
    name: str | None = None  # 繁体公司名（仅降级数据用作展示）
    apply_end_date: date | None = None  # 招股截止日
    grey_market_date: date | None = None  # 暗盘日
    listing_date: date | None = None
    issue_price: float | None = None  # 上市价 / 单值招股价
    issue_price_text: str | None = None  # 招股价区间文本，如 "39-44" / "最高 69.88"
    oversubscription: float | None = None  # 公开发售超购倍数
    first_day_change: float | None = None  # 首日表现 %


def _cell_text(cell: str) -> str:
    text = re.sub(r"<[^>]+>", " ", cell)
    return re.sub(r"\s+", " ", text).strip()


def _rows_of(html: str) -> list[list[str]]:
    """整页 → 所有表格的所有行；保留空单元格以维持列对齐。"""
    rows: list[list[str]] = []
    for table in _TABLE_RE.findall(html):
        for tr in _TR_RE.findall(table):
            cells = [_cell_text(c) for c in _TD_RE.findall(tr)]
            cells = ["" if c == "&nbsp;" else c for c in cells]
            if any(cells):
                rows.append(cells)
    return rows


def _parse_date(text: str) -> date | None:
    """AAStocks 日期为 ``YYYY/MM/DD``。"""
    try:
        year, month, day = text.strip().split("/")
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _parse_float(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


def _parse_pct(text: str) -> float | None:
    if "N/A" in text or "%" not in text:
        return None
    return _parse_float(text.replace("%", "").replace("+", ""))


def _parse_price(text: str) -> tuple[float | None, str | None]:
    """招股价单元格：单值 → float；区间/「最高」→ 原文本。"""
    cleaned = text.strip()
    single = _parse_float(cleaned)
    if single is not None:
        return single, None
    return None, cleaned or None


def _row_code(cells: list[str]) -> str | None:
    for cell in cells:
        m = _CODE_RE.search(cell)
        if m is not None:
            return m.group(1)
    return None


# 招股状态按剩余天数换措辞：N日後/明天/今日/後天/即日 截止招股
_NAME_NOISE_RE = re.compile(r"(?:\d+日後|明天|今日|後天|即日)?截止招股|跌穿上市價")


def _row_name(cells: list[str]) -> str | None:
    """「深圳市景旺電子03228.HK2日後截止招股」→「深圳市景旺電子」。"""
    for cell in cells:
        if _CODE_RE.search(cell) is None:
            continue
        name = _CODE_RE.sub("", cell, count=1)
        name = _NAME_NOISE_RE.sub("", name)
        name = name.strip(" .·、,")
        if name:
            return name
    return None


def _idx(header: list[str], keyword: str) -> int | None:
    for i, cell in enumerate(header):
        if keyword in cell:
            return i
    return None


def _find_table(
    rows: list[list[str]], need: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> tuple[list[str], list[list[str]]] | None:
    """按表头签名取表格 → (表头行, 数据行列表)。"""
    for i, header in enumerate(rows):
        if not all(any(kw in cell for cell in header) for kw in need):
            continue
        if any(any(kw in cell for cell in header) for kw in exclude):
            continue
        return header, rows[i + 1 :]
    return None


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _fill(extra: HkIpoExtra, **fields: object) -> HkIpoExtra:
    for key, value in fields.items():
        if value is not None:
            setattr(extra, key, value)
    return extra


def parse_mainpage(html: str) -> dict[str, HkIpoExtra]:
    """主页面：招股中表（招股价/截止日/上市日）+ 已上市表（上市价/超购/首日表现）。"""
    rows = _rows_of(html)
    out: dict[str, HkIpoExtra] = {}

    subscribing = _find_table(rows, need=("招股截止日", "上市日期"), exclude=("暗盤日期", "超額倍數"))
    if subscribing is not None:
        header, data_rows = subscribing
        i_end, i_list, i_price = _idx(header, "招股截止日"), _idx(header, "上市日期"), _idx(header, "招股價")
        for r in data_rows:
            code = _row_code(r)
            if code is None:
                continue
            apply_end = _parse_date(_cell(r, i_end))
            listing = _parse_date(_cell(r, i_list))
            # _find_table 的数据行会延伸到后续表格；日期解析不出即非本表行
            if apply_end is None and listing is None:
                continue
            price_pair = _parse_price(_cell(r, i_price))
            out[code] = _fill(
                HkIpoExtra(code=code, name=_row_name(r)),
                apply_end_date=apply_end,
                listing_date=listing,
                issue_price=price_pair[0],
                issue_price_text=price_pair[1],
            )

    listed = _find_table(rows, need=("上市價", "超額倍數", "首日表現"))
    if listed is not None:
        header, data_rows = listed
        i_price, i_over = _idx(header, "上市價"), _idx(header, "超額倍數")
        i_first, i_list = _idx(header, "首日表現"), _idx(header, "上市日期")
        i_name = _idx(header, "公司名稱")
        for r in data_rows:
            code = _row_code(r)
            if code is None:
                continue
            listing = _parse_date(_cell(r, i_list))
            if listing is None:
                continue
            extra = out.get(code)
            if extra is None:
                # 已上市表的代號是独立列，名称取「公司名稱」列并剔除状态后缀
                name_text = _NAME_NOISE_RE.sub("", _cell(r, i_name)).strip(" .·、,")
                extra = HkIpoExtra(code=code, name=name_text or None)
            _fill(
                extra,
                listing_date=listing,
                issue_price=_parse_float(_cell(r, i_price)),
                oversubscription=_parse_float(_cell(r, i_over)),
                first_day_change=_parse_pct(_cell(r, i_first)),
            )
            out[code] = extra
    return out


def parse_upcoming(html: str) -> dict[str, HkIpoExtra]:
    """即将上市页：招股截止日 + 暗盘日期 + 上市日期。"""
    rows = _rows_of(html)
    found = _find_table(rows, need=("招股截止日", "暗盤日期"))
    out: dict[str, HkIpoExtra] = {}
    if found is None:
        return out
    header, data_rows = found
    i_end, i_grey, i_list = _idx(header, "招股截止日"), _idx(header, "暗盤日期"), _idx(header, "上市日期")
    for r in data_rows:
        code = _row_code(r)
        if code is None:
            continue
        apply_end = _parse_date(_cell(r, i_end))
        grey = _parse_date(_cell(r, i_grey))
        listing = _parse_date(_cell(r, i_list))
        if apply_end is None and grey is None and listing is None:
            continue
        extra = out.get(code) or HkIpoExtra(code=code, name=_row_name(r))
        _fill(
            extra,
            apply_end_date=apply_end,
            grey_market_date=grey,
            listing_date=listing,
        )
        out[code] = extra
    return out


def merge_hk_ipo_extra(events: list[IpoEvent], extras: dict[str, HkIpoExtra]) -> list[IpoEvent]:
    """把 AAStocks 增强字段合并进东财港股事件（只填空缺字段，名称保留东财简体）。

    可链式调用多次合并多个来源；字段判空用 ``is None``（首日 0.0 是有效值）。
    """
    if not extras:
        return events

    def _pick(mine: object, theirs: object) -> object:
        return mine if mine is not None else theirs

    merged: list[IpoEvent] = []
    for ev in events:
        extra = extras.get(ev.code)
        if extra is None:
            merged.append(ev)
            continue
        merged.append(
            replace(
                ev,
                apply_end_date=_pick(ev.apply_end_date, extra.apply_end_date),
                grey_market_date=_pick(ev.grey_market_date, extra.grey_market_date),
                listing_date=_pick(ev.listing_date, extra.listing_date),
                issue_price=_pick(ev.issue_price, extra.issue_price),
                issue_price_text=_pick(ev.issue_price_text, extra.issue_price_text),
                oversubscription=_pick(ev.oversubscription, extra.oversubscription),
                first_day_change=_pick(ev.first_day_change, extra.first_day_change),
            )
        )
    return merged


def hk_ipo_events_from_extras(extras: dict[str, HkIpoExtra]) -> list[IpoEvent]:
    """AAStocks 增强表 → 港股 IpoEvent 降级数据（push2 不可达时兜底）。

    覆盖局限：仅招股中 + AAStocks 收录的已上市（不含 GEM/介绍上市），
    名称是繁体。上市日为空的行（纯已申报）丢弃。
    """
    events: list[IpoEvent] = []
    for x in extras.values():
        if x.listing_date is None:
            continue
        events.append(
            IpoEvent(
                market=IpoMarket.HK,
                code=x.code,
                name=x.name or x.code,
                listing_date=x.listing_date,
                apply_end_date=x.apply_end_date,
                grey_market_date=x.grey_market_date,
                issue_price=x.issue_price,
                issue_price_text=x.issue_price_text,
                oversubscription=x.oversubscription,
                first_day_change=x.first_day_change,
                currency="HKD",
                board="港交所主板",
            )
        )
    return events
