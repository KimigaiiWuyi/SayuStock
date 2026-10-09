import json
from io import BytesIO
from typing import Dict, List, Tuple, Union, Optional
from datetime import datetime, timedelta

import aiofiles
from PIL import Image, UnidentifiedImageError
from aiohttp import ClientSession, ClientTimeout, ClientConnectionError

from gsuid_core.logger import logger

from .utils import get_file
from ..constant import PREFIX_DATA, code_id_dict, chinese_stocks, code_query_overrides
from ...stock_config.stock_config import STOCK_CONFIG

SEARCHAPI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7",
    "Cache-Control": "max-age=0",
    "Dnt": "1",
    "Sec-Ch-Ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _get_searchapi_headers() -> Dict[str, str]:
    """构建 searchapi 请求头，注入配置中的 Cookie。"""
    headers = dict(SEARCHAPI_HEADERS)
    cookies = STOCK_CONFIG.get_config("eastmoney_cookie").data
    if cookies:
        headers["Cookie"] = cookies
    return headers


async def get_fund_pos_list(fcode: Union[str, int]) -> Optional[Dict[str, object]]:
    _api = "https://fundwebapi.eastmoney.com/FundMEApi/FundPositionList"
    params = {
        "pageIndex": "1",
        "pageSize": "10",
        "deviceid": "1234567.py.service",
        "version": "4.3.0",
        "product": "Eastmoney",
        "plat": "Web",
        "FCODE": str(fcode),
    }
    async with ClientSession() as sess:
        try:
            async with sess.get(_api, params=params) as res:
                if res.status == 200:
                    data = await res.json()
                    logger.info(f"[SayuStock]获取{params['FCODE']}持仓数据成功")
                    return data
        except ClientConnectionError:
            logger.warning(f"[SayuStock]获取{params['FCODE']}持仓数据失败")
    return None


# 显式市场后缀（与 _get_code_id_one 里的剥离顺序一致：.hk 要在 .h 前判定）
_MARKET_SUFFIXES: tuple[str, ...] = (".hk", ".us", ".kr", ".h", ".a")


def _code_query_candidates(raw: str) -> List[str]:
    """拆分「600519 贵州茅台」等复合查询，优先纯代码再名称。

    带显式市场后缀（.us/.h/.kr/.a）时**只回整串**：否则「600519.us」会先被抽出
    裸代码「600519」，以 priority=None 命中 A 股，用户指定的市场被前面的候选架空。
    """
    import re

    text = (raw or "").strip()
    if not text:
        return []
    if text.lower().endswith(_MARKET_SUFFIXES):
        return [text]
    out: List[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        t = s.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    # 代码优先：复合串先抽 6 位 / secid，再试原文与名称
    for m in re.finditer(r"\b([0-3]\.\d{6})\b", text):
        _add(m.group(1))
    for m in re.finditer(r"\b(\d{6})\b", text):
        _add(m.group(1))
    for part in re.split(r"[\s,，/|+\-—]+", text):
        _add(part)
    name_only = re.sub(r"[\d.\s,，/|+\-—]+", "", text)
    _add(name_only)
    _add(text)
    return out


class ResolveLayerError(Exception):
    """行情ID解析层（东财 searchapi）不可用：网络/HTTP 失败。

    与「标的不存在」（HTTP 200 且无结果 → None）区分开，供行情优先级链
    在解析层瞬断时返回 network 错误顺延，而不是误报 not_found 短路。
    """


async def get_code_id(code: str, priority: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
    """
    生成东方财富股票专用的行情ID
    code:可以是代码或简称或英文
    """
    try:
        return await get_code_id_strict(code, priority)
    except ResolveLayerError:
        return None


async def get_code_id_strict(code: str, priority: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
    """同 get_code_id，但解析层失败时抛 ResolveLayerError；None 仅表示标的不存在。"""
    candidates = _code_query_candidates(code)
    if not candidates:
        return None
    # 复合 query 依次尝试；首个成功即返回；存在解析层失败时优先抛出（宁报网络错不误报不存在）
    last: Optional[Tuple[str, str, str]] = None
    layer_error: Optional[ResolveLayerError] = None
    for cand in candidates:
        try:
            hit = await _get_code_id_one(cand, priority)
        except ResolveLayerError as error:
            layer_error = error
            continue
        if hit is not None:
            return hit
        last = hit
    if layer_error is not None:
        raise layer_error
    return last


# 纯 6 位代码本地映射。60/68/30 与场内基金前缀不会和指数撞码；
# 00/京市必须在名称表里才短路，避免 000300 被当成深市股票。
_LOCAL_DIGIT_MARKET: Dict[str, tuple[str, str]] = {
    "60": ("1", "沪A"),
    "68": ("1", "科创板"),
    "30": ("0", "创业板"),
    "00": ("0", "深A"),
    "43": ("0", "京A"),
    "83": ("0", "京A"),
    "87": ("0", "京A"),
    "92": ("0", "京A"),
    "51": ("1", "基金"),
    "56": ("1", "基金"),
    "58": ("1", "基金"),
    "15": ("0", "基金"),
    "16": ("0", "基金"),
}


def local_digit_code(code: str) -> Optional[Tuple[str, str, str]]:
    """6 位数字 → (secid, 名称, 证券类型)。无法确定时返回 None，交给 searchapi。"""
    text = code.strip()
    if not text.isdigit() or len(text) != 6:
        return None
    prefix = text[:2]
    if prefix not in _LOCAL_DIGIT_MARKET:
        return None
    market, sec_type = _LOCAL_DIGIT_MARKET[prefix]
    if text.startswith("688"):
        sec_type = "科创板"
    elif text.startswith("300"):
        sec_type = "创业板"
    info = chinese_stocks.get(text)
    named = info is not None
    # 00 与京市和指数撞码，名称表里没有就不本地猜测
    if not named and text[:2] in ("00", "43", "83", "87", "92"):
        return None
    name = info["name"] if info is not None else ""
    return f"{market}.{text}", name, sec_type


# 本地 A 股名称表（chinese_stocks）的代码前缀 → 东财 secid 市场前缀。
# 表里只有这些前缀（实测 5909 条：00/30/60/68/81/83/92）；
# 未列出的前缀一律不补名，宁缺勿错。
_LOCAL_NAME_MARKET: Dict[str, str] = {
    "60": "1",
    "68": "1",
    "00": "0",
    "30": "0",
    "43": "0",
    "81": "0",
    "83": "0",
    "87": "0",
    "92": "0",
}


# 市场后缀（.h/.us/.kr/.a）→ 该市场在东财 searchapi 里对应的 SecurityTypeName。
# 搜索有结果但一个都不属于目标市场时按「没有这只票」处理，绝不跨市场兜底。
_MARKET_SEC_TYPES: Dict[str, frozenset] = {
    "h": frozenset({"港股"}),
    "us": frozenset({"美股", "粉单"}),
    "kr": frozenset({"韩股"}),
    "a": frozenset({"沪深A", "沪A", "深A", "创业板", "科创板", "京A"}),
}


async def _get_code_id_one(code: str, priority: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
    """单次解析行情 ID（不做复合 query 拆分）。"""
    override = code_query_overrides.get(code.strip().lower())
    if override is not None:
        return override
    if code.endswith(".h"):
        code = code[: -len(".h")]
        priority = "h"
    elif code.endswith(".hk"):
        code = code[: -len(".hk")]
        priority = "h"
    elif code.endswith(".us"):
        code = code[: -len(".us")]
        priority = "us"
    elif code.endswith(".kr"):
        code = code[: -len(".kr")]
        priority = "kr"
    elif code.endswith(".a"):
        code = code[: -len(".a")]
        priority = "a"

    if priority is not None:
        priority = priority.lower()

    is_bond = False
    if code in ["us10y", "us30y", "us2y", "cn10y", "cn30y", "cn2y", "tlm"]:
        is_bond = True

    if "." in code:
        code_prefix, main_code = code.split(".", 1)
        if code_prefix in PREFIX_DATA:
            _sec_type = PREFIX_DATA[code_prefix]
        else:
            _sec_type = "未知"
        # 按代码细化 A 股版块标签（创业板/科创板/京A），供标题展示
        if code_prefix in ("0", "1"):
            if main_code.startswith("300"):
                _sec_type = "创业板"
            elif main_code.startswith("688"):
                _sec_type = "科创板"
            elif main_code.startswith(("4", "8", "92")):
                _sec_type = "京A"
            elif code_prefix == "0":
                _sec_type = "深A"
            elif code_prefix == "1":
                _sec_type = "沪A"

        # secid 形态本地短路（不发网络）。名称尽力从随仓库分发的 A 股表补：
        # 缺名会让 Quote.symbol.name 退化成代码，同花顺（快照无名称，沿用解析层）
        # 供价时 matcher._is_st 判不出 ST，模拟盘涨跌停拦截会从 ±5% 退回 ±10%。
        # 守卫：表按 6 位代码索引，指数与个股会撞码（1.000001 上证指数 vs
        # 000001 平安银行），只有 secid 前缀与该代码的真实市场一致才补名。
        info = chinese_stocks.get(main_code) if _LOCAL_NAME_MARKET.get(main_code[:2]) == code_prefix else None
        return code, (info["name"] if info else ""), _sec_type

    if code in code_id_dict.keys():
        return code_id_dict[code], code, ""

    # 带 .us/.h/.kr 时不能把 6 位代码当成 A 股，否则 600519.us 会短路成茅台。
    if priority is None or priority == "a":
        local = local_digit_code(code)
        if local is not None:
            return local

    url = "https://searchapi.eastmoney.com/api/suggest/get"
    params = (
        ("input", f"{code}"),
        ("type", "14"),
        # ("token", "D43BF722C8E33BDC906FB84D85E326E8"),
        ("count", "4"),
    )
    async with ClientSession(headers=_get_searchapi_headers(), timeout=ClientTimeout(total=15)) as sess:
        try:
            async with sess.get(url, params=params) as res:
                if res.status != 200:
                    raise ResolveLayerError(f"searchapi HTTP {res.status}")
                logger.debug(f"[SayuStock]开始获取{code}的ID")
                text = await res.text()
                logger.debug(text)
                data = json.loads(text)
                code_dict: List[Dict] = data["QuotationCodeTable"]["Data"]
                if code_dict:
                    # 排序：SecurityTypeName为"债券"的排到最后
                    if not is_bond:
                        code_dict.sort(key=lambda x: x.get("SecurityTypeName") == "债券")
                    if priority is None:
                        # 未指定市场：取搜索首项（债券已排到最后）
                        first = code_dict[0]
                        return (
                            first["QuoteID"],
                            first["Name"],
                            first["SecurityTypeName"],
                        )
                    accepted = _MARKET_SEC_TYPES[priority]
                    for i in code_dict:
                        if i["SecurityTypeName"] in accepted:
                            return (
                                i["QuoteID"],
                                i["Name"],
                                i["SecurityTypeName"],
                            )
                    # 有搜索结果但没有该市场的标的（例：600519.us 只搜到沪A 贵州茅台）。
                    # 此时必须返回 None：曾经的 for/else 兜底回的是
                    # 「第一项的 QuoteID/名称 + 最后一项的证券类型」，
                    # A 股会被当成美股解析出去，下游按错误的 secid 取价。
                    return None
                else:
                    # HTTP 200 且无结果：标的确切不存在
                    return None
        except ClientConnectionError as error:
            logger.error(f"[SayuStock] 获取{code}的ID失败: {error}")
            raise ResolveLayerError(str(error)) from error
        except Exception as error:
            logger.error(f"[SayuStock] 获取{code}的ID异常: {error}")
            raise ResolveLayerError(str(error)) from error
    return None


async def get_image_from_em(
    name: str = "0.899001",
    size: Optional[Tuple[int, int]] = None,
) -> Image.Image:
    WEBPIC = "https://webquotepic.eastmoney.com/GetPic.aspx"
    url = f"{WEBPIC}?nid={name}&imageType=FFRST&type=ffr"

    file = get_file(name, "png")
    if file.exists():
        # 检查文件的修改时间是否在一分钟以内
        minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(f"[SayuStock] image文件在{minutes}分钟内，直接返回文件数据。")
            try:
                img = Image.open(file)
                if size:
                    return img.resize(size)
                return img
            except UnidentifiedImageError:
                logger.warning(f"[SayuStock]{name}已存在文件读取失败, 尝试重新下载...")

    async with ClientSession() as sess:
        try:
            logger.info(f"[SayuStock]开始下载: {name} | 地址: {url}")
            async with sess.get(url) as res:
                if res.status == 200:
                    content = await res.read()
                    logger.info(f"[SayuStock]下载成功: {name}")
                else:
                    logger.warning(f"[SayuStock]{name}下载失败")
                    return Image.new("RGBA", (256, 256))
        except ClientConnectionError:
            logger.warning(f"[SayuStock]{name}下载失败")
            return Image.new("RGBA", (256, 256))

    async with aiofiles.open(str(file), "wb") as f:
        await f.write(content)
        stream = BytesIO(content)
        if size:
            return Image.open(stream).resize(size)
        else:
            return Image.open(stream)
