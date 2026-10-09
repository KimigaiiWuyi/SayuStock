import random
import asyncio
from dataclasses import replace

from gsuid_core.logger import logger
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.market import (
    DisplayItem,
    from_quote,
    get_market,
    is_market_error,
    pick_display_items,
    board_rows_to_items,
)
from ..utils.constant import bond, whsc, crypto, i_code, commodity
from ..utils.sparkline import sparkline_from_series
from ..utils.all_weather_html import (
    SPARK_H,
    SPARK_W,
    CSS_WIDTH,
    em_secid,
    build_all_weather_html,
    all_weather_canvas_size,
)

ItemMap = dict[str, DisplayItem]
SparkMap = dict[str, str]


async def _quote_item(stock: str, display_name: str) -> DisplayItem | None:
    q = await get_market().quote(stock)
    if is_market_error(q):
        return None
    item = from_quote(q)
    if display_name and display_name != item.name:
        item = replace(item, name=display_name)
    return item


async def __get_item(
    result: ItemMap,
    stock: str,
    display_name: str,
    sparks: SparkMap | None,
) -> None:
    await asyncio.sleep(random.uniform(0.2, 1))
    if sparks is None:
        item = await _quote_item(stock, display_name)
        if item is not None:
            result[item.name] = item
        return
    series = await get_market().intraday(stock)
    if is_market_error(series) or series.quote is None:
        # 外盘多数源只有盘口。分时失败仍留报价格，只是没有折线。
        item = await _quote_item(stock, display_name)
        if item is not None:
            result[item.name] = item
        return
    q = series.quote
    item = from_quote(q)
    # 链上盖的是序列的 sourceBy，嵌套 quote 可能还是空的。
    if item.provider is None and series.provider:
        item = replace(item, provider=series.provider)
    # 全天候格子按配置表的键展示/对齐；API 名可能是「黄金/美元」对不上 XAU
    if display_name and display_name != item.name:
        item = replace(item, name=display_name)
    result[item.name] = item
    svg = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
    if svg:
        sparks[item.name] = svg
        sparks[stock] = svg


async def _get_items(_d: dict[str, str], sparks: SparkMap | None = None) -> ItemMap:
    result: ItemMap = {}
    tasks = [__get_item(result, code, name, sparks) for name, code in _d.items() if code]
    await asyncio.gather(*tasks)
    return result


async def _fetch_sparks(
    table: dict[str, str],
    sparks: SparkMap,
    sources: list[str | None],
) -> None:
    """只补 trends2 折线，不改格子报价（国际市场报价仍走 clist）。"""

    async def one(name: str, code: str) -> None:
        await asyncio.sleep(random.uniform(0.2, 1))
        secid = em_secid(code)
        if not secid:
            return
        series = await get_market().intraday(secid)
        if is_market_error(series):
            return
        sources.append(series.provider)
        svg = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
        if not svg:
            return
        sparks[name] = svg
        sparks[code] = svg
        sparks[secid] = svg
        q = series.quote
        if q is not None:
            sparks[q.symbol.code] = svg
            sparks[q.symbol.provider_symbol] = svg

    await asyncio.gather(
        *[one(name, code) for name, code in table.items() if code],
        return_exceptions=True,
    )


def _index_queries() -> dict[str, str]:
    """国际市场板块失败时，按单只 secid 报价。clist 键要去掉 ``i:``。"""
    out: dict[str, str] = {}
    for name, code in i_code.items():
        secid = em_secid(code)
        if secid:
            out[name] = secid
    return out


async def draw_future_img() -> str | bytes:
    market = get_market()
    intl = await market.board("国际市场", limit=100, sort_asc=False)
    sparks: SparkMap = {}
    # 板块快照与折线可能不是同一个源，图角两边都要收。
    extra_sources: list[str | None] = []
    board_message = intl.message if is_market_error(intl) else ""

    async def _tail() -> ItemMap | None:
        # 国际市场列表只有东财。列表失败改走单只报价，其余分区照常画。
        if is_market_error(intl):
            return await _get_items(_index_queries(), sparks)
        extra_sources.append(intl.provider)
        await _fetch_sparks(i_code, sparks, extra_sources)
        return None

    results = await asyncio.gather(
        _get_items(commodity, sparks),
        _get_items(bond, sparks),
        _get_items(whsc, sparks),
        _get_items(crypto, sparks),
        _tail(),
        return_exceptions=True,
    )

    def safe_map(result: object) -> ItemMap:
        if isinstance(result, BaseException) or not isinstance(result, dict):
            return {}
        out: ItemMap = {}
        for key, value in result.items():
            if isinstance(key, str) and isinstance(value, DisplayItem):
                out[key] = value
        return out

    if is_market_error(intl):
        data_gz = list(safe_map(results[4]).values())
    else:
        data_gz = board_rows_to_items(intl.rows)
    data2 = safe_map(results[0])
    data3 = safe_map(results[1])
    data4 = safe_map(results[2])
    data5 = safe_map(results[3])

    sections: list[tuple[str, list[DisplayItem]]] = [
        ("国际市场", pick_display_items(data_gz, i_code)),
        ("大宗商品", pick_display_items(data2, commodity)),
        ("债券市场", pick_display_items(data3, bond)),
        ("外汇市场", pick_display_items(data4, whsc)),
        ("加密货币", pick_display_items(data5, crypto)),
    ]
    visible = [(title, items) for title, items in sections if items]
    if not visible:
        return board_message or "全天候没有可展示的行情"
    _ai_return_all_weather(data_gz, data2, data3, data4, data5)
    html = build_all_weather_html(visible, sparklines=sparks, sources=tuple(extra_sources))
    _, height = all_weather_canvas_size(sections, sparks)
    try:
        return await render_html_to_bytes(
            html,
            max_width=float(CSS_WIDTH * 2),
            dpi=192.0,
            device_height=float(height * 2),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(CSS_WIDTH),
        )
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception(f"[SayuStock] 全天候 HTML 出图失败: {e}")
        return "全天候出图失败"


def _ai_return_all_weather(
    data_gz: list[DisplayItem],
    data_commodity: ItemMap,
    data_bond: ItemMap,
    data_whsc: ItemMap,
    data_crypto: ItemMap,
) -> None:
    """全天候语义数据 → ai_return。"""
    try:
        result = "【全天候板块】\n\n【全球股市】\n"
        for name in i_code:
            for item in data_gz:
                if name in item.name or item.name in name:
                    result += f"  {item.name}: {item.price} ({item.change_pct}%)\n"
                    break
        for title, pool, keys in (
            ("大宗商品", data_commodity, commodity),
            ("债券", data_bond, bond),
            ("外汇", data_whsc, whsc),
            ("加密货币", data_crypto, crypto),
        ):
            result += f"\n【{title}】\n"
            for item in pick_display_items(pool, keys):
                result += f"  {item.name}: {item.price} ({item.change_pct}%)\n"
        ai_return(result)
    except (TypeError, ValueError, KeyError) as e:
        logger.warning(f"[SayuStock] ai_return 全天候失败: {e}")
