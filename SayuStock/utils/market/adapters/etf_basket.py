"""上市基金反查、沪深交易所申赎清单与有时间戳的批量行情。"""

from __future__ import annotations

import re
import asyncio
from html import unescape
from math import isfinite
from time import monotonic
from typing import TypeVar
from datetime import date
from collections import OrderedDict
from dataclasses import dataclass
from collections.abc import Callable, Sequence, Coroutine
from xml.etree.ElementTree import Element, ParseError, fromstring

from aiohttp import ClientError, ClientSession, ClientTimeout

from ..errors import is_market_error
from ..models import Quote
from ..registry import get_market

HOLDER_PAGE_SIZE = 100

_HOLDERS_URL = "https://data.eastmoney.com/dataapi/zlsj/detail"
_SSE_PCF = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do"
_SZSE_PCF = "https://reportdocs.static.szse.cn/files/text/ETFDown"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sse.com.cn/"}
_HOLDER_LIMIT = asyncio.Semaphore(4)
_PCF_LIMIT = asyncio.Semaphore(4)
_PCF_CACHE_SIZE = 256
_PCF_CACHE_TTL_SECONDS = 8 * 60 * 60
_ETF_CODE = re.compile(r"(?:15|51|52|56|58)[0-9]{4}")
_STOCK_CODE = re.compile(r"(?:00|30|60|68)[0-9]{4}")
_CASH_MARKER = "159900"
_TRACKED_FIELD = re.compile(r"<th[^>]*>\s*跟踪标的\s*</th>\s*<td[^>]*>(.*?)</td>", re.S)


@dataclass(frozen=True, slots=True)
class ListedFund:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class HolderPage:
    funds: tuple[ListedFund, ...]
    pages: int


@dataclass(frozen=True, slots=True)
class BasketStock:
    symbol: str
    name: str
    quantity: float


@dataclass(frozen=True, slots=True)
class CreationBasket:
    code: str
    day: date
    stocks: tuple[BasketStock, ...]
    zero_stocks: tuple[str, ...]


_PCF_CACHE: OrderedDict[tuple[str, date], tuple[float, CreationBasket]] = OrderedDict()
_PCF_PENDING: dict[tuple[str, date], asyncio.Task[CreationBasket | str]] = {}
_HOLDER_PENDING: dict[tuple[str, date, int], asyncio.Task[HolderPage | str]] = {}
_INDEX_PENDING: dict[str, asyncio.Task[str]] = {}
_QUOTE_PENDING: dict[tuple[str, ...], asyncio.Task[dict[str, Quote] | str]] = {}
_RequestKey = TypeVar("_RequestKey")
_RequestResult = TypeVar("_RequestResult")


async def _join_request(
    pending: dict[_RequestKey, asyncio.Task[_RequestResult]],
    key: _RequestKey,
    request: Callable[[], Coroutine[None, None, _RequestResult]],
) -> _RequestResult:
    if key not in pending or pending[key].done():
        task = asyncio.create_task(request())
        pending[key] = task

        def remove(completed: asyncio.Task[_RequestResult]) -> None:
            if key in pending and pending[key] is completed:
                del pending[key]

        task.add_done_callback(remove)
    return await asyncio.shield(pending[key])


def is_etf_code(code: str) -> bool:
    return _ETF_CODE.fullmatch(code) is not None


def parse_holder_page(payload: object, period: date) -> HolderPage | str:
    if not isinstance(payload, dict) or "success" not in payload or payload["success"] is not True:
        return "基金反查响应无有效数据"
    if "data" not in payload or "pages" not in payload:
        return "基金反查缺少数据或页数"
    rows, pages = payload["data"], payload["pages"]
    if type(pages) is not int or pages < 0 or not (isinstance(rows, list) or rows is None and pages == 0):
        return "基金反查分页格式异常"
    if not rows:
        return HolderPage((), 0) if pages == 0 else "披露页返回空记录，无法确认已完整读取"
    if pages == 0:
        return "基金反查记录与页数不一致"
    funds: dict[str, ListedFund] = {}
    for row in rows:
        if not isinstance(row, dict) or "HOLDER_CODE" not in row or "HOLDER_NAME" not in row:
            continue
        code, name = row["HOLDER_CODE"], row["HOLDER_NAME"]
        if (
            not isinstance(code, str)
            or not isinstance(name, str)
            or not is_etf_code(code)
            or "ETF" not in name.upper()
            or "联接" in name
        ):
            continue
        report = row["REPORT_DATE"] if "REPORT_DATE" in row else None
        if not isinstance(report, str) or report[:10] != period.isoformat():
            return "基金反查返回的披露日期与请求不一致"
        funds.setdefault(code, ListedFund(code, name))
    return HolderPage(tuple(funds.values()), pages)


async def fetch_holder_page(stock: str, period: date, page: int) -> HolderPage | str:
    key = (stock, period, page)
    return await _join_request(_HOLDER_PENDING, key, lambda: _download_holder_page(stock, period, page))


async def _download_holder_page(stock: str, period: date, page: int) -> HolderPage | str:
    params = {
        "SHType": "1",
        "SHCode": "",
        "SCode": stock,
        "ReportDate": period.isoformat(),
        "sortField": "NETASSET_RATIO",
        "sortDirec": "1",
        "pageNum": str(page),
        "pageSize": str(HOLDER_PAGE_SIZE),
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://data.eastmoney.com/zlsj/detail/{period.isoformat()}-1-{stock}.html",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with _HOLDER_LIMIT, ClientSession(headers=headers) as session:
        for seconds in (4, 2):
            try:
                async with session.get(_HOLDERS_URL, params=params, timeout=ClientTimeout(total=seconds)) as response:
                    if response.status != 200:
                        return f"基金反查接口 HTTP {response.status}"
                    payload: object = await response.json(content_type=None)
            except TimeoutError:
                continue
            except ClientError:
                return "基金反查接口暂时不可用"
            except ValueError:
                return "基金反查响应无法解析"
            return parse_holder_page(payload, period)
    return "请求超时，短重试一次后仍未取得数据"


def fund_quote_symbol(code: str) -> str:
    return ("sz" if code.startswith(("15", "16")) else "sh") + code


def _xml_name(el: Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _stock_from_row(
    row: dict[str, str], id_key: str, qty_key: str, name_key: str, market_key: str
) -> BasketStock | str | None:
    code = row[id_key]
    raw_qty = row[qty_key]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw_qty):
        return f"{code} 股数无效"
    quantity = float(raw_qty)
    if not isfinite(quantity):
        return f"{code} 股数无效"
    if code == _CASH_MARKER and quantity == 0:
        return None
    if not _STOCK_CODE.fullmatch(code) or market_key not in row or row[market_key] not in ("101", "102"):
        return f"含暂不支持估值的成分股 {code}（当前覆盖沪深 A 股）"
    prefix = "sh" if row[market_key] == "101" else "sz"
    if (prefix == "sh") != code.startswith("6"):
        return f"{code} 的挂牌市场与代码不一致"
    return BasketStock(prefix + code, row[name_key] if name_key in row and row[name_key] else code, quantity)


def parse_creation_basket(document: bytes, code: str, day: date) -> CreationBasket | str:
    if b"<!DOCTYPE" in document.upper():
        return "申赎清单格式无效"
    try:
        root = fromstring(document)
    except (ParseError, ValueError, LookupError):
        return "申赎清单无法解析"
    sh = fund_quote_symbol(code).startswith("sh")
    fund_key, count_key = ("FundInstrumentID", "RecordNumber") if sh else ("SecurityID", "TotalRecordNum")
    id_key, qty_key, name_key, market_key = (
        ("InstrumentID", "Quantity", "InstrumentName", "UnderlyingSecurityID")
        if sh
        else ("UnderlyingSecurityID", "ComponentShare", "UnderlyingSymbol", "UnderlyingSecurityIDSource")
    )
    meta: dict[str, str] = {}
    stocks: dict[str, BasketStock] = {}
    zero_stocks: list[str] = []
    row_count = 0
    for el in root.iter():
        name = _xml_name(el)
        if name in ("TradingDay", fund_key, count_key) and el.text:
            meta.setdefault(name, el.text.strip())
        row = {_xml_name(child): (child.text or "").strip() for child in el}
        if id_key not in row or qty_key not in row:
            continue
        row_count += 1
        item = _stock_from_row(row, id_key, qty_key, name_key, market_key)
        if isinstance(item, str):
            return item
        if item is None:
            continue
        if item.symbol in stocks or item.symbol[2:] in zero_stocks:
            return "申赎清单包含重复股票"
        if item.quantity:
            stocks[item.symbol] = item
        else:
            zero_stocks.append(item.symbol[2:])
    if fund_key not in meta or meta[fund_key] != code or "TradingDay" not in meta:
        return "未取得该基金当天的申赎清单"
    if meta["TradingDay"] not in (day.isoformat(), day.strftime("%Y%m%d")):
        return "未取得该基金当天的申赎清单"
    if count_key not in meta or meta[count_key] != str(row_count) or not stocks:
        return "申赎清单明细不完整"
    return CreationBasket(code, day, tuple(stocks.values()), tuple(zero_stocks))


async def fetch_creation_basket(code: str, day: date) -> CreationBasket | str:
    if not is_etf_code(code):
        return "请提供场内 ETF 代码"
    key = (code, day)
    if key in _PCF_CACHE:
        expires, basket = _PCF_CACHE[key]
        if monotonic() < expires:
            _PCF_CACHE.move_to_end(key)
            return basket
        del _PCF_CACHE[key]

    async def fetch_and_cache() -> CreationBasket | str:
        result = await _download_creation_basket(code, day)
        if isinstance(result, CreationBasket):
            _PCF_CACHE[key] = (monotonic() + _PCF_CACHE_TTL_SECONDS, result)
            if len(_PCF_CACHE) > _PCF_CACHE_SIZE:
                _PCF_CACHE.popitem(last=False)
        return result

    return await _join_request(_PCF_PENDING, key, fetch_and_cache)


async def _download_creation_basket(code: str, day: date) -> CreationBasket | str:
    if fund_quote_symbol(code).startswith("sh"):
        url = f"{_SSE_PCF}?fundCode={code}"
    else:
        url = f"{_SZSE_PCF}/pcf_{code}_{day:%Y%m%d}.xml"
    try:
        async with _PCF_LIMIT, ClientSession(headers=_HEADERS, timeout=ClientTimeout(total=8)) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return f"申赎清单接口 HTTP {response.status}"
                document = await response.read()
    except (ClientError, TimeoutError):
        return "申赎清单接口暂时不可用"
    return parse_creation_basket(document, code, day)


async def fetch_tracked_index(code: str) -> str:
    return await _join_request(_INDEX_PENDING, code, lambda: _download_tracked_index(code))


async def _download_tracked_index(code: str) -> str:
    url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
    try:
        async with _PCF_LIMIT, ClientSession(headers=_HEADERS, timeout=ClientTimeout(total=3)) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return ""
                html = await response.text(encoding="utf-8", errors="replace")
    except (ClientError, TimeoutError):
        return ""
    return parse_tracked_index(html)


def parse_tracked_index(html: str) -> str:
    tracked = _TRACKED_FIELD.search(html)
    if tracked is None:
        return ""
    index = unescape(re.sub(r"<[^>]*>", "", tracked[1])).strip()
    if index in ("--", "-", "无", "暂无", "该基金无跟踪标的"):
        return ""
    return index


async def fetch_batch_quotes(symbols: Sequence[str]) -> dict[str, Quote] | str:
    """同时查询同一组代码时共用取价任务，经 MarketDataPort 按源链顺延取价。"""
    if not symbols:
        return {}
    unique = tuple(sorted(set(symbols)))
    prices = await _join_request(_QUOTE_PENDING, unique, lambda: _download_batch_quotes(unique))
    return prices.copy() if isinstance(prices, dict) else prices


async def _download_batch_quotes(symbols: tuple[str, ...]) -> dict[str, Quote] | str:
    result = await get_market().batch_quotes(symbols)
    if is_market_error(result):
        return result.message
    return result
