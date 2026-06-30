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


# ============================================================
# 大盘概览 / 板块热力 —— 决策前的"扫描"工具
# 让 LLM 自主选股时先看大盘环境、再选行业、再选个股
# ============================================================
@ai_tools()
async def get_market_overview(
    ctx: RunContext[ToolContext],
) -> str:
    """获取 A 股大盘概览（核心宽基指数 + 成交额 + 涨跌家数 + 北向资金）。

    用于 AI 决策前的"扫描阶段"——LLM 应先调此工具看大盘环境（强势/弱势/震荡），
    再决定今天该不该出手、该偏向哪个方向。

    返回字段（json 字符串）：
        - indices: 上证指数 / 深证成指 / 创业板指 / 沪深300 / 中证500 / 科创50
                   的当前点位、涨跌幅、成交额
        - breadth: 上涨家数 / 下跌家数 / 平盘家数 / 涨停 / 跌停
        - north_bound: 北向资金净流入（亿元，正=外资流入）
        - limit_up_pct: 涨停占比（%）
        - _truncated: 是否有字段因接口失败被截断

    使用建议：
        1. 大盘跌 1%+ 或北向净流出 > 50 亿 → 整体防御，仓位 ≤ 30%
        2. 大盘涨 1%+ 且涨跌比 > 3:1 → 进攻，仓位可至 60~80%
        3. 震荡市 → 选股重于择时
    """
    import json as _json

    from ..utils.eastmoney import EASTMONEY_REQUESTER

    # 6 大宽基指数（东财 secid 格式：1=沪市, 0=深市）
    INDEX_SECIDS: list[tuple[str, str]] = [
        ("上证指数", "1.000001"),
        ("深证成指", "0.399001"),
        ("创业板指", "0.399006"),
        ("沪深300", "1.000300"),
        ("中证500", "1.000905"),
        ("科创50", "1.000688"),
    ]
    indices: list[dict[str, object]] = []
    truncated: list[str] = []
    for name, secid in INDEX_SECIDS:
        try:
            data = await EASTMONEY_REQUESTER.get_single_fig_data(secid)
            if isinstance(data, str) or not isinstance(data, list) or not data:
                truncated.append(name)
                continue
            last = data[-1]
            indices.append(
                {
                    "name": name,
                    "price": last.get("price", 0.0),
                    "avg_price": last.get("avg_price", 0.0),
                    "amount": last.get("amount", 0),  # 累计成交额（元）
                }
            )
        except Exception:
            truncated.append(name)
            continue

    # 沪深两市涨跌家数 + 涨停跌停（用 push2his 的 clist 拉沪深A股一次）
    breadth = {"rise": 0, "fall": 0, "flat": 0, "limit_up": 0, "limit_down": 0}
    try:
        # 东财 m:0 t:6 沪深A股 / m:1 t:2 沪深京A股 / fs 行情
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = [
            ("pn", "1"),
            ("pz", "5000"),
            ("po", "1"),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", "f3"),
            ("fs", "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2"),
            ("fields", "f1,f2,f3,f12,f14"),
        ]
        resp = await EASTMONEY_REQUESTER.stock_request(url, params=params)
        if isinstance(resp, dict) and "data" in resp and resp["data"]:
            diff = resp["data"].get("diff", [])
            for d in diff:
                chg = d.get("f3", 0) or 0
                if chg > 0:
                    breadth["rise"] += 1
                elif chg < 0:
                    breadth["fall"] += 1
                else:
                    breadth["flat"] += 1
                # 涨跌幅 ≥ 9.95% 算涨停（创业板/科创板 19.95%）
                if chg >= 19.0 or (9.5 <= chg < 20 and chg >= 9.95):
                    breadth["limit_up"] += 1
                if chg <= -19.0 or (-20 < chg <= -9.5):
                    breadth["limit_down"] += 1
    except Exception:
        truncated.append("breadth")

    # 北向资金（沪股通+深股通净买入）—— 用 push2.eastmoney.com 北向接口
    north_bound: float | None = None
    try:
        url2 = "https://push2.eastmoney.com/api/qt/kamt/get"
        params2 = [
            ("fields1", "f1,f2,f3,f4"),
            ("fields2", "f51,f52,f53,f54,f55,f56"),
            ("kamt", "1"),  # 1=沪深港通
            ("fs", "m:1+t:1,m:0+t:1"),  # 沪股通+深股通
        ]
        resp2 = await EASTMONEY_REQUESTER.stock_request(url2, params=params2)
        if isinstance(resp2, dict) and resp2.get("data"):
            d = resp2["data"]
            # 沪股通净买入 f55 / 深股通净买入 f56（万元）
            sh = d.get("f55", 0) or 0
            sz = d.get("f56", 0) or 0
            # 转为亿元
            north_bound = (sh + sz) / 1e4 / 1e4 * 1e4  # 万 → 元 → 亿
            # 实际东财字段：f55 / f56 已是"万元"
            north_bound = (sh + sz) / 10000.0  # 万元 → 亿元
    except Exception:
        truncated.append("north_bound")

    # 涨停占比
    total = breadth["rise"] + breadth["fall"] + breadth["flat"]
    limit_up_pct: float = breadth["limit_up"] / total * 100 if total > 0 else 0.0

    return _json.dumps(
        {
            "indices": indices,
            "breadth": breadth,
            "total_count": total,
            "north_bound_yi": north_bound,
            "limit_up_pct": limit_up_pct,
            "_truncated": truncated,
        },
        ensure_ascii=False,
        default=str,
    )


@ai_tools()
async def get_sector_heatmap(
    ctx: RunContext[ToolContext],
    top_n: int = 10,
    sector_type: str = "industry",
) -> str:
    """获取行业/概念板块涨跌幅排行（板块热力图）。

    Args:
        top_n: 返回前 N 个板块（默认 10）
        sector_type: ``industry``（行业板块）/ ``concept``（概念板块）

    用于 AI 决策前确定"今天哪个板块最强 / 最弱"，
    便于从强势板块中选股，或避开弱势板块。

    返回字段：
        - top_rise: 涨幅 TOP N 板块，附成分股 TOP3 代码
        - top_fall: 跌幅 TOP N 板块
        - hot_stocks: 热门个股 TOP 5（按成交额）
        - lead_sectors: 连续 3 日强势板块（占位，实际需额外拉历史）

    使用建议：
        1. 找出 top_rise 第一的板块 → 调 search_stock 拿成分股 → 选股
        2. 找与持仓股所属板块 → 判断板块整体趋势，辅助 hold/sell 决策
    """
    import json as _json

    from ..utils.eastmoney import EASTMONEY_REQUESTER

    mode: int = 2 if sector_type == "industry" else 3
    out: dict[str, object] = {"sector_type": sector_type, "top_rise": [], "top_fall": [], "hot_stocks": []}
    try:
        # 拿板块菜单
        menu = await EASTMONEY_REQUESTER.get_menu(mode)
        if not menu:
            return _json.dumps(out, ensure_ascii=False)
        # 限制拉取板块数（避免太慢）
        sector_codes = list(menu.values())[:30]
        # 并发拉行情
        results: list[tuple[str, str, dict[str, object]]] = []
        for sec_code, sec_name in zip(sector_codes, list(menu.keys())[:30]):
            try:
                m = await EASTMONEY_REQUESTER.get_market_list(sec_code, False, 1, 5)
                if not isinstance(m, dict):
                    continue
                diff = m.get("data", {}).get("diff", [])
                f3 = diff[0].get("f3", 0) if diff else 0
                top_codes = [str(d.get("f12", "")) for d in diff[:3] if d.get("f12")]
                results.append((sec_name, sec_code, {"f3": f3, "top_codes": top_codes}))
            except Exception:
                continue
        # 按涨跌幅排序
        results.sort(key=lambda x: -(x[2].get("f3", 0) or 0))
        out["top_rise"] = [
            {"name": n, "code": c, "change_pct": v.get("f3"), "top_stocks": v.get("top_codes")}
            for n, c, v in results[:top_n]
        ]
        out["top_fall"] = [
            {"name": n, "code": c, "change_pct": v.get("f3"), "top_stocks": v.get("top_codes")}
            for n, c, v in sorted(results, key=lambda x: x[2].get("f3", 0) or 0)[:top_n]
        ]
    except Exception as e:
        out["_error"] = str(e)

    # 热门个股 TOP 5（成交额）
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = [
            ("pn", "1"),
            ("pz", "5"),
            ("po", "1"),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", "f6"),  # 成交额
            ("fs", "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2"),
            ("fields", "f12,f14,f2,f3,f6"),
        ]
        resp = await EASTMONEY_REQUESTER.stock_request(url, params=params)
        if isinstance(resp, dict) and resp.get("data"):
            diff = resp["data"].get("diff", [])
            out["hot_stocks"] = [
                {
                    "code": d.get("f12", ""),
                    "name": d.get("f14", ""),
                    "price": d.get("f2", 0),
                    "change_pct": d.get("f3", 0),
                    "amount_yi": (d.get("f6", 0) or 0) / 1e8,  # 成交额（元）→ 亿元
                }
                for d in diff
            ]
    except Exception:
        pass

    return _json.dumps(out, ensure_ascii=False, default=str)


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
