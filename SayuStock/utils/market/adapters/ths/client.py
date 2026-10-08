"""同花顺金融数据 API（扶摇 fuyao.aicubes.cn）HTTP：A股票/指数/场内基金 快照与日K。

- 鉴权：请求头 `X-api-key`，Key 在后台「行情API → 同花顺API密钥」配置。
- 响应统一信封 `{code, message, request_id, data}`，code=0 成功；
  HTTP 恒 200，限流时可能 429。实测错误码与文档略有出入：
  无效/缺失 Key 返回 2003（文档写 2001）、未知代码返回 1002（文档写 3001）。
- 量额口径与领域模型一致：成交量=股、成交额=元，无需换算。
"""

from __future__ import annotations

from typing import Mapping
from collections.abc import Sequence

from aiohttp import ClientError, ClientSession, ClientTimeout

from gsuid_core.logger import logger

from ...errors import MarketError, empty_error, parse_error, unsupported, network_error

PROVIDER = "ths"
BASE_URL = "https://fuyao.aicubes.cn"
# 内置公共 Key（后台 ths_api_key 读不到时的兜底，与东财默认 Cookie 同策略）
DEFAULT_API_KEY = "sk-fuyao-ORe_1l_p-CogNpfJpfU90yzO7LjLkMOE"
STOCK_SNAPSHOT_URL = f"{BASE_URL}/api/a-share/prices/snapshot"
STOCK_HISTORICAL_URL = f"{BASE_URL}/api/a-share/prices/historical"
INDEX_SNAPSHOT_URL = f"{BASE_URL}/api/a-share-index/prices/snapshot"
INDEX_HISTORICAL_URL = f"{BASE_URL}/api/a-share-index/prices/historical"
FUND_SNAPSHOT_URL = f"{BASE_URL}/api/fund/market/snapshot"
FUND_HISTORICAL_URL = f"{BASE_URL}/api/fund/market/historical"
_TIMEOUT = ClientTimeout(total=20)


def _api_key() -> str:
    """后台「同花顺API密钥」；读不到（单测/配置层不可用）用内置公共 Key。"""
    try:
        # adapters/ths 距 SayuStock 包根 4 级，需 5 个点（4 个点会落到 utils.stock_config）
        from .....stock_config.stock_config import STOCK_CONFIG

        key = STOCK_CONFIG.get_config("ths_api_key").data
        if isinstance(key, str) and key.strip():
            return key.strip()
    except Exception:  # noqa: BLE001 - 配置层不可用时用内置 Key 保底
        pass
    return DEFAULT_API_KEY


def _envelope_error(code: int, message: str) -> MarketError:
    """扶摇业务错误码 → MarketError（映射按实测口径，见模块 docstring）。"""
    text = f"同花顺 {code}: {message}"
    if code in (2001, 2003):
        return network_error(f"{text}（检查后台「同花顺API密钥」）", provider=PROVIDER)
    if code in (3001, 3004):
        # 标的不存在/类型不支持该能力：解析层已确认标的存在，多为覆盖面差异 → 跳过顺延
        return unsupported(text, provider=PROVIDER)
    if code == 3002:
        return empty_error(text, provider=PROVIDER)
    if code == 4001 or code >= 5000:
        return network_error(text, provider=PROVIDER)
    return parse_error(text, provider=PROVIDER)


async def _get_data(url: str, params: Mapping[str, object]) -> object | MarketError:
    """GET + X-api-key → ApiResponse.data 节点；信封错误码统一转 MarketError。"""
    # 查询参数统一转 str：aiohttp Query 类型不含 object，dict[str, str] 才能通过
    # basedpyright==1.39.7（CI 锁定版本）的参数检查
    query: dict[str, str] = {key: str(value) for key, value in params.items()}
    try:
        async with ClientSession(headers={"X-api-key": _api_key()}, timeout=_TIMEOUT) as sess:
            async with sess.get(url, params=query) as res:
                if res.status == 429:
                    return network_error("同花顺 HTTP 429 限流", provider=PROVIDER)
                if res.status != 200:
                    return network_error(f"同花顺 HTTP {res.status}", provider=PROVIDER)
                try:
                    payload: object = await res.json(content_type=None)
                except (ValueError, TypeError) as e:
                    return parse_error(f"同花顺 JSON 无效: {e}", provider=PROVIDER)
    except (ClientError, TimeoutError) as e:
        logger.warning(f"[SayuStock][同花顺] 请求失败: {e}")
        return network_error(str(e), provider=PROVIDER)
    if not isinstance(payload, Mapping):
        return parse_error("同花顺响应信封非对象", provider=PROVIDER)
    code = payload.get("code")
    if code != 0:
        return _envelope_error(code if isinstance(code, int) else -1, str(payload.get("message") or ""))
    data = payload.get("data")
    if data is None:
        return empty_error("同花顺响应 data 为空", provider=PROVIDER)
    return data


async def fetch_stock_snapshot(thscodes: Sequence[str]) -> object | MarketError:
    """A 股个股快照（thscodes 批量，北交所 .BJ 亦覆盖）。"""
    return await _get_data(STOCK_SNAPSHOT_URL, {"thscodes": ",".join(thscodes)})


async def fetch_index_snapshot(thscodes: Sequence[str]) -> object | MarketError:
    """A 股指数快照（thscodes 批量，含同花顺板块指数 .TI）。"""
    return await _get_data(INDEX_SNAPSHOT_URL, {"thscodes": ",".join(thscodes)})


async def fetch_fund_snapshot(thscode: str) -> object | MarketError:
    """场内基金（ETF/LOF）快照；该端点仅支持单只。"""
    return await _get_data(FUND_SNAPSHOT_URL, {"thscode": thscode})


async def fetch_stock_historical(thscode: str, start_ms: int, end_ms: int) -> object | MarketError:
    """个股日K（前复权，窗口 ≤ 10 年）。"""
    return await _get_data(
        STOCK_HISTORICAL_URL,
        {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms, "adjust": "forward"},
    )


async def fetch_index_historical(thscode: str, start_ms: int, end_ms: int) -> object | MarketError:
    """指数日K（无复权语义，窗口 ≤ 10 年）。"""
    return await _get_data(
        INDEX_HISTORICAL_URL,
        {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms},
    )


async def fetch_fund_historical(thscode: str, start_ms: int, end_ms: int) -> object | MarketError:
    """ETF 日K（前复权口径，窗口 ≤ 5 个自然年）。"""
    return await _get_data(
        FUND_HISTORICAL_URL,
        {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms},
    )
