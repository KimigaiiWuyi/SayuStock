import re
import asyncio
import datetime
import traceback
from typing import Any, Dict, Optional

import httpx

from gsuid_core.logger import logger

from .stock.utils import async_file_cache

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


def analyze_market_target(query: str):
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


@async_file_cache(market="{crypto}", sector="single-stock-crypto", suffix="json")
async def get_crypto_trend_as_json(
    crypto: str = "BTC-USDT", client: Optional[httpx.AsyncClient] = None, proxy: Optional[str] = None
) -> Dict[str, Any]:
    """
    获取OKX数据并完美伪装成东方财富A股JSON格式 (修复KeyError: f168)
    """

    # --- 1. ID 修正逻辑 ---
    inst_id = crypto.strip().upper()
    if inst_id in ["BTC", "ETH", "SOL", "DOGE", "PEPE"]:
        inst_id = f"{inst_id}-USDT"
    if inst_id.endswith("-USD"):
        inst_id = inst_id.replace("-USD", "-USDT")

    url = "https://www.okx.com/api/v5/market/candles"

    # --- 2. 客户端初始化 ---
    should_close_client = False
    if client is None:
        mounts = (
            {
                "http://": httpx.HTTPTransport(proxy=proxy),
                "https://": httpx.HTTPTransport(proxy=proxy),
            }
            if proxy
            else None
        )
        client = httpx.AsyncClient(mounts=mounts, timeout=10.0)
        should_close_client = True

    # --- 3. 时间计算 (UTC+8) ---
    tz_utc8 = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz_utc8)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = int(today_start.timestamp() * 1000)

    all_candles = []

    try:
        logger.info(f"正在获取 {inst_id} (Proxy: {proxy})...")

        # --- 4. 分页获取数据 ---
        after = ""
        for _ in range(20):  # 限制最大页数
            params = {"instId": inst_id, "bar": "1m", "limit": 100}
            if after:
                params["after"] = after

            response = await client.get(url, params=params)
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}")

            res_json = response.json()
            if res_json.get("code") != "0":
                raise Exception(f"OKX API Error: {res_json.get('msg')}")

            data = res_json.get("data", [])
            if not data:
                break

            chunk_valid = [c for c in data if int(c[0]) >= start_ts]
            all_candles.extend(chunk_valid)

            if int(data[-1][0]) < start_ts:
                break
            after = data[-1][0]

        if not all_candles:
            # 兜底：如果完全没数据（比如刚过0点），尝试取最近一根
            logger.warning("未获取到今日K线，尝试获取最近一根作为快照")
            params = {"instId": inst_id, "bar": "1m", "limit": 1}
            resp = await client.get(url, params=params)
            data = resp.json().get("data", [])
            if data:
                all_candles = data
            else:
                raise Exception("No data retrieved")

        # 排序：旧 -> 新
        all_candles.sort(key=lambda x: int(x[0]))

        # --- 5. 数据清洗与统计 ---
        trends_list = []
        day_open = float(all_candles[0][1])
        day_high = -1.0
        day_low = float("inf")
        last_close = 0.0
        total_vol = 0.0
        total_money = 0.0

        for candle in all_candles:
            ts = int(candle[0])
            c = float(candle[4])
            h = float(candle[2])
            l = float(candle[3])
            vol = float(candle[5])
            turnover = float(candle[7])

            day_high = max(day_high, h)
            day_low = min(day_low, l)
            last_close = c
            total_vol += vol
            total_money += turnover

            # 格式化时间 HH:MM
            dt_obj = datetime.datetime.fromtimestamp(ts / 1000, tz=tz_utc8)
            time_str = dt_obj.strftime("%H:%M")
            avg_price = (turnover / vol) if vol > 0 else c

            trends_list.append(
                {
                    "datetime": time_str,
                    "price": c,
                    "open": float(candle[1]),
                    "high": h,
                    "low": l,
                    "amount": int(vol),  # 必须转int，部分绘图库不支持float量
                    "money": turnover,
                    "avg_price": round(avg_price, 2),
                }
            )

        change_amt = last_close - day_open
        change_pct = (change_amt / day_open * 100) if day_open != 0 else 0

        # --- 6. 核心：构建完整的“伪股票”快照 ---
        # 补全了 f168 以及 f31-f40 (五档盘口)
        snapshot_data = {
            "f43": last_close,  # 最新价
            "f44": day_high,  # 最高
            "f45": day_low,  # 最低
            "f46": day_open,  # 今开
            "f47": int(total_vol),  # 成交量(手)
            "f48": total_money,  # 成交额
            "f57": inst_id,  # 代码
            "f58": inst_id,  # 名称
            "f59": 2,  # 小数点精度 (A股通常是2，设8可能导致渲染器格式化报错)
            "f60": day_open,  # 昨收 (用今开模拟，保证幅度计算)
            "f107": 1,  # 市场标识
            "f169": round(change_amt, 2),  # 涨跌额
            "f170": round(change_pct, 2),  # 涨跌幅 %
            # --- 关键补丁开始 ---
            "f168": 0.0,  # 换手率 (Turnover Rate) - 修复 KeyError 的关键！
            "f177": int(total_vol),  # 内外盘总量 (模拟)
            "f277": 0,  # 总市值 (Total Value)
            "f278": 0,  # 流通市值
            # 五档盘口 (Buy/Sell 1-5) - 全部填当前价，防止数组越界
            "f19": last_close,
            "f20": 1,  # 买1
            "f17": last_close,
            "f18": 1,  # 买2
            "f15": last_close,
            "f16": 1,  # 买3
            "f13": last_close,
            "f14": 1,  # 买4
            "f11": last_close,
            "f12": 1,  # 买5
            "f31": last_close,
            "f32": 1,  # 卖1
            "f33": last_close,
            "f34": 1,  # 卖2
            "f35": last_close,
            "f36": 1,  # 卖3
            "f37": last_close,
            "f38": 1,  # 卖4
            "f39": last_close,
            "f40": 1,  # 卖5
            # 其他垃圾字段填充
            "f111": 0,
            "f152": 2,
            "f260": "-",
            "f261": "-",
            "f279": 0,
            "f288": 0,
            # --- 关键补丁结束 ---
        }

        result_json = {
            "rc": 0,
            "rt": 4,
            "svr": 181214693,
            "lt": 1,
            "full": 1,
            "dlmkts": "",
            "data": snapshot_data,
            "trends": trends_list,
            # 文件名稍微改一下格式，确保兼容
            "file_name": f"{inst_id}_single-stock_None_data.json",
        }

        return result_json

    except Exception as e:
        logger.error(f"Crypto Error: {e}")
        logger.error(traceback.format_exc())
        return {"rc": 1, "msg": f"Error: {str(e)}", "data": {}, "trends": [], "file_name": "error.json"}

    finally:
        if should_close_client:
            await client.aclose()


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
