"""新浪财经行情 HTTP：实时盘口 / K线 / 分时 / 行情中心列表。"""

from __future__ import annotations

import re
import asyncio
from typing import Mapping

from aiohttp import ClientError, ClientSession, ClientTimeout

from gsuid_core.logger import logger

from ...errors import MarketError, parse_error, network_error
from ....stock.utils import async_file_cache

PROVIDER = "sina"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Referer": "https://finance.sina.com.cn/",
}
HQ_URL = "https://hq.sinajs.cn/list="
KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
MINLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20t=/CN_MinlineService.getMinlineData"
US_DAILY_URL = "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK"
US_MINK_URL = "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getMinK"
NODE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
NODE_COUNT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
)
INDUSTRY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
# 板块分类汇总：class=概念 / industry=申万行业 / area=地域
FLJK_URL = "http://money.finance.sina.com.cn/q/view/newFLJK.php"
# 资金流排行：个股级。fenlei 0=申万 1=概念 2=证监会行业
MONEY_FLOW_RANK_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"
# 两市成交额取指数行情即可（上证+深证），省掉全市场扫描
TURNOVER_INDEXES = ("sh000001", "sz399001")
# 行情中心单页上限；新浪对大 num 不稳定，超出走翻页
_NODE_PAGE_SIZE = 80
# 翻页保护上限（沪深A 全量约 5500+）
_NODE_MAX_PAGES = 80
# 美股符号带下划线（gb_qqq），A 股符号（sh600519）亦匹配
_HQ_RE = re.compile(r'hq_str_(?P<sym>[A-Za-z0-9_]+)="(?P<line>[^"]*)"')


def _exc_message(exc: BaseException) -> str:
    """TimeoutError 的 str 是空的，空消息会让涨跌分布整页变成 0。"""
    text = str(exc).strip()
    return text or type(exc).__name__


async def _get_text(
    url: str,
    params: Mapping[str, str] | None = None,
    *,
    timeout_s: float = 20,
) -> str | MarketError:
    timeout = ClientTimeout(total=timeout_s)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(url, params=dict(params) if params else None) as res:
                if res.status != 200:
                    return network_error(f"新浪 HTTP {res.status}", provider=PROVIDER)
                return await res.text(encoding="gb18030", errors="replace")
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][新浪] 请求失败: {e}")
        return network_error(_exc_message(e), provider=PROVIDER)


async def _get_json(url: str, params: Mapping[str, str], *, timeout_s: float = 20) -> object | MarketError:
    timeout = ClientTimeout(total=timeout_s)
    try:
        async with ClientSession(headers=_HEADERS, timeout=timeout) as sess:
            async with sess.get(url, params=dict(params)) as res:
                if res.status != 200:
                    return network_error(f"新浪 HTTP {res.status}", provider=PROVIDER)
                try:
                    payload: object = await res.json(content_type=None)
                except (ValueError, TypeError) as e:
                    return parse_error(f"新浪 JSON 无效: {e}", provider=PROVIDER)
                return payload
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][新浪] 请求失败: {e}")
        return network_error(_exc_message(e), provider=PROVIDER)


async def fetch_hq_lines(symbols: list[str]) -> dict[str, str] | MarketError:
    """实时盘口原始 CSV（GBK 解码后）；返回 {sina_symbol: csv行}。"""
    if not symbols:
        return {}
    text = await _get_text(HQ_URL + ",".join(symbols))
    if isinstance(text, MarketError):
        return text
    out: dict[str, str] = {}
    for m in _HQ_RE.finditer(text):
        out[m.group("sym")] = m.group("line")
    if not out:
        return parse_error("新浪盘口响应无数据", provider=PROVIDER)
    return out


@async_file_cache(market="{symbol}", sector="sina-kline-{scale}", suffix="json", minutes=2)
async def fetch_kline(symbol: str, scale: int, datalen: int) -> list[object] | str:
    params = {"symbol": symbol, "scale": str(scale), "ma": "no", "datalen": str(datalen)}
    payload = await _get_json(KLINE_URL, params)
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, list):
        return "新浪K线响应非列表"
    return payload


@async_file_cache(market="{symbol}", sector="sina-usdaily", suffix="json", minutes=2)
async def fetch_us_daily(symbol: str) -> list[object] | str:
    """美股日K（全量历史，字段 d/o/h/l/c/v/a）；symbol 为裸代码，如 QQQ。"""
    payload = await _get_json(US_DAILY_URL, {"symbol": symbol})
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, list):
        return "新浪美股日K响应非列表"
    return payload


@async_file_cache(market="{symbol}", sector="sina-usmink-{type_}", suffix="json", minutes=2)
async def fetch_us_mink(symbol: str, type_: int) -> list[object] | str:
    """美股分钟K：type_=1 分时级 1 分钟，5/15/30/60 分钟K；最多 1023 根。

    symbol 股票为裸代码（QQQ），指数带前导点（.inx）；时间戳为美东时间。
    """
    payload = await _get_json(US_MINK_URL, {"symbol": symbol, "type": str(type_), "qn": "3"})
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, list):
        return "新浪美股分钟K响应非列表"
    return payload


@async_file_cache(market="{symbol}", sector="sina-minline", suffix="json", minutes=1)
async def fetch_minline(symbol: str) -> object | str:
    text = await _get_text(MINLINE_URL, {"symbol": symbol})
    if isinstance(text, MarketError):
        return text.message
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return "新浪分时响应无数据"
    import json

    try:
        return json.loads(text[start + 1 : end])
    except ValueError:
        return "新浪分时 JSON 无效"


@async_file_cache(
    market="{node}",
    sector="sina-node-{page}",
    suffix="json",
    sp="{sort}-{asc}",
    minutes=1,
)
async def fetch_node_page(
    node: str,
    page: int,
    sort: str,
    asc: int,
    timeout_s: float = 20,
) -> list[object] | str:
    params = {
        "page": str(page),
        "num": str(_NODE_PAGE_SIZE),
        "sort": sort,
        "asc": str(asc),
        "node": node,
    }
    payload = await _get_json(NODE_URL, params, timeout_s=timeout_s)
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, list):
        return "新浪行情中心响应非列表"
    return payload


@async_file_cache(market="{node}", sector="sina-node-count", suffix="json", minutes=5)
async def fetch_node_count(node: str) -> int | str:
    payload = await _get_json(NODE_COUNT_URL, {"node": node})
    if isinstance(payload, MarketError):
        return payload.message
    try:
        return int(str(payload).strip().strip('"'))
    except (TypeError, ValueError):
        return "新浪成分数量响应无效"


@async_file_cache(market="sina-industry", sector="summary", suffix="json", minutes=3)
async def fetch_industry_summary() -> dict[str, str] | str:
    """行业板块汇总（GBK 变量赋值文本）；返回 {行业名: csv行}。"""
    text = await _get_text(INDUSTRY_URL)
    if isinstance(text, MarketError):
        return text.message
    out: dict[str, str] = {}
    for m in re.finditer(r'"(?P<node>[^"]+)":"(?P<row>[^"]*)"', text):
        out[m.group("node")] = m.group("row")
    if not out:
        return "新浪行业板块响应无数据"
    return out


@async_file_cache(market="sina-fljk", sector="{kind}", suffix="json", minutes=3)
async def fetch_fljk_summary(kind: str) -> dict[str, str] | str:
    """板块分类汇总（GBK 变量赋值文本）；kind=class 概念 / industry 申万行业 / area 地域。

    与 `fetch_industry_summary`（newSinaHy）行列序完全一致，都是 13 段 CSV：
    节点,名称,家数,均价,涨跌额,涨跌幅,成交量(手),成交额(元),
    领涨代码,领涨涨跌幅,领涨价,领涨涨跌额,领涨名。
    """
    text = await _get_text(FLJK_URL, {"param": kind})
    if isinstance(text, MarketError):
        return text.message
    out: dict[str, str] = {}
    for m in re.finditer(r'"(?P<node>[^"]+)":"(?P<row>[^"]*)"', text):
        out[m.group("node")] = m.group("row")
    if not out:
        return f"新浪板块分类 {kind} 响应无数据"
    return out


@async_file_cache(market="sina-mfrank", sector="stock", suffix="json", sp="{asc}", minutes=3)
async def fetch_money_flow_rank(asc: int, num: int) -> list[object] | str:
    """个股资金流排行；asc=1 净流出在前，0 净流入在前。fenlei=0 申万全市场。"""
    payload = await _get_json(
        MONEY_FLOW_RANK_URL,
        {"page": "1", "num": str(num), "sort": "netamount", "asc": str(asc), "fenlei": "0"},
    )
    if isinstance(payload, MarketError):
        return payload.message
    if not isinstance(payload, list):
        return "新浪资金流排行响应非列表"
    return payload


async def fetch_turnover_lines() -> dict[str, str] | MarketError:
    """两市成交额：上证 + 深证指数盘口原始行（一次请求拿两个）。"""
    return await fetch_hq_lines(list(TURNOVER_INDEXES))


# 全 A 约 70 页。16 路、单页 8 秒；默认 20 秒会吃掉 25 秒预算，一页超时分布就变 0。
_BREADTH_CONCURRENCY = 16
_BREADTH_PAGE_TIMEOUT_S = 8.0


@async_file_cache(market="sina-breadth", sector="hs_a", suffix="json", minutes=3)
async def fetch_breadth_rows() -> list[object] | MarketError:
    """沪深 A 全量行情（并发翻页）；breadth 统计用，失败即整条链顺延。"""
    count = await fetch_node_count("hs_a")
    if isinstance(count, str):
        return network_error(count or "新浪成分数量失败", provider=PROVIDER)
    pages = max(1, -(-count // _NODE_PAGE_SIZE))
    pages = min(pages, _NODE_MAX_PAGES)
    gate = asyncio.Semaphore(_BREADTH_CONCURRENCY)

    async def one(page: int) -> list[object] | str:
        async with gate:
            chunk = await fetch_node_page("hs_a", page, "changepercent", 0, _BREADTH_PAGE_TIMEOUT_S)
            if isinstance(chunk, str):
                chunk = await fetch_node_page("hs_a", page, "changepercent", 0, _BREADTH_PAGE_TIMEOUT_S)
            return chunk

    chunks = await asyncio.gather(*(one(p) for p in range(1, pages + 1)))
    failed = [chunk for chunk in chunks if isinstance(chunk, str)]
    rows: list[object] = []
    for chunk in chunks:
        if isinstance(chunk, list):
            rows.extend(chunk)
    # 缺一两页仍然能画分布；缺太多就交给下一条源。
    if failed and len(failed) > max(1, len(chunks) // 10):
        message = next((item.strip() for item in failed if item.strip()), "新浪全A翻页失败")
        return network_error(message, provider=PROVIDER)
    if failed:
        logger.warning(f"[SayuStock][新浪] 涨跌分布缺 {len(failed)}/{len(chunks)} 页，用已拿到的页")
    if not rows:
        return parse_error("新浪全A列表为空", provider=PROVIDER)
    return rows


async def fetch_node_rows(
    node: str,
    *,
    sort: str,
    asc: bool,
    limit: int | None = None,
) -> list[object] | MarketError:
    """按排序翻页拉取行情中心列表；limit=None 时拉全量。"""
    rows: list[object] = []
    pages = _NODE_MAX_PAGES if limit is None else max(1, (limit + _NODE_PAGE_SIZE - 1) // _NODE_PAGE_SIZE)
    for page in range(1, pages + 1):
        chunk = await fetch_node_page(node, page, sort, 1 if asc else 0)
        if isinstance(chunk, str):
            if rows:
                return rows
            return network_error(chunk, provider=PROVIDER)
        if not chunk:
            break
        rows.extend(chunk)
        if limit is not None and len(rows) >= limit:
            return rows[:limit]
        if len(chunk) < _NODE_PAGE_SIZE:
            break
    if not rows:
        return parse_error("新浪行情中心列表为空", provider=PROVIDER)
    return rows
