import random
import asyncio
from typing import Callable, Optional
from dataclasses import replace

from gsuid_core.logger import logger
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.ai_core.trigger_bridge import ai_return

from .get_jp_data import get_jpy
from ..utils.market import DisplayItem, from_quote, get_market, is_market_error, pick_display_items
from ..utils.constant import bond, whsc, crypto, i_code, commodity
from ..utils.all_weather_html import CSS_WIDTH, CSS_HEIGHT, build_all_weather_html

ItemMap = dict[str, DisplayItem]


async def __get_item(result: ItemMap, stock: str, display_name: str) -> None:
    await asyncio.sleep(random.uniform(0.2, 1))
    q = await get_market().quote(stock)
    if is_market_error(q):
        return
    item = from_quote(q)
    # 全天候格子按配置表的键展示/对齐；API 名可能是「黄金/美元」对不上 XAU
    if display_name and display_name != item.name:
        item = replace(item, name=display_name)
    result[item.name] = item


async def _get_items(_d: dict[str, str], other_call: Optional[Callable] = None) -> ItemMap:
    result: ItemMap = {}
    tasks = []
    if other_call:
        tasks.append(other_call(result))
    for name, code in _d.items():
        if code:
            tasks.append(__get_item(result, code, name))
    await asyncio.gather(*tasks)
    return result


async def append_jpy(result: ItemMap) -> None:
    data = await get_jpy()
    if data is None:
        return
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        price = v["price"] if "price" in v else 0.0
        chg = v["change_pct"] if "change_pct" in v else 0.0
        result[k] = DisplayItem(
            name=str(v["name"]) if "name" in v else k,
            price=float(price) if not isinstance(price, str) else 0.0,
            change_pct=float(chg) if not isinstance(chg, str) else 0.0,
        )


async def draw_future_img() -> str | bytes:
    market = get_market()
    intl = await market.board("国际市场", limit=100, sort_asc=False)
    if is_market_error(intl):
        return intl.message
    from ..utils.market import board_rows_to_items

    data_gz = board_rows_to_items(intl.rows)

    results = await asyncio.gather(
        _get_items(commodity),
        _get_items(bond, append_jpy),
        _get_items(whsc),
        _get_items(crypto),
        return_exceptions=True,
    )

    def safe_map(result: object) -> ItemMap:
        if isinstance(result, Exception) or not isinstance(result, dict):
            return {}
        return result

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
    _ai_return_all_weather(data_gz, data2, data3, data4, data5)
    html = build_all_weather_html(sections)
    try:
        return await render_html_to_bytes(
            html,
            max_width=float(CSS_WIDTH * 2),
            dpi=192.0,
            device_height=float(CSS_HEIGHT * 2),
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
