import asyncio
from typing import Optional

import httpx

from gsuid_core.logger import logger

# 币种名称到 OKX API instId 的映射
CRYPTO_MAP = {
    "BTC USD": "BTC-USD",
    "ETH USD": "ETH-USD",
    # "USDT": "USDT-USD",
    # "USDC": "USDC-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
}


async def get_all_crypto_price():
    async with httpx.AsyncClient() as client:

        async def fetch(crypto: str):
            data = await get_price_and_change_simple(crypto, client)
            if data:
                price = data["price"]
                change_24h_percent = data["change_24h_percent"]
                return (
                    crypto,
                    {
                        "f58": crypto,
                        "f14": crypto,
                        "f43": price,
                        "f170": change_24h_percent,
                        "f48": "",
                    },
                )
            return None

        tasks = [fetch(crypto) for crypto in CRYPTO_MAP]
        results = await asyncio.gather(*tasks)
        return {crypto: info for item in results if item for crypto, info in [item]}


async def get_price_and_change_simple(
    crypto: str = "BTCUSD",
    client: Optional[httpx.AsyncClient] = None,
):
    """
    通过单次异步请求OKX指数API，高效获取BTC的最新价格、
    滚动24小时涨跌幅和UTC+8当天涨跌幅。
    """
    url = "https://www.okx.com/api/v5/market/index-tickers"
    params = {"instId": CRYPTO_MAP.get(crypto, crypto)}

    # 如果没有传入client，则新建一个
    close_client = False
    if client is None:
        client = httpx.AsyncClient()
        close_client = True

    try:
        logger.info(f"正在异步查询 {crypto} 指数行情...")
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("code") == "0":
            ticker_info = data["data"][0]

            current_price = float(ticker_info.get("idxPx", "0"))
            open_24h_price = float(ticker_info.get("open24h", "0"))
            open_utc8_price = float(ticker_info.get("sodUtc8", "0"))

            if open_24h_price == 0:
                change_24h_percent = float("inf")
            else:
                change_24h_percent = ((current_price - open_24h_price) / open_24h_price) * 100

            if open_utc8_price == 0:
                change_utc8_daily_percent = float("inf")
            else:
                change_utc8_daily_percent = ((current_price - open_utc8_price) / open_utc8_price) * 100

            return {
                "price": current_price,
                "open_24h": open_24h_price,
                "open_utc8": open_utc8_price,
                "change_24h_percent": change_24h_percent,
                "change_utc8_daily_percent": change_utc8_daily_percent,
            }
        else:
            logger.error(f"API 返回错误: {data.get('msg')}")
            return None

    except httpx.RequestError as e:
        logger.error(f"网络请求错误: {e}")
        return None
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"解析或计算数据时出错: {e}")
        return None
    finally:
        if close_client:
            await client.aclose()
