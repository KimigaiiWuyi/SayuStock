"""
SayuStock AI Tools 注册模块

为AI提供独立于触发器的高级查询工具。
已通过触发器 to_ai 覆盖的功能不再重复定义。
保留的工具提供更精确的独立能力或触发器未覆盖的功能。
"""

from datetime import datetime

from pydantic_ai import RunContext

from gsuid_core.logger import logger
from gsuid_core.segment import MessageSegment
from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.html_render import render_md_to_bytes

from ..utils.market import (
    KlinePeriod,
    get_market,
    is_market_error,
)
from ..utils.get_OKX import get_all_crypto_price
from ..utils.request import get_news
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

    market = get_market()
    INDEX_NAMES = ["上证指数", "深证成指", "创业板指", "沪深300", "中证500", "科创50"]
    indices: list[dict[str, object]] = []
    truncated: list[str] = []
    for name in INDEX_NAMES:
        q = await market.quote(name)
        if is_market_error(q):
            truncated.append(name)
            continue
        indices.append(
            {
                "name": name,
                "price": q.price,
                "avg_price": q.open if q.open is not None else q.price,
                "amount": q.amount if q.amount is not None else 0,
                "change_pct": q.change_pct,
            }
        )

    breadth = {"rise": 0, "fall": 0, "flat": 0, "limit_up": 0, "limit_down": 0}
    snap = await market.board("沪深A", limit=5000, sort_asc=False)
    if is_market_error(snap):
        truncated.append("breadth")
    else:
        for row in snap.rows:
            chg = row.change_pct if row.change_pct is not None else 0.0
            if chg > 0:
                breadth["rise"] += 1
            elif chg < 0:
                breadth["fall"] += 1
            else:
                breadth["flat"] += 1
            if chg >= 19.0 or chg >= 9.95:
                breadth["limit_up"] += 1
            if chg <= -19.0 or chg <= -9.95:
                breadth["limit_down"] += 1

    north_bound: float | None = None
    nb = await market.northbound()
    if is_market_error(nb):
        truncated.append("north_bound")
    else:
        north_bound = nb.sh_net_yi + nb.sz_net_yi

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

    ⚠️ ``change_pct`` 是**板块自身的聚合涨跌幅**（东财板块指数 f3），正常量级在
    ±10% 以内（A 股个股涨跌停 ±10%/±20%，但整板块聚合极少超过 ±10%）；它**不是**
    板块内领涨个股的涨幅。领涨个股单独放在 ``lead_stock`` / ``lead_stock_pct``
    字段——那才可能出现 +20%（创业板/科创板个股涨停）这类数字，不要把它当成板块涨幅。

    返回字段：
        - top_rise: 涨幅 TOP N 板块，每项含 name / code / change_pct（板块聚合涨跌幅）
          / up_count / down_count（成分股涨跌家数）/ lead_stock / lead_stock_code /
          lead_stock_pct（领涨股）/ top_stocks（成分股涨幅 TOP3 代码）
        - top_fall: 跌幅 TOP N 板块（结构同上）
        - hot_stocks: 热门个股 TOP 5（按成交额）

    使用建议：
        1. 找出 top_rise 第一的板块 → 看 top_stocks / lead_stock → 选股
        2. 找与持仓股所属板块 → 判断板块整体趋势，辅助 hold/sell 决策
    """
    import json as _json
    import asyncio

    market = get_market()
    board_kind = "行业板块" if sector_type == "industry" else "概念板块"
    out: dict[str, object] = {"sector_type": sector_type, "top_rise": [], "top_fall": [], "hot_stocks": []}

    def _board_rows(snap_rows: object, reverse: bool) -> list[dict[str, object]]:
        from ..utils.market.models import BoardRow

        rows_list: list[BoardRow] = list(snap_rows) if isinstance(snap_rows, (list, tuple)) else []
        rows_list = sorted(
            rows_list,
            key=lambda r: r.change_pct if r.change_pct is not None else 0.0,
            reverse=reverse,
        )[: max(top_n, 1)]
        result: list[dict[str, object]] = []
        for r in rows_list:
            extras = r.extras
            result.append(
                {
                    "name": r.name,
                    "code": r.code,
                    "change_pct": r.change_pct if r.change_pct is not None else 0,
                    "up_count": extras.up_count if extras is not None else None,
                    "down_count": extras.down_count if extras is not None else None,
                    "lead_stock": r.lead_name or "",
                    "lead_stock_code": extras.lead_code if extras is not None else "",
                    "lead_stock_pct": r.lead_change_pct,
                }
            )
        return result

    async def _top_codes(board_code: str) -> list[str]:
        snap = await market.board(board_code, limit=3, sort_asc=False)
        if is_market_error(snap):
            return []
        return [r.code for r in snap.rows[:3] if r.code]

    try:
        rise_snap = await market.board(board_kind, limit=max(top_n * 2, 10), sort_asc=False)
        fall_snap = await market.board(board_kind, limit=max(top_n * 2, 10), sort_asc=True)
        rise_rows: list[dict[str, object]] = []
        fall_rows: list[dict[str, object]] = []
        if not is_market_error(rise_snap):
            rise_rows = _board_rows(rise_snap.rows, reverse=True)
        if not is_market_error(fall_snap):
            fall_rows = _board_rows(fall_snap.rows, reverse=False)
        picked_codes = list({str(r["code"]) for r in (rise_rows + fall_rows) if r.get("code")})
        code_lists = await asyncio.gather(*[_top_codes(c) for c in picked_codes])
        code_to_top: dict[str, list[str]] = dict(zip(picked_codes, code_lists))
        for r in rise_rows + fall_rows:
            r["top_stocks"] = code_to_top.get(str(r.get("code", "")), [])
        out["top_rise"] = rise_rows
        out["top_fall"] = fall_rows
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        out["_error"] = str(e)

    try:
        hot = await market.board("沪深A", limit=5, sort_asc=False)
        if not is_market_error(hot):
            # 按成交额重排
            hot_sorted = sorted(
                hot.rows,
                key=lambda r: r.amount if r.amount is not None else 0.0,
                reverse=True,
            )[:5]
            out["hot_stocks"] = [
                {
                    "code": r.code,
                    "name": r.name,
                    "price": r.price if r.price is not None else 0,
                    "change_pct": r.change_pct if r.change_pct is not None else 0,
                    "amount_yi": (r.amount or 0) / 1e8,
                }
                for r in hot_sorted
            ]
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError):
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
    # 语义化：经 MarketDataPort/OKX adapter
    symbols = ["BTC", "ETH", "SOL", "DOGE", "BNB"]
    market = get_market()
    result = "【加密货币】\n"
    any_ok = False
    for name in symbols:
        q = await market.quote(name)
        if is_market_error(q):
            continue
        any_ok = True
        chg = f"{q.change_pct}%" if q.change_pct is not None else "N/A"
        result += f"{name}: ${q.price} ({chg})\n"
    if not any_ok:
        data = await get_all_crypto_price()
        if not data or not isinstance(data, dict):
            return "获取加密货币数据失败"
        for name, d in data.items():
            if not isinstance(d, dict):
                continue
            price = d["price"] if "price" in d else "N/A"
            change = d["change_pct"] if "change_pct" in d else "N/A"
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
    query_map = {
        "300": "300VIX",
        "50": "50VIX",
        "1000": "1000VIX",
        "kcb": "科创板VIX",
        "cyb": "创业板VIX",
    }
    query = query_map.get(vix_type.lower(), "300VIX")
    name_map = {
        "300": "沪深300 VIX",
        "50": "上证50 VIX",
        "1000": "中证1000 VIX",
        "kcb": "科创板 VIX",
        "cyb": "创业板 VIX",
    }
    q = await get_market().quote(query)
    if is_market_error(q):
        return q.message
    label = name_map.get(vix_type.lower(), query)
    chg = f"{q.change_pct}%" if q.change_pct is not None else "N/A"
    return f"【{label}】\n当前: {q.price}  涨跌: {chg}"


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

    series = await get_market().kline(
        code_id[0],
        KlinePeriod.D1,
        start=start_dt.date(),
        end=end_dt.date(),
    )
    if is_market_error(series):
        return series.message
    if not series.bars:
        return f"暂无{series.symbol.name}在{start_date_raw}~{end_date_raw}期间的K线数据"

    start_val, end_val = None, None
    for bar in series.bars:
        day = bar.ts.strftime("%Y-%m-%d")
        if start_dt.strftime("%Y-%m-%d") <= day <= end_dt.strftime("%Y-%m-%d"):
            if start_val is None:
                start_val = bar.open
            end_val = bar.close

    stock_name = series.symbol.name or stock_code
    if start_val is None or end_val is None:
        actual_dates = [b.ts.strftime("%Y-%m-%d") for b in series.bars]
        range_hint = f"{min(actual_dates)}~{max(actual_dates)}" if actual_dates else "无"
        return f"在指定时间范围内未找到{stock_name}的K线数据（实际数据范围: {range_hint}）"

    change_rate = ((end_val - start_val) / start_val) * 100
    return (
        f"【{stock_name} 涨跌幅分析】\n"
        f"时间范围: {start_date_raw} ~ {end_date_raw}\n"
        f"起始日期开盘价: {start_val:.3f}\n"
        f"结束日期收盘价: {end_val:.3f}\n"
        f"区间涨跌幅: {change_rate:+.2f}%"
    )


@ai_tools(category="common", capability_domain="股票研报出图")
async def send_stock_report_image(
    ctx: RunContext[ToolContext],
    markdown_content: str,
    title: str = "",
    max_width: int = 760,
) -> str:
    """把一整篇股票研报 / 复盘 / 看盘分析渲染成**一张图片**发出去（防群聊刷屏）。

    ⚠️ 正文数字应来自本插件工具（行情/财务/技术/估值等）；勿只用 web 摘要编全文。
    能力代理 ``stock_report_agent`` 写完研报后**必须**调本工具出图。

    长篇 + 多段落 + 表格 / 多小标题（个股研报、复盘、看盘、价位表等）**必须出图**：
    把完整 markdown 放进 ``markdown_content``。**不要**纯文字发正文——群聊按空行
    拆成多条刷屏。

    调用后最终文字只留极短点评，**禁止**再复述正文 / 表格 / 价位。

    Args:
        markdown_content: 完整研报的 markdown 原文（含 ``#`` 标题、``| |`` 表格、
            ``-`` 列表、``---`` 分隔线等，原样传入即可，无需转义）。
        title: 可选，在图片顶部再加一行大标题；不传则沿用正文里已有的标题。
        max_width: 图片最大宽度（像素），默认 760；表格列很多时可调大到 900。

    Returns:
        状态标记字符串。成功时提示"图片已发送"，你据此只补一句点评即可，勿再发正文。
    """
    md = (markdown_content or "").strip()
    if not md:
        return "❌ 研报内容为空，无法出图；请把完整 markdown 正文放进 markdown_content 再调用。"

    bot = ctx.deps.bot
    if bot is None:
        # 极少数无会话上下文的调用拿不到 Bot——退回让模型用文字精简回复
        return "❌ 当前上下文拿不到 Bot，无法发图；请直接用文字精简回复（勿发超长正文）。"

    if title.strip():
        md = f"# {title.strip()}\n\n{md}"

    try:
        image_bytes = await render_md_to_bytes(
            md=md,
            max_width=max_width,
            image_format="jpeg",
        )
    except Exception as e:
        logger.exception(f"🧠 [SayuStock] 研报渲染成图片失败: {e}")
        return f"❌ 研报渲染成图片失败：{e}；请直接用文字精简回复（勿发超长正文刷屏）。"

    await bot.send(MessageSegment.image(image_bytes))
    logger.info(f"🧠 [SayuStock] 研报已渲染为图片发送，图片长度: {len(image_bytes)} bytes")
    return (
        "✅ 研报已作为【一张图片】发送到群里。"
        "现在只需用角色口癖补一句简短点评即可，禁止再用文字复述研报正文 / 表格 / 价位。"
    )
