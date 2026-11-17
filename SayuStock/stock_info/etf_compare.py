"""以同日申赎篮子和批量行情比较场内 ETF。"""

from __future__ import annotations

import re
import asyncio
from math import isfinite
from datetime import date, time, datetime, timedelta
from itertools import combinations
from dataclasses import replace, dataclass
from collections.abc import Sequence

from ..utils.market import Quote, AssetClass, get_market
from ..utils.constant import chinese_stocks
from ..utils.warn_box import Failure
from ..utils.time_range import Market, now_bjt
from ..utils.market_holidays import is_market_holiday
from ..utils.market.adapters.etf_basket import (
    ListedFund,
    CreationBasket,
    is_etf_code,
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
    ("人工智能与算力", ("人工智能", "算力", "AIGC", "机器人")),
    ("半导体", ("半导体", "芯片")),
    ("计算机与软件", ("计算机", "软件", "信创ETF", "信创产业", "云计算", "大数据", "传媒", "游戏")),
    ("工业互联网", ("工业互联网", "工业互联")),
    ("智能制造", ("智能制造", "工业4.0", "高端制造")),
    ("汽车与智能驾驶", ("新能源车", "新能源汽车", "智能汽车", "汽车", "车联网")),
    ("新能源与电池", ("新能源", "电池", "锂电", "储能", "光伏", "风电", "碳中和")),
    ("医药与医疗", ("医药", "医疗", "创新药", "中药", "生物")),
    ("银行", ("银行",)),
    ("证券与金融", ("证券ETF", "证券公司", "证券指数", "券商", "非银", "金融", "保险")),
    ("有色与贵金属", ("有色", "黄金", "稀土", "铜", "金属")),
    ("能源与周期", ("煤炭", "石油", "石化", "化工", "钢铁", "电力", "公用事业")),
    ("军工与航天", ("军工", "国防", "航天", "航空")),
    ("红利与高股息", ("红利", "高股息", "现金流")),
    ("基建与地产", ("地产", "房地产", "基建", "建材", "建筑")),
    ("农林牧渔", ("农业", "养殖", "畜牧", "农牧")),
    ("交通运输", ("交运", "物流", "航运")),
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
            "A100",
            "A500",
            "中证100",
            "中证500",
            "中证800",
            "中证1000",
            "中证2000",
            "科创50",
            "科创板",
            "创业板",
            "双创",
            "深证100",
            "深100",
            "深证成指",
            "MSCIA50",
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
    stale_stocks: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()

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


_MANAGER_NOISE = ("工银瑞信", "创金合信", "申万菱信", "中银证券", "财通证券", "民生加银", "中邮创业")


def fund_theme(name: str) -> str:
    text = name.upper()
    for noise in _MANAGER_NOISE:
        text = text.replace(noise, "")
    return next((theme for theme, keys in _THEMES if any(key.upper() in text for key in keys)), "其他主题")


async def resolve_target(query: str) -> TargetStock | None:
    ref = await get_market().resolve(query.strip())
    if ref is None or not re.fullmatch(r"(?:00|30|60|68)[0-9]{4}", ref.code):
        return None
    if ref.asset_class is not AssetClass.EQUITY:
        return None
    return TargetStock(ref.code, ref.name)


async def resolve_etf_code(query: str) -> str | None:
    from ..utils.stock.request_utils import get_code_id

    token = query.strip()
    if not token:
        return None
    lower = token.lower()
    code = lower[-6:]
    if is_etf_code(code) and lower in (code, fund_quote_symbol(code)):
        return code
    ref = await get_market().resolve(token)
    if ref is not None and is_etf_code(ref.code):
        return ref.code
    info = await get_code_id(token, priority="etf")
    if info is not None:
        resolved = info[0].split(".")[-1]
        if is_etf_code(resolved):
            return resolved
    return None


def latest_etf_trading_day(now: datetime | None = None) -> date:
    """最近一个已有盘口与申赎清单的 A 股交易日（盘前 9:30 前及休市日回退）。"""
    current = now or now_bjt()
    day = current.date()
    if current.time() < time(9, 30):
        day -= timedelta(days=1)
    for _ in range(15):
        if day.weekday() < 5 and not is_market_holiday(Market.A_SHARE, datetime.combine(day, time(10, 0))):
            return day
        day -= timedelta(days=1)
    return current.date()


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
    stale_codes: list[str] = []
    used_providers: list[str] = []
    today_count = 0
    for item in basket.stocks:
        quote = quotes[item.symbol] if item.symbol in quotes else None
        code = item.symbol[2:]
        stamp = None if quote is None else quote.as_of
        if quote is None or stamp is None or stamp.date() > basket.day or not isfinite(quote.price) or quote.price <= 0:
            missing.append(code)
            continue
        if stamp.date() == basket.day:
            today_count += 1
            times.append(stamp)
        else:
            # 停牌股在盘中/收盘只有上一交易日时间戳：按最近价暂估并在结果中显式标出
            stale_codes.append(code)
        if quote.provider and quote.provider not in used_providers:
            used_providers.append(quote.provider)
        market_value = item.quantity * quote.price
        priced[code] = (item.name, market_value)
        industry = chinese_stocks[code]["industry_l2"] if code in chinese_stocks else ""
        if industry in ("", "未知"):
            industry = UNKNOWN_INDUSTRY
        industries.setdefault(industry, 0.0)
        industries[industry] += market_value
    if missing:
        suffix = f"等 {len(missing)} 只" if len(missing) > 3 else ""
        return f"{'、'.join(missing[:3])}{suffix}缺当日报价"
    if today_count == 0:
        suffix = f"等 {len(stale_codes)} 只" if len(stale_codes) > 3 else ""
        return f"{'、'.join(stale_codes[:3])}{suffix}缺当日报价"
    max_stale = max(5, len(basket.stocks) // 5)
    if len(stale_codes) > max_stale:
        return f"{'、'.join(stale_codes[:3])}等 {len(stale_codes)} 只非当日报价"
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
        stale_stocks=tuple(stale_codes),
        providers=tuple(used_providers),
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
    if isinstance(prices, str):
        failures.extend(Failure(pcf.code, prices) for pcf in usable)
        return BasketResult((), tuple(failures), tuple(membership))
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
    if result.page >= result.planned_pages:
        return result, ()
    page_nums = list(range(result.page + 1, result.planned_pages + 1))
    pages = await asyncio.gather(*(fetch_holder_page(result.stock.code, result.period, n) for n in page_nums))
    known = {fund.code for fund in result.candidates}
    merged = list(result.candidates)
    completed = result.page
    total_pages = result.pages
    for page_num, more in zip(page_nums, pages, strict=True):
        if isinstance(more, str):
            return replace(result, candidates=tuple(merged), page=completed, pages=total_pages), (
                Failure(f"持仓披露第 {page_num} 页", more),
            )
        for fund in more.funds:
            if fund.code not in known:
                known.add(fund.code)
                merged.append(fund)
        completed = page_num
        total_pages = more.pages
    return replace(result, candidates=tuple(merged), page=completed, pages=total_pages), ()


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
