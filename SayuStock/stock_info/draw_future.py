import random
import asyncio
from typing import Callable, Optional
from pathlib import Path

from PIL import Image

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from .draw_info import draw_block
from .get_jp_data import get_jpy
from ..utils.image import get_footer
from ..utils.market import DisplayItem, from_quote, get_market, is_market_error
from ..utils.get_OKX import CRYPTO_MAP
from ..utils.constant import bond, whsc, i_code, commodity

TEXT_PATH = Path(__file__).parent / "texture2d"
ItemMap = dict[str, DisplayItem]


async def __get_item(result: ItemMap, stock: str) -> None:
    await asyncio.sleep(random.uniform(0.2, 1))
    q = await get_market().quote(stock)
    if is_market_error(q):
        return
    item = from_quote(q)
    result[item.name] = item


async def _get_items(_d: dict[str, str], other_call: Optional[Callable] = None) -> ItemMap:
    result: ItemMap = {}
    tasks = []
    if other_call:
        tasks.append(other_call(result))
    for name, code in _d.items():
        if code:
            tasks.append(__get_item(result, code))
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
        _get_items({k: k for k in ("BTC", "ETH", "SOL", "DOGE", "BNB") if k in CRYPTO_MAP or True}),
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

    img = Image.open(TEXT_PATH / "bg1.jpg").convert("RGBA")
    ox = 223
    oy = 140

    async def paste_blocks(items: list[DisplayItem] | ItemMap, keys: dict[str, str] | list[str], y_base: int) -> None:
        index = 0
        key_list = list(keys.keys()) if isinstance(keys, dict) else list(keys)
        pool: list[DisplayItem] = list(items.values()) if isinstance(items, dict) else list(items)
        for d in key_list:
            for item in pool:
                if item.name != d and d not in item.name and item.name not in d:
                    continue
                block = await draw_block(item)
                img.paste(block, (62 + ox * (index % 4), y_base + oy * (index // 4)), block)
                index += 1
                break

    await paste_blocks(data_gz, i_code, 487)
    await paste_blocks(data2, commodity, 1007)
    await paste_blocks(data3, bond, 1395)
    await paste_blocks(data4, whsc, 1773)
    await paste_blocks(data5, list(CRYPTO_MAP.keys())[:8], 1988)

    footer = get_footer()
    img.paste(footer, (75, 2135), footer)
    res = await convert_img(img)
    _ai_return_all_weather(data_gz, data2, data3, data4, data5)
    return res


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
        for title, pool in (
            ("大宗商品", data_commodity),
            ("债券", data_bond),
            ("外汇", data_whsc),
            ("加密货币", data_crypto),
        ):
            result += f"\n【{title}】\n"
            for item in pool.values():
                result += f"  {item.name}: {item.price} ({item.change_pct}%)\n"
        ai_return(result)
    except (TypeError, ValueError, KeyError) as e:
        logger.warning(f"[SayuStock] ai_return 全天候失败: {e}")
