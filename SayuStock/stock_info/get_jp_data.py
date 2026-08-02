import asyncio

import httpx
from bs4 import BeautifulSoup

from gsuid_core.logger import logger


async def get_live_pch_by_symbol(soup: object, symbol: str) -> object:
    table = soup.select_one("table.table.table-hover.sortable-theme-minimal")

    if not table:
        return None

    target_tr = table.select_one(f'tr[data-symbol="{symbol}"]')

    if not target_tr:
        return None

    pch_td = target_tr.select_one("td#pch")
    p_td = target_tr.select_one("td#p")

    if not pch_td:
        all_tds = target_tr.find_all("td")
        if len(all_tds) >= 4:
            pch_td = all_tds[3]
        else:
            return None

    if not p_td:
        all_tds = target_tr.find_all("td")
        if len(all_tds) >= 3:
            p_td = all_tds[2]
        else:
            return None

    return float(pch_td.get_text(strip=True)[:-1]), float(p_td.get_text(strip=True))


def calculate_change_rate(a: float, b: float) -> object:
    previous_value = b - a
    if previous_value == 0:
        return 0

    diff = a / previous_value
    return diff * 100


async def get_jpy() -> object:
    url = "https://zh.tradingeconomics.com/japan/government-bond-yield"
    symbol_to_find1 = "GJGB30Y:IND"
    symbol_to_find2 = "GJGB10:IND"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit"
        "/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, follow_redirects=True, timeout=15.0)
        response.raise_for_status()
        html_content = response.text

    soup = BeautifulSoup(html_content, "html.parser")

    y30 = await get_live_pch_by_symbol(soup, symbol_to_find1)
    y10 = await get_live_pch_by_symbol(soup, symbol_to_find2)

    if y30 is None or y10 is None:
        return None

    diff30 = calculate_change_rate(y30[0], y30[1])
    diff10 = calculate_change_rate(y10[0], y10[1])

    logger.debug(f"y30: {y30[0]} ({diff30:.2%})")
    logger.debug(f"y10: {y10[0]} ({diff10:.2%})")

    # 语义字段：name / price / change_pct（供 DisplayItem 使用）
    return {
        "JP 30Y": {
            "name": "JP 30Y",
            "price": y30[1],
            "change_pct": diff30,
        },
        "JP 10Y": {
            "name": "JP 10Y",
            "price": y10[1],
            "change_pct": diff10,
        },
    }


if __name__ == "__main__":
    print(asyncio.run(get_jpy()))
