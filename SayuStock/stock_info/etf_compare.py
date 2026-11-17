"""以同日申赎篮子和批量行情比较场内 ETF。"""

from __future__ import annotations

import re
import asyncio
from math import isfinite
from datetime import date, datetime, timedelta
from itertools import combinations
from dataclasses import replace, dataclass
from collections.abc import Sequence

from ..utils.market import Quote, AssetClass, get_market
from ..utils.constant import chinese_stocks
from ..utils.warn_box import Failure
from ..utils.market.adapters.etf_basket import (
    ListedFund,
    CreationBasket,
    fetch_holder_page,
    fund_quote_symbol,
    fetch_batch_quotes,
    fetch_tracked_index,
    fetch_creation_basket,
)

MAX_COMPARE = 4
UNKNOWN_INDUSTRY = "未匹配申万二级"
PREVIEW_STOCK_LIMIT = 300
_PREVIEW_TOTAL = 8
_SEARCH_PAGES = 4

_THEMES = (
    ("酒类", ("中证酒", "白酒", "酒ETF")),
    ("食品饮料", ("食品饮料", "细分食品", "饮食ETF")),
    ("消费电子", ("消费电子", "全指电子", "中证电子")),
    ("通信与5G", ("通信", "5G", "光通信")),
    ("人工智能与算力", ("人工智能", "算力", "AIGC")),
    ("半导体", ("半导体", "芯片")),
    ("工业互联网", ("工业互联网", "工业互联")),
    ("智能制造", ("智能制造", "工业4.0", "高端制造")),
    ("大消费", ("消费",)),
    (
        "宽基",
        (
            "上证50",
            "上证180",
            "上证综指",
            "上证综合",
            "上证中盘",
            "沪深300",
            "A50",
            "A500",
            "中证500",
            "中证1000",
            "科创50",
            "创业板50",
            "MSCI中国A股",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class TargetStock:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class FundValue:
    code: str
    name: str
    stocks: dict[str, tuple[str, float]]
    industries: dict[str, float]
    first_quote: datetime
    last_quote: datetime
    zero_stocks: tuple[str, ...]
    index_name: str = ""

    def weight(self, stock: str) -> float:
        return self.stocks[stock][1] if stock in self.stocks else 0.0

    def industry_weight(self, label: str) -> float:
        return self.industries[label] if label in self.industries else 0.0


@dataclass(frozen=True, slots=True)
class BasketMembership:
    code: str
    stock_count: int


@dataclass(frozen=True, slots=True)
class BasketResult:
    funds: tuple[FundValue, ...]
    failures: tuple[Failure, ...]
    membership_only: tuple[BasketMembership, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchResult:
    stock: TargetStock
    period: date
    candidates: tuple[ListedFund, ...]
    page: int
    pages: int
    preview: BasketResult

    @property
    def planned_pages(self) -> int:
        return min(_SEARCH_PAGES, self.pages)


def fund_theme(name: str) -> str:
    text = name.upper()
    return next((theme for theme, keys in _THEMES if any(key.upper() in text for key in keys)), "其他主题")


async def resolve_target(query: str) -> TargetStock | None:
    ref = await get_market().resolve(query.strip())
    if ref is None or not re.fullmatch(r"(?:00|30|60|68)[0-9]{4}", ref.code):
        return None
    if ref.asset_class is not AssetClass.EQUITY:
        return None
    return TargetStock(ref.code, ref.name)


def _recent_periods(today: date) -> tuple[date, date]:
    cutoff = today - timedelta(days=20)
    quarter_ends = ((3, 31), (6, 30), (9, 30), (12, 31))
    dates = [date(year, month, day) for year in (cutoff.year - 1, cutoff.year) for month, day in quarter_ends]
    usable = [day for day in dates if day <= cutoff]
    return usable[-1], usable[-2]


def value_basket(basket: CreationBasket, name: str, quotes: dict[str, Quote]) -> FundValue | str:
    priced: dict[str, tuple[str, float]] = {}
    industries: dict[str, float] = {}
    times: list[datetime] = []
    missing: list[str] = []
    for item in basket.stocks:
        quote = quotes[item.symbol] if item.symbol in quotes else None
        code = item.symbol[2:]
        stamp = None if quote is None else quote.as_of
        if (
            quote is None
            or stamp is None
            or stamp.date() != basket.day
            or not isfinite(quote.price)
            or quote.price <= 0
        ):
            missing.append(code)
            continue
        market_value = item.quantity * quote.price
        priced[code] = (item.name, market_value)
        industry = chinese_stocks[code]["industry_l2"] if code in chinese_stocks else ""
        if industry in ("", "未知"):
            industry = UNKNOWN_INDUSTRY
        industries.setdefault(industry, 0.0)
        industries[industry] += market_value
        times.append(stamp)
    if missing:
        suffix = f"等 {len(missing)} 只" if len(missing) > 3 else ""
        return f"{'、'.join(missing[:3])}{suffix}缺当日报价"
    total = sum(value for _, value in priced.values())
    if not isfinite(total) or total <= 0:
        return "篮子股票市值无有效数据"
    return FundValue(
        basket.code,
        name,
        {code: (label, value / total * 100) for code, (label, value) in priced.items()},
        {label: value / total * 100 for label, value in industries.items()},
        min(times),
        max(times),
        basket.zero_stocks,
    )


async def load_baskets(
    codes: Sequence[str], *, day: date, target: TargetStock | None = None, preview: bool = False
) -> BasketResult:
    pcfs = await asyncio.gather(*(fetch_creation_basket(code, day) for code in codes))
    failures: list[Failure] = []
    usable: list[CreationBasket] = []
    membership: list[BasketMembership] = []
    for code, pcf in zip(codes, pcfs, strict=True):
        if isinstance(pcf, str):
            failures.append(Failure(code, pcf))
            continue
        if target is not None and all(item.symbol[2:] != target.code for item in pcf.stocks):
            failures.append(Failure(code, f"当天篮子无{target.name}的计价股数"))
            continue
        if preview and len(pcf.stocks) > PREVIEW_STOCK_LIMIT:
            membership.append(BasketMembership(code, len(pcf.stocks)))
            continue
        usable.append(pcf)
    symbols = [item.symbol for pcf in usable for item in pcf.stocks]
    prices = await fetch_batch_quotes([*symbols, *(fund_quote_symbol(pcf.code) for pcf in usable)])
    values: list[FundValue] = []
    for pcf in usable:
        symbol = fund_quote_symbol(pcf.code)
        quote = prices[symbol] if symbol in prices else None
        valuation = value_basket(pcf, quote.symbol.name if quote and quote.symbol.name else pcf.code, prices)
        if isinstance(valuation, str):
            failures.append(Failure(pcf.code, valuation))
        else:
            values.append(valuation)
    indices = await asyncio.gather(*(fetch_tracked_index(fund.code) for fund in values))
    funds = tuple(replace(fund, index_name=index) for fund, index in zip(values, indices, strict=True))
    return BasketResult(funds, tuple(failures), tuple(membership))


def _preview_codes(candidates: Sequence[ListedFund]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for fund in candidates:
        groups.setdefault(fund_theme(fund.name), []).append(fund.code)
    return [group[i] for i in range(_PREVIEW_TOTAL) for group in groups.values() if i < len(group)][:_PREVIEW_TOTAL]


async def _first_page(stock: TargetStock, today: date) -> SearchResult | str:
    for period in _recent_periods(today):
        page = await fetch_holder_page(stock.code, period, 1)
        if isinstance(page, str):
            return f"持仓披露第 1 页未取得，成功查了 0 页。{page}"
        if page.funds or page.pages >= 2:
            return SearchResult(stock, period, page.funds, 1, page.pages, BasketResult((), ()))
    return "披露记录中未找到 ETF 候选"


async def _scan_pages(result: SearchResult) -> tuple[SearchResult, tuple[Failure, ...]]:
    while result.page < result.planned_pages:
        next_page = result.page + 1
        more = await fetch_holder_page(result.stock.code, result.period, next_page)
        if isinstance(more, str):
            return result, (Failure(f"持仓披露第 {next_page} 页", more),)
        known = {fund.code for fund in result.candidates}
        unique = tuple(fund for fund in more.funds if fund.code not in known)
        result = replace(result, candidates=(*result.candidates, *unique), page=next_page, pages=more.pages)
    return result, ()


async def search_listed_funds(stock: TargetStock, today: date) -> SearchResult | str:
    first = await _first_page(stock, today)
    if isinstance(first, str):
        return first
    result, warnings = await _scan_pages(first)
    current = await load_baskets(_preview_codes(result.candidates), day=today, target=result.stock, preview=True)
    return replace(result, preview=replace(current, failures=(*current.failures, *warnings)))


async def expand_fund_theme(result: SearchResult, theme: str, today: date) -> BasketResult | str:
    codes = [fund.code for fund in result.candidates if fund_theme(fund.name) == theme]
    if not codes:
        return f"本次已查的 {result.page} 页披露中暂无「{theme}」候选，请使用找股结果里的主题名。"
    current = await load_baskets(codes[:MAX_COMPARE], day=today, target=result.stock)
    remaining = codes[MAX_COMPARE:_PREVIEW_TOTAL]
    if len(current.funds) < MAX_COMPARE and remaining:
        extra = await load_baskets(remaining, day=today, target=result.stock)
        current = BasketResult((*current.funds, *extra.funds)[:MAX_COMPARE], (*current.failures, *extra.failures))
    return current


def comparison_stocks(funds: Sequence[FundValue], target: TargetStock | None) -> list[TargetStock]:
    names = {code: name for fund in funds for code, (name, _) in fund.stocks.items()}
    ranked = sorted(names, key=lambda code: (-max(fund.weight(code) for fund in funds), code))
    leaders = {max(fund.stocks, key=fund.weight) for fund in funds}
    selected = sorted(ranked, key=lambda code: code not in leaders)
    if target is not None:
        names[target.code] = target.name
        selected = [target.code, *[code for code in selected if code != target.code]]
    top = selected[:6]
    spread = sorted(
        (code for code in names if code not in top),
        key=lambda code: (min(fund.weight(code) for fund in funds) - max(fund.weight(code) for fund in funds), code),
    )[:2]
    return [TargetStock(code, names[code]) for code in (*top, *spread)]


def shares_overlap(funds: Sequence[FundValue]) -> list[tuple[str, str, float]]:
    return [
        (left.code, right.code, sum(min(weight, right.weight(code)) for code, (_, weight) in left.stocks.items()))
        for left, right in combinations(funds, 2)
    ]
