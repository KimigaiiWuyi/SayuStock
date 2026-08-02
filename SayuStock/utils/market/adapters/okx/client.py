"""OKX REST 拉取原生 candle（不包装成东财 JSON）。"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone, timedelta

import httpx

from gsuid_core.logger import logger

from ...enums import KlinePeriod
from ...errors import MarketError, empty_error, network_error
from ....get_OKX import FREQ_MAP, CRYPTO_MAP, FREQ_TO_SECONDS, analyze_market_target

PROVIDER = "okx"
_TZ_UTC8 = timezone(timedelta(hours=8))


def normalize_inst_id(query: str) -> str | None:
    market_type, code = analyze_market_target(query)
    if market_type != "crypto":
        return None
    inst_id = code.strip().upper()
    if inst_id in CRYPTO_MAP:
        inst_id = CRYPTO_MAP[inst_id]
    if inst_id in {"BTC", "ETH", "SOL", "DOGE", "PEPE"}:
        inst_id = f"{inst_id}-USDT"
    if inst_id.endswith("-USD") and not inst_id.endswith("-USDT"):
        inst_id = inst_id.replace("-USD", "-USDT")
    return inst_id


def period_to_okx_bar(period: KlinePeriod) -> str:
    return FREQ_MAP.get(period.value, FREQ_MAP.get(str(period.value).lower(), "1D"))


async def fetch_today_1m_candles(inst_id: str) -> list[object] | MarketError:
    url = "https://www.okx.com/api/v5/market/candles"
    now = datetime.now(_TZ_UTC8)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = int(today_start.timestamp() * 1000)
    all_candles: list[object] = []
    after = ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(20):
                params: dict[str, str] = {"instId": inst_id, "bar": "1m", "limit": "100"}
                if after:
                    params["after"] = after
                response = await client.get(url, params=params)
                if response.status_code != 200:
                    return network_error(f"OKX HTTP {response.status_code}", provider=PROVIDER)
                body = response.json()
                if not isinstance(body, dict) or body.get("code") != "0":
                    msg = body.get("msg") if isinstance(body, dict) else "bad response"
                    return network_error(f"OKX API: {msg}", provider=PROVIDER)
                data = body.get("data")
                if not isinstance(data, list) or not data:
                    break
                for c in data:
                    if isinstance(c, list) and c and int(c[0]) >= start_ts:
                        all_candles.append(c)
                if isinstance(data[-1], list) and data[-1] and int(data[-1][0]) < start_ts:
                    break
                after = str(data[-1][0]) if isinstance(data[-1], list) else ""
            if not all_candles:
                params = {"instId": inst_id, "bar": "1m", "limit": "1"}
                resp = await client.get(url, params=params)
                body = resp.json()
                data = body.get("data") if isinstance(body, dict) else None
                if isinstance(data, list) and data:
                    all_candles = list(data)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
        logger.error(f"[OKX] fetch 1m fail: {e}")
        return network_error(str(e), provider=PROVIDER)
    if not all_candles:
        return empty_error("OKX 无分时 candle", provider=PROVIDER)
    all_candles.sort(key=lambda x: int(x[0]) if isinstance(x, list) else 0)
    return all_candles


async def fetch_history_candles(
    inst_id: str,
    period: KlinePeriod,
    *,
    start: date | None,
    end: date | None,
) -> list[object] | MarketError:
    bar = period_to_okx_bar(period)
    end_d = end or date.today()
    start_d = start or (end_d - timedelta(days=365))
    time_diff = max(
        0.0,
        (datetime.combine(end_d, datetime.min.time()) - datetime.combine(start_d, datetime.min.time())).total_seconds(),
    )
    interval_seconds = FREQ_TO_SECONDS.get(bar, 86400)
    count = min(1440, max(10, math.ceil(time_diff / interval_seconds) + 60))

    url = "https://www.okx.com/api/v5/market/history-candles"
    all_candles: list[object] = []
    after = ""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            max_loops = (count // 100) + 2
            for _ in range(max_loops):
                params: dict[str, str] = {"instId": inst_id, "bar": bar, "limit": "100"}
                if after:
                    params["after"] = after
                response = await client.get(url, params=params)
                if response.status_code != 200:
                    url = "https://www.okx.com/api/v5/market/candles"
                    response = await client.get(url, params=params)
                    if response.status_code != 200:
                        break
                body = response.json()
                if not isinstance(body, dict) or body.get("code") != "0":
                    break
                data = body.get("data")
                if not isinstance(data, list) or not data:
                    break
                all_candles.extend(data)
                after = str(data[-1][0]) if isinstance(data[-1], list) else ""
                if len(all_candles) >= count:
                    break
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
        logger.error(f"[OKX] fetch history fail: {e}")
        return network_error(str(e), provider=PROVIDER)
    if not all_candles:
        return empty_error(f"未获取到 {inst_id} {bar} K线", provider=PROVIDER)
    final = all_candles[:count]
    final.sort(key=lambda x: int(x[0]) if isinstance(x, list) else 0)
    return final


async def fetch_index_ticker(inst_id: str) -> dict[str, float] | MarketError:
    url = "https://www.okx.com/api/v5/market/index-tickers"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params={"instId": inst_id})
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as e:
        return network_error(str(e), provider=PROVIDER)
    if not isinstance(body, dict) or ("code" not in body) or body["code"] != "0":
        return network_error("index-tickers 失败", provider=PROVIDER)
    data = body["data"] if "data" in body else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return empty_error("index-tickers 空", provider=PROVIDER)
    row = data[0]
    try:
        return {
            "price": float(row["idxPx"]) if "idxPx" in row else 0.0,
            "open_24h": float(row["open24h"]) if "open24h" in row else 0.0,
            "open_utc8": float(row["sodUtc8"]) if "sodUtc8" in row else 0.0,
        }
    except (TypeError, ValueError, KeyError) as e:
        from ...errors import parse_error

        return parse_error(str(e), provider=PROVIDER)
