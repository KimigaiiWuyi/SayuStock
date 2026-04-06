"""
SayuStock AI Tools 注册模块

为AI提供股票查询、分析等功能的工具集
直接引用现有函数，不增加复杂度
"""

from datetime import datetime

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools

from ..utils.utils import get_vix_name
from ..utils.get_OKX import get_all_crypto_price
from ..utils.request import get_news
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
    获取股票基本信息和实时行情

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
    获取大盘概览信息

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
    获取领涨板块

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
    获取基金持仓信息

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
