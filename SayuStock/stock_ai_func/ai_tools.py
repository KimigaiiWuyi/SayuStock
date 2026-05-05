"""
SayuStock AI Tools 注册模块

为AI提供独立于触发器的高级查询工具。
已通过触发器 to_ai 覆盖的功能不再重复定义。
保留的工具提供更精确的独立能力或触发器未覆盖的功能。
"""

from datetime import datetime

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools

from ..utils.get_OKX import get_all_crypto_price
from ..utils.request import get_news
from ..utils.stock.request import get_gg, get_vix
from ..utils.stock.request_utils import get_code_id


@ai_tools()
async def get_latest_news(
    ctx: RunContext[ToolContext],
    limit: int = 5,
) -> str:
    """
    获取最新财经新闻

    获取雪球7x24小时最新财经新闻，用于了解市场动态和重要资讯。
    注意：订阅/取消订阅新闻请使用触发器命令。

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

    获取主流加密货币（BTC、ETH、SOL等）的实时价格和涨跌幅。
    数据来源：OKX交易所。

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

    获取中国市场的VIX波动率指数，反映市场恐慌/贪婪情绪。

    Args:
        vix_type: VIX类型，可选值:
            - "300": 沪深300 VIX（默认）
            - "50": 上证50 VIX
            - "1000": 中证1000 VIX
            - "kcb": 科创板 VIX
            - "cyb": 创业板 VIX

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

    根据股票名称或代码模糊搜索，返回匹配的股票信息。
    用于确认股票代码后再进行其他查询。

    Args:
        query: 股票名称或代码，如"贵州茅台"、"600000"、"证券ETF"

    Returns:
        搜索结果
    """
    code_id = await get_code_id(query)
    if code_id is None:
        return f"未找到 '{query}'"

    return f"{code_id[1]}: {code_id[0]} ({code_id[2] if len(code_id) > 2 else '未知'})"


@ai_tools()
async def get_stock_change_rate(
    ctx: RunContext[ToolContext],
    stock_code: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    获取股票任意时间范围内的涨跌幅

    计算个股在指定时间范围内的涨跌情况，可用于分析股票在特定时间段内的表现。
    比触发器的"对比个股"更灵活，支持精确日期范围。

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
        actual_dates = [line.split(",")[0] for line in klines if len(line.split(",")) >= 11]
        return (
            f"在指定时间范围({start_date_fmt}~{end_date_fmt})"
            f"内未找到{stock_name}的K线数据"
            f"（实际数据范围: {min(actual_dates)}~{max(actual_dates)}）"
        )

    change_rate = ((end_val - start_val) / start_val) * 100
    stock_name = data.get("data", {}).get("name", stock_code)

    return (
        f"【{stock_name} 涨跌幅分析】\n"
        f"时间范围: {start_date_raw} ~ {end_date_raw}\n"
        f"起始日期开盘价: {start_val:.3f}\n"
        f"结束日期收盘价: {end_val:.3f}\n"
        f"区间涨跌幅: {change_rate:+.2f}%"
    )
