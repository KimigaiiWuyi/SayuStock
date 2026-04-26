"""
SayuStock AI Tools 注册模块

为AI提供股票查询、分析等功能的工具集
直接引用现有函数，不增加复杂度
"""

import asyncio
from datetime import datetime

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools

from ..utils.utils import get_vix_name
from ..utils.get_OKX import get_all_crypto_price
from ..utils.request import get_news
from ..utils.constant import bond, i_code, commodity
from ..utils.stock.request import get_gg, get_vix, get_mtdata
from ..utils.database.models import SsBind

# 直接引用现有函数
from ..utils.stock.request_utils import get_code_id


@ai_tools()
async def get_stock_basic(
    ctx: RunContext[ToolContext],
    stock_code: str,
) -> str:
    """
    获取股票的基本信息和当天实时行情

    Args:
        stock_code: 股票代码或名称，如"600000"、"贵州茅台"

    Returns:
        股票基本信息文本
    """
    code_id = await get_code_id(stock_code)
    if code_id is None:
        return "未找到该股票"

    data = await get_gg(code_id[0], "single-stock")
    if isinstance(data, str):
        return data

    d = data.get("data", {})
    return (
        f"{d.get('f58', 'N/A')}({code_id[0]})\n"
        f"价格: {d.get('f43', 'N/A')}  "
        f"涨跌: {d.get('f170', 'N/A')}%  "
        f"换手: {d.get('f168', 'N/A')}%"
    )


@ai_tools()
async def get_market_summary(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取当天A股的大盘概览信
    包括主要指数的涨跌幅、主要板块的涨跌幅、以及当天的市场量能

    Returns:
        主要指数行情文本
    """
    data = await get_mtdata("主要指数", pz=10)
    if isinstance(data, str):
        return data

    result = "【主要指数】\n"
    for item in data.get("data", {}).get("diff", []):
        name = item.get("f14", "N/A")
        price = item.get("f2", "N/A")
        change = item.get("f3", "N/A")
        result += f"{name}: {price} ({change}%)\n"

    return result


@ai_tools()
async def get_sector_leader(
    ctx: RunContext[ToolContext],
    sector_type: str = "行业板块",
) -> str:
    """
    获取当天中国A股的领涨板块

    Args:
        sector_type: "行业板块" 或 "概念板块"

    Returns:
        领涨板块列表
    """
    data = await get_mtdata(sector_type, po=1, pz=10)
    if isinstance(data, str):
        return data

    result = f"【{sector_type}领涨】\n"
    for item in data.get("data", {}).get("diff", []):
        name = item.get("f14", "N/A")
        change = item.get("f3", "N/A")
        top = item.get("f128", "N/A")
        result += f"{name}: {change}% (领涨: {top})\n"

    return result


@ai_tools()
async def get_fund_holdings(
    ctx: RunContext[ToolContext],
    fund_code: str,
) -> str:
    """
    获取某个基金的持仓(股票)信息

    比如获取沪深300的持仓信息

    Args:
        fund_code: 基金代码，如"000001"

    Returns:
        基金持仓文本
    """
    code_id = await get_code_id(fund_code)
    if code_id is None:
        return "基金代码有误"

    from ..utils.stock.request_utils import get_fund_pos_list

    fund_data = await get_fund_pos_list(code_id[0].split(".")[1])

    if not fund_data or not fund_data.get("Datas"):
        return "获取基金持仓失败"

    result = f"【{code_id[1]}】持仓:\n"
    for d in fund_data.get("Datas", [])[:5]:
        result += f"{d.get('ShareName', 'N/A')}: {d.get('ShareProportion', 'N/A')}%\n"

    return result


@ai_tools()
async def get_latest_news(
    ctx: RunContext[ToolContext],
    limit: int = 5,
) -> str:
    """
    获取最新财经新闻

    Args:
        limit: 新闻条数，默认5条

    Returns:
        新闻列表文本
    """
    news = await get_news()
    if isinstance(news, int):
        return f"获取新闻失败: {news}"

    _, news_data = news
    items = news_data.get("items", [])

    result = "【财经新闻】\n"
    for item in items[:limit]:
        ts = item.get("created_at", 0)
        dt = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        text = item.get("text", "")
        result += f"[{dt}] {text[:50]}...\n"

    return result


@ai_tools()
async def get_crypto_prices(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取加密货币价格

    Returns:
        主流加密货币行情
    """
    data = await get_all_crypto_price()
    if not data:
        return "获取加密货币数据失败"

    result = "【加密货币】\n"
    for name, d in data.items():
        price = d.get("f43", "N/A")
        change = d.get("f170", "N/A")
        result += f"{name}: ${price} ({change}%)\n"

    return result


@ai_tools()
async def get_vix_index(
    ctx: RunContext[ToolContext],
    vix_type: str = "300",
) -> str:
    """
    获取VIX波动率指数

    Args:
        vix_type: "300"、"50"、"1000"、"kcb"、"cyb"

    Returns:
        VIX指数数据
    """
    vix_name = f"vix{vix_type.lower()}"
    data = await get_vix(vix_name)

    if isinstance(data, str):
        return data

    d = data.get("data", {})
    name_map = {
        "vix300": "沪深300 VIX",
        "vix50": "上证50 VIX",
        "vixindex1000": "中证1000 VIX",
        "vixkcb": "科创板 VIX",
        "vixcyb": "创业板 VIX",
    }

    return f"【{name_map.get(vix_name, vix_name)}】\n当前: {d.get('f43', 'N/A')}  涨跌: {d.get('f170', 'N/A')}%"


@ai_tools()
async def search_stock(
    ctx: RunContext[ToolContext],
    query: str,
) -> str:
    """
    搜索股票代码

    Args:
        query: 股票名称或代码

    Returns:
        搜索结果
    """
    code_id = await get_code_id(query)
    if code_id is None:
        return f"未找到 '{query}'"

    return f"{code_id[1]}: {code_id[0]} ({code_id[2] if len(code_id) > 2 else '未知'})"


@ai_tools()
async def get_my_watchlist(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取用户自选列表

    获取用户的基金、股票持仓信息
    当用户请求他的股票、基金今天的涨跌信息时，调用此功能

    Returns:
        自选股票行情
    """
    ev = ctx.deps.ev if ctx.deps else None
    if ev is None:
        return "无法获取用户信息"

    uid_list = await SsBind.get_uid_list_by_game(ev.user_id, ev.bot_id)
    if not uid_list:
        return "暂无自选股票"

    result = "【我的自选】\n"
    for uid in uid_list[:10]:
        vix_name = get_vix_name(uid)
        if vix_name:
            data = await get_vix(vix_name)
        else:
            data = await get_gg(uid, "single-stock")

        if isinstance(data, str):
            continue

        d = data.get("data", {})
        name = d.get("f58", uid)
        change = d.get("f170", "N/A")
        result += f"{name}: {change}%\n"

    return result


# K线周期代码映射
KLINE_PERIOD_MAP = {
    "日线": "101",
    "日k": "101",
    "周线": "102",
    "周k": "102",
    "月线": "103",
    "月k": "103",
    "季线": "104",
    "季k": "104",
    "半年线": "105",
    "半年k": "105",
    "年线": "106",
    "年k": "106",
}


@ai_tools()
async def get_stock_kline(
    ctx: RunContext[ToolContext],
    stock_code: str,
    period: str = "日线",
) -> str:
    """
    获取股票K线数据

    获取指定股票的K线数据，支持日K、周K、月K、季K、半年K、年K等多种周期。
    AI可以使用此工具获取任意代码的K线数据进行技术分析。

    Args:
        stock_code: 股票代码或名称，如"600000"、"贵州茅台"
        period: K线周期，可选值:
            - "日线"或"日k" (默认): 日K线
            - "周线"或"周k": 周K线
            - "月线"或"月k": 月K线
            - "季线"或"季k": 季K线
            - "半年线"或"半年k": 半年K线
            - "年线"或"年k": 年K线

    Returns:
        K线数据文本，包含最近20条K线的日期、开盘、收盘、最高、最低、涨跌幅
    """
    code_id = await get_code_id(stock_code)
    if code_id is None:
        return f"未找到股票: {stock_code}"

    kline_code = KLINE_PERIOD_MAP.get(period, "101")
    data = await get_gg(code_id[0], f"single-stock-kline-{kline_code}")
    if isinstance(data, str):
        return data

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return f"暂无{data.get('data', {}).get('name', stock_code)}的K线数据"

    result = f"【{data.get('data', {}).get('name', stock_code)} {period}K线】\n"
    result += "日期        开盘    收盘    最高    最低    涨跌幅\n"

    # 返回最近20条
    for line in klines[-20:]:
        values = line.split(",")
        if len(values) >= 11:
            date = values[0]
            open_p = values[1]
            close_p = values[2]
            high = values[3]
            low = values[4]
            change = values[8]  # 涨跌幅
            result += f"{date} {open_p:>8} {close_p:>8} {high:>8} {low:>8} {change:>8}%\n"

    return result


@ai_tools()
async def get_stock_change_rate(
    ctx: RunContext[ToolContext],
    stock_code: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    获取股票任意时间范围内的涨跌幅

    对比个股在指定时间范围内的涨跌情况，可用于分析股票在特定时间段内的表现。
    日期格式支持YYYYMMDD或YYYY-MM-DD。

    Args:
        stock_code: 股票代码或名称，如"600000"、"贵州茅台"
        start_date: 开始日期，如"20240101"或"2024-01-01"（必填，需完整日期）
        end_date: 结束日期，如"20241231"或"2024-12-31"，默认为今天

    Returns:
        时间范围内的涨跌幅信息
    """
    code_id = await get_code_id(stock_code)
    if code_id is None:
        return f"未找到股票: {stock_code}"

    # 格式化日期 - 确保是8位数字格式
    start_date_raw = start_date.replace("-", "").replace("/", "")
    end_date_raw = end_date.replace("-", "").replace("/", "") if end_date else datetime.now().strftime("%Y%m%d")

    # 验证日期格式
    if len(start_date_raw) != 8:
        return "开始日期格式错误，请使用YYYYMMDD格式，如20240101"
    if len(end_date_raw) != 8:
        return "结束日期格式错误，请使用YYYYMMDD格式，如20241231"

    # 将日期转换为datetime对象用于计算
    try:
        start_dt = datetime.strptime(start_date_raw, "%Y%m%d")
        end_dt = datetime.strptime(end_date_raw, "%Y%m%d")
    except ValueError:
        return "日期格式错误，请使用YYYYMMDD格式，如20240101"

    if start_dt > end_dt:
        return "开始日期不能晚于结束日期"

    # 获取日K线数据，传入时间范围
    data = await get_gg(code_id[0], "single-stock-kline-101", start_time=start_dt, end_time=end_dt)
    if isinstance(data, str):
        return data

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        stock_name = data.get("data", {}).get("name", stock_code)
        return f"暂无{stock_name}在{start_date_raw}~{end_date_raw}期间的K线数据"

    # 解析日期并筛选在时间范围内的数据
    # 注意：K线数据的日期格式是 YYYY-MM-DD，需要转换比较
    start_val, end_val = None, None

    # 将 YYYYMMDD 格式转换为 YYYY-MM-DD 格式用于比较
    start_date_fmt = f"{start_date_raw[:4]}-{start_date_raw[4:6]}-{start_date_raw[6:8]}"
    end_date_fmt = f"{end_date_raw[:4]}-{end_date_raw[4:6]}-{end_date_raw[6:8]}"

    for line in klines:
        values = line.split(",")
        if len(values) >= 11:
            date = values[0]  # 格式是 YYYY-MM-DD
            if start_date_fmt <= date <= end_date_fmt:
                if start_val is None:
                    start_val = float(values[1])  # 开盘价
                end_val = float(values[2])  # 收盘价

    if start_val is None or end_val is None:
        stock_name = data.get("data", {}).get("name", stock_code)
        # 找到实际数据的日期范围
        actual_dates = [line.split(",")[0] for line in klines if len(line.split(",")) >= 11]
        return f"在指定时间范围({start_date_fmt}~{end_date_fmt})内未找到{stock_name}的K线数据（实际数据范围: {min(actual_dates)}~{max(actual_dates)}）"

    change_rate = ((end_val - start_val) / start_val) * 100
    stock_name = data.get("data", {}).get("name", stock_code)

    return (
        f"【{stock_name} 涨跌幅分析】\n"
        f"时间范围: {start_date_raw} ~ {end_date_raw}\n"
        f"起始日期开盘价: {start_val:.3f}\n"
        f"结束日期收盘价: {end_val:.3f}\n"
        f"区间涨跌幅: {change_rate:+.2f}%"
    )


@ai_tools()
async def get_commodity_prices(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取大宗商品价格

    获取国际大宗商品的实时价格和涨跌幅，包括黄金、白银、铜、原油等。
    数据来源：东方财富

    Returns:
        大宗商品价格列表
    """
    data = await get_mtdata("大宗商品")
    if isinstance(data, str):
        return f"获取大宗商品数据失败: {data}"

    result = "【大宗商品】\n"
    for name, code in commodity.items():
        if not code:
            continue
        # 从市场数据中查找
        diff_data = data.get("data", {}).get("diff", [])
        for item in diff_data:
            item_name = item.get("f14", "")
            if item_name and name in item_name:
                price = item.get("f2", "N/A")
                change = item.get("f3", "N/A")
                result += f"{item_name}: {price} ({change}%)\n"
                break

    if result == "【大宗商品】\n":
        return "暂无大宗商品数据"

    return result


@ai_tools()
async def get_bond_prices(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取国债收益率

    获取中国和美国国债的收益率数据，包括2年期、10年期、30年期等。
    数据来源：东方财富

    Returns:
        国债收益率列表
    """
    data = await get_mtdata("债券")
    if isinstance(data, str):
        return f"获取国债数据失败: {data}"

    result = "【国债收益率】\n"
    for name, code in bond.items():
        if not code:
            continue
        # 从市场数据中查找
        diff_data = data.get("data", {}).get("diff", [])
        for item in diff_data:
            item_name = item.get("f14", "")
            if item_name and (name in item_name or code in item.get("f12", "")):
                price = item.get("f2", "N/A")
                change = item.get("f3", "N/A")
                result += f"{item_name}: {price}% ({change}%)\n"
                break

    if result == "【国债收益率】\n":
        return "暂无国债数据"

    return result


@ai_tools()
async def get_global_stock_indexes(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取全球主要股市指数

    获取全球主要股市指数的实时行情，包括：
    - A股: 上证指数
    - 港股: 恒生指数
    - 日韩: 日经225、韩国KOSPI200
    - 美股: 纳斯达克、道琼斯、标普500
    - 欧洲: 欧洲斯托克50、英国富时100、法国CAC40、德国DAX30

    Returns:
        全球股市指数列表
    """
    data = await get_mtdata("国际市场")
    if isinstance(data, str):
        return f"获取国际市场数据失败: {data}"

    result = "【全球股市】\n"
    index_names = [
        "上证指数",
        "恒生指数",
        "日经225",
        "韩国KOSPI200",
        "纳斯达克",
        "道琼斯",
        "标普500",
        "欧洲斯托克50",
        "英国富时100",
        "法国CAC40",
        "德国DAX30",
    ]

    diff_data = data.get("data", {}).get("diff", [])
    for name in index_names:
        for item in diff_data:
            item_name = item.get("f14", "")
            if item_name and (name in item_name or name.split("0")[0] in item_name):
                price = item.get("f2", "N/A")
                change = item.get("f3", "N/A")
                result += f"{item_name}: {price} ({change}%)\n"
                break

    if result == "【全球股市】\n":
        return "暂无全球股市数据"

    return result


@ai_tools()
async def get_all_weather_data(
    ctx: RunContext[ToolContext],
) -> str:
    """
    获取全天候板块数据

    获取全天候策略关注的所有品种行情，包括：
    - 大宗商品（黄金、白银、铜、原油、螺纹钢等）
    - 国债（中国和美国国债收益率）
    - 外汇（美元兑离岸人民币、美元兑瑞郎等）
    - 加密货币（BTC、ETH等）
    - 全球股市指数

    Returns:
        全天候板块完整数据
    """
    # 并发获取所有数据
    results = await asyncio.gather(
        get_mtdata("国际市场"),
        get_mtdata("大宗商品"),
        get_mtdata("债券"),
        get_all_crypto_price(),
        return_exceptions=True,
    )

    def safe_data(result) -> dict:
        if isinstance(result, Exception):
            return {}
        return result

    intl_data = safe_data(results[0])
    commodity_data = safe_data(results[1])
    bond_data = safe_data(results[2])
    crypto_data = safe_data(results[3])

    result = "【全天候板块】\n\n"

    # 国际市场指数
    result += "【全球股市】\n"
    if intl_data.get("data", {}).get("diff"):
        for name, code in i_code.items():
            if not code:
                continue
            for item in intl_data["data"]["diff"]:
                if code.replace("i:", "") in item.get("f12", ""):
                    price = item.get("f2", "N/A")
                    change = item.get("f3", "N/A")
                    result += f"{name}: {price} ({change}%)\n"
                    break

    result += "\n【大宗商品】\n"
    if commodity_data.get("data", {}).get("diff"):
        for name, code in commodity.items():
            if not code:
                continue
            for item in commodity_data["data"]["diff"]:
                if code in item.get("f12", ""):
                    price = item.get("f2", "N/A")
                    change = item.get("f3", "N/A")
                    result += f"{name}: {price} ({change}%)\n"
                    break

    result += "\n【国债收益率】\n"
    if bond_data.get("data", {}).get("diff"):
        for name, code in bond.items():
            if not code:
                continue
            for item in bond_data["data"]["diff"]:
                if code in item.get("f12", ""):
                    price = item.get("f2", "N/A")
                    change = item.get("f3", "N/A")
                    result += f"{name}: {price}% ({change}%)\n"
                    break

    result += "\n【加密货币】\n"
    if crypto_data:
        for name, d in crypto_data.items():
            price = d.get("f43", "N/A")
            change = d.get("f170", "N/A")
            result += f"{name}: ${price} ({change}%)\n"

    return result if result != "【全天候板块】\n\n" else "获取全天候数据失败"
