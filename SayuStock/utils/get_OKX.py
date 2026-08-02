import re
from typing import Union, Optional

import httpx

from gsuid_core.logger import logger

FREQ_MAP = {
    # 日线
    "101": "1D",
    "d": "1D",
    "day": "1D",
    "1d": "1D",
    # 周线
    "102": "1W",
    "w": "1W",
    "week": "1W",
    "1w": "1W",
    # 月线
    "103": "1M",
    "m": "1M",
    "month": "1M",
    "1m": "1M",
    # 季线 (OKX支持 3M)
    "104": "3M",
    "q": "3M",
    "quarter": "3M",
    "3m": "3M",
    # 半年线 (OKX支持 6M)
    "105": "6M",
    "h": "6M",
    "half": "6M",
    "6m": "6M",
    # 年线 (OKX支持 1Y)
    "106": "1Y",
    "y": "1Y",
    "year": "1Y",
    "1y": "1Y",
}
FREQ_TO_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "1D": 86400,
    "1W": 604800,
    "1M": 2629746,  # 约30.44天
    "3M": 7889238,  # 约91.3天
    "6M": 15778476,  # 约182.6天
    "1Y": 31556952,  # 约365.24天
}

# 币种名称到 OKX API instId 的映射
CRYPTO_MAP = {
    "BTC USD": "BTC-USD",
    "ETH USD": "ETH-USD",
    "BTCUSD": "BTC-USD",
    "BTC": "BTC-USD",
    # "USDT": "USDT-USD",
    # "USDC": "USDC-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "ETH": "ETH-USDT",
    "DOGE": "DOGE-USDT",
    "PEPE": "PEPE-USDT",
    "SUI": "SUI-USDT",
    "BNB": "BNB-USDT",
    "AVAX": "AVAX-USDT",
    "LINK": "LINK-USDT",
    "ADA": "ADA-USDT",
    "TRX": "TRX-USDT",
    "SHIB": "SHIB-USDT",
    "DOT": "DOT-USDT",
    "LTC": "LTC-USDT",
    "BCH": "BCH-USDT",
    "NEAR": "NEAR-USDT",
    "MATIC": "MATIC-USDT",
    "UNI": "UNI-USDT",
    "APT": "APT-USDT",
    "OP": "OP-USDT",
    "ARB": "ARB-USDT",
    "ORDI": "ORDI-USDT",
    "SATS": "SATS-USDT",
}

CRYPTO_MAP.update({value: value for key, value in CRYPTO_MAP.items() if key not in value})


def analyze_market_target(query: str) -> str:
    """
    分析用户输入，判断是股票还是虚拟货币。

    Returns:
        tuple: (market_type, formatted_code)
        market_type: 'stock' | 'crypto'
        formatted_code: 清洗后的代码 (用于传给对应API)
    """
    # 0. 预处理：去空格，转大写
    clean_query = query.strip().upper()

    # 1. 特征一：包含中文字符 -> 肯定是股票 (如：贵州茅台)
    if re.search(r"[\u4e00-\u9fa5]", clean_query):
        return "stock", clean_query

    # 2. 特征二：包含 "-USDT" 或 "-USD" -> 肯定是虚拟货币
    # OKX 的标准格式通常是 BTC-USDT
    if "-USD" in clean_query:
        # 如果用户没写全(比如只写了 BTC-USD)，尝试补全T，或者直接透传给OKX看看
        # 这里假设用户如果带了横杠，就是想查币
        return "crypto", clean_query

    # 3. 特征三：纯数字 (通常A股是6位) -> 视为股票
    # 就算有叫 "123" 的币，通常大家也是查股票代码
    if clean_query.isdigit():
        return "stock", clean_query

    # 4. 特征四：检查热门币种白名单
    # 如果用户输入 "btc"，这里匹配到 "BTC"，返回 "BTC-USDT"
    if clean_query in CRYPTO_MAP:
        return "crypto", CRYPTO_MAP[clean_query]

    # 5. 特征五：常用股票后缀 (hk, sh, sz) -> 视为股票
    # 比如 00700.hk
    if any(clean_query.endswith(suffix) for suffix in [".HK", ".SH", ".SZ", ".BJ"]):
        return "stock", clean_query

    # 6. 兜底逻辑 (灰色地带)
    # 剩下的通常是 3-5 个字母的字符串，如 "AAPL", "NVDA", "ORDI"
    # 这里是一个策略选择：
    #   - 策略 A (保守): 默认股票。因为美股代码也是字母。
    #   - 策略 B (激进): 如果看起来像币的格式，先查币。

    # 建议：默认视为股票 (因为东方财富覆盖了美股)。
    # 除非用户显式输入 "ORDI-USDT" (规则2) 或者在白名单里 (规则4)。
    # 如果你想让非白名单的冷门币也能查到，可以加一个判断：

    return "stock", clean_query


async def get_all_crypto_price() -> object:
    """批量加密货币行情：语义字段 name/price/change_pct。"""
    from .market import get_market, is_market_error

    market = get_market()
    out: dict[str, dict[str, object]] = {}
    for crypto in ("BTC", "ETH", "SOL", "XRP"):
        q = await market.quote(crypto)
        if is_market_error(q):
            continue
        out[crypto] = {
            "name": q.symbol.name,
            "price": q.price,
            "change_pct": q.change_pct,
        }
    return out


async def get_crypto_trend(
    crypto: str = "BTC-USDT",
    client: Optional[httpx.AsyncClient] = None,
    proxy: Optional[str] = None,
) -> object:
    """OKX 分时 → IntradaySeries | MarketError。"""
    _ = client, proxy
    from .market import get_market

    return await get_market().intraday(crypto)


async def get_crypto_history_kline(
    crypto: str = "BTC-USDT",
    freq: Union[str, int] = "101",
    start_time: str = "",
    end_time: str = "",
    client: Optional[httpx.AsyncClient] = None,
    proxy: Optional[str] = None,
) -> object:
    """OKX 历史 K 线 → KlineSeries | MarketError。"""
    _ = client, proxy
    from datetime import date, datetime

    from .market import KlinePeriod, get_market

    try:
        period = KlinePeriod(str(freq))
    except ValueError:
        period = KlinePeriod.D1
    start_d: date | None = None
    end_d: date | None = None
    if start_time:
        try:
            if len(start_time) >= 8 and start_time[:8].isdigit():
                start_d = datetime.strptime(start_time[:8], "%Y%m%d").date()
            else:
                start_d = datetime.strptime(start_time[:10], "%Y-%m-%d").date()
        except ValueError:
            start_d = None
    if end_time:
        try:
            if len(end_time) >= 8 and end_time[:8].isdigit():
                end_d = datetime.strptime(end_time[:8], "%Y%m%d").date()
            else:
                end_d = datetime.strptime(end_time[:10], "%Y-%m-%d").date()
        except ValueError:
            end_d = None
    return await get_market().kline(crypto, period, start=start_d, end=end_d)


# 旧名保留，避免外部 import 瞬间炸掉；请改用无 _as_json 版本
get_crypto_trend_as_json = get_crypto_trend
get_crypto_history_kline_as_json = get_crypto_history_kline


async def get_price_and_change_simple(
    crypto: str = "BTCUSD",
    client: Optional[httpx.AsyncClient] = None,
) -> object:
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
