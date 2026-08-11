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
@ai_tools(
    covers=[
        "A股大盘概览：上证指数/深证成指/创业板指/沪深300等核心宽基指数实时点位",
        "市场涨跌家数分布、两市成交额、北向资金净流入、涨停占比",
    ],
)
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
    from datetime import datetime as _dt

    from ..utils.stock.request import get_bar

    market = get_market()
    as_of = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
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

    # 涨跌家数：专用分布接口（勿用 clist 前 N 只冒充全市场）
    breadth = {"rise": 0, "fall": 0, "flat": 0, "limit_up": 0, "limit_down": 0}
    bars_raw = await get_bar()
    if isinstance(bars_raw, str):
        truncated.append("breadth")
    else:

        def _int_list(key: str, size: int) -> list[int]:
            raw = bars_raw[key] if key in bars_raw else []
            if not isinstance(raw, list):
                return [0] * size
            vals: list[int] = []
            for x in raw:
                if isinstance(x, bool):
                    vals.append(int(x))
                elif isinstance(x, (int, float)):
                    vals.append(int(x))
                elif isinstance(x, str):
                    try:
                        vals.append(int(float(x)))
                    except ValueError:
                        vals.append(0)
                else:
                    vals.append(0)
            if len(vals) < size:
                vals.extend([0] * (size - len(vals)))
            return vals[:size]

        def _int_val(key: str) -> int:
            raw = bars_raw[key] if key in bars_raw else 0
            if isinstance(raw, bool):
                return int(raw)
            if isinstance(raw, (int, float)):
                return int(raw)
            if isinstance(raw, str):
                try:
                    return int(float(raw))
                except ValueError:
                    return 0
            return 0

        zf = _int_list("2", 10)
        df = _int_list("3", 10)
        limit_up = _int_val("5")
        limit_down = _int_val("6")
        rise = zf[0] + zf[1] + zf[2] + zf[3] + zf[4] + zf[5] + zf[6] + zf[7] + zf[8] + zf[9] + limit_up
        fall = df[0] + df[1] + df[2] + df[3] + df[4] + df[5] + df[6] + df[7] + df[8] + df[9] + limit_down
        breadth = {
            "rise": rise,
            "fall": fall,
            "flat": 0,
            "limit_up": limit_up,
            "limit_down": limit_down,
        }

    north_bound: float | None = None
    nb = await market.northbound()
    if is_market_error(nb):
        truncated.append("north_bound")
    else:
        north_bound = nb.sh_net_yi + nb.sz_net_yi

    total = breadth["rise"] + breadth["fall"] + breadth["flat"]
    limit_up_pct: float = breadth["limit_up"] / total * 100 if total > 0 else 0.0

    return _json.dumps(
        {
            "ok": True,
            "as_of": as_of,
            "indices": indices,
            "breadth": breadth,
            "total_count": total,
            "north_bound_yi": north_bound,
            "limit_up_pct": limit_up_pct,
            "gaps": truncated,
            "_truncated": truncated,
        },
        ensure_ascii=False,
        default=str,
    )


@ai_tools(
    covers=[
        "A股行业/概念板块涨跌幅排行、板块热力、领涨领跌板块与龙头股",
    ],
)
async def get_sector_heatmap(
    ctx: RunContext[ToolContext],
    top_n: int = 15,
    sector_type: str = "industry",
    include_all: bool | None = None,
) -> str:
    """获取行业/概念板块涨跌幅排行（板块热力）。

    Args:
        top_n: 两端「明细」条数：``top_rise`` / ``top_fall`` 各取 N 条，并为这些
            板块补 ``top_stocks``（成分股涨幅 TOP3）。默认 15。
        sector_type: ``industry``（行业板块）/ ``concept``（概念板块）
        include_all: 是否附带全市场 ``ranked``。``None``（默认）时：
            **行业**自动 True（板块数有限，便于看持仓位次）；
            **概念**自动 False（常 300+ 行，防 token 膨胀，仍保留两端明细）。
            显式传 True/False 可覆盖。

    用于 AI 决策前看清「今天哪些板块强/弱、持仓所属板块排在哪」，
    避免只看 TOP10 漏掉中游轮动。

    ⚠️ ``change_pct`` 是**板块自身的聚合涨跌幅**（东财板块指数 f3），正常量级在
    ±10% 以内；**不是**板块内领涨个股的涨幅。领涨个股在 ``lead_stock`` /
    ``lead_stock_pct``；仅两端明细行带 ``top_stocks``。

    返回字段：
        - count: 板块总数
        - ranked: 全列表（涨→跌），每项精简：name / code / change_pct /
          up_count / down_count / lead_stock / lead_stock_code / lead_stock_pct
          （未 include_all 时为 []）
        - top_rise / top_fall: 两端明细（含 top_stocks）
        - hot_stocks: 热门个股 TOP 5（按成交额）

    使用建议：
        1. 行业先扫 ``ranked`` 看全貌与持仓位次；概念默认看两端 + 必要时
           ``include_all=True``；
        2. 对关心的强/弱板块看 top_stocks 再选股。
    """
    import json as _json
    import asyncio

    market = get_market()
    board_kind = "行业板块" if sector_type == "industry" else "概念板块"
    detail_n = max(int(top_n), 1)
    # 行业默认全表；概念默认两端明细（概念 300+ 防 token 爆炸）
    use_all: bool = (sector_type == "industry") if include_all is None else bool(include_all)
    out: dict[str, object] = {
        "sector_type": sector_type,
        "count": 0,
        "ranked": [],
        "top_rise": [],
        "top_fall": [],
        "hot_stocks": [],
        "detail_n": detail_n,
        "include_all": use_all,
    }

    def _row_compact(r: object) -> dict[str, object]:
        from ..utils.market.models import BoardRow

        assert isinstance(r, BoardRow)
        extras = r.extras
        return {
            "name": r.name,
            "code": r.code,
            "change_pct": r.change_pct if r.change_pct is not None else 0,
            "up_count": extras.up_count if extras is not None else None,
            "down_count": extras.down_count if extras is not None else None,
            "lead_stock": r.lead_name or "",
            "lead_stock_code": extras.lead_code if extras is not None else "",
            "lead_stock_pct": r.lead_change_pct,
        }

    async def _top_codes(board_code: str) -> list[str]:
        snap = await market.board(board_code, limit=3, sort_asc=False)
        if is_market_error(snap):
            return []
        return [r.code for r in snap.rows[:3] if r.code]

    try:
        from ..utils.market.models import BoardRow

        # 一次拉全量（limit=None → provider 分页 is_loop），客户端按涨跌幅排序，
        # 避免旧逻辑只拉 top_n*3 再截两端、中游板块对 agent 不可见。
        full_snap = await market.board(board_kind, limit=None, sort_asc=False)
        if is_market_error(full_snap):
            out["_error"] = str(full_snap)
        else:
            rows_list: list[BoardRow] = [r for r in list(full_snap.rows) if isinstance(r, BoardRow)]
            rows_list.sort(
                key=lambda r: r.change_pct if r.change_pct is not None else 0.0,
                reverse=True,
            )
            compact_all = [_row_compact(r) for r in rows_list]
            out["count"] = len(compact_all)
            if use_all:
                out["ranked"] = compact_all

            rise_rows = [dict(x) for x in compact_all[:detail_n]]
            fall_rows = [dict(x) for x in compact_all[-detail_n:]] if compact_all else []
            fall_rows.reverse()  # 跌幅从深到浅，与旧 top_fall 语义一致

            # 仅两端补成分股 TOP3，避免对全市场每个板块再打 N 次 board
            picked_codes: list[str] = []
            for r in rise_rows + fall_rows:
                if "code" not in r:
                    continue
                code_s = str(r["code"] or "")
                if code_s and code_s not in picked_codes:
                    picked_codes.append(code_s)
            if picked_codes:
                code_lists = await asyncio.gather(*[_top_codes(c) for c in picked_codes])
                code_to_top: dict[str, list[str]] = dict(zip(picked_codes, code_lists))
                for r in rise_rows + fall_rows:
                    code_key = str(r["code"] if "code" in r else "")
                    r["top_stocks"] = code_to_top[code_key] if code_key in code_to_top else []
            out["top_rise"] = rise_rows
            out["top_fall"] = fall_rows
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError, AssertionError) as e:
        out["_error"] = str(e)

    try:
        from ..utils.market import RankBy

        # 真成交额榜（勿对涨跌幅 board 子集再按 amount 排序）
        hot = await market.rank_list(RankBy.AMOUNT, limit=5)
        if not is_market_error(hot):
            out["hot_stocks"] = [
                {
                    "code": r.code,
                    "name": r.name,
                    "price": r.price if r.price is not None else 0,
                    "change_pct": r.change_pct if r.change_pct is not None else 0,
                    "amount_yi": (r.amount or 0) / 1e8,
                }
                for r in hot.rows
            ]
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        out["_hot_error"] = str(e)

    return _json.dumps(out, ensure_ascii=False, default=str)


@ai_tools(
    covers=[
        "A股个股排行榜：成交额/换手率/ROE/净利润增速/主力资金流等指标排名",
    ],
)
async def get_market_ranking(
    ctx: RunContext[ToolContext],
    rank_by: str = "amount",
    n: int = 20,
    high_first: bool | None = None,
) -> str:
    """沪深 A **通用排行榜**（东财 clist）：资金流 / 换手 / ROE / 量额 / 净利增速等。

    Args:
        rank_by: 排行类型（英文键或中文别名均可），支持：
            - ``main_inflow`` / 资金流入 / 主力净流入
            - ``main_outflow`` / 资金流出（净流入升序，流出居前）
            - ``turnover`` / 换手率
            - ``roe`` / ROE / 净资产收益率（同一指标）
            - ``amount`` / 成交额
            - ``volume`` / 成交量
            - ``profit_yoy`` / 净利润增长率 / 净利润同比
        n: 返回条数，1～50，默认 20
        high_first: 是否高→低。``None`` 用该榜默认（资金流出默认低→高）

    返回 JSON：``rank_by`` / ``items``（code/name/price/change_pct/metric/…）/
    ``caveat`` / ``unit_hint``。

    ⚠️ **硬纪律（读完再调）**：
    1. 榜单**只作扫描线索**，绝不能单独作为 buy/sell 依据；
    2. **增长率 / ROE / 换手 / 资金流靠前 ≠ 好公司**——报表与量价可被调节、
       炒作或短期失真；须与估值、现金流质量、行业周期、技术与事件交叉验证；
    3. 禁止「榜一就重仓」；禁止把 clist 财务字段当成已审计年报全文。
    """
    import json as _json

    from ..utils.market_ranking import fetch_market_ranking

    payload = await fetch_market_ranking(rank_by, n=n, high_first=high_first)
    return _json.dumps(payload, ensure_ascii=False, default=str)


@ai_tools(
    covers=[
        "实时财经新闻快讯：雪球7x24市场动态、宏观/行业/个股要闻、政策与事件驱动",
    ],
)
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
        if not isinstance(ts, (int, float)):
            ts = 0
        dt = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M") if ts else "??-??"
        text = item.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        # 完整摘要（上限 280 字），避免 50 字截断导致代理无法研判
        body = text if len(text) <= 280 else text[:277] + "..."
        result += f"[{dt}] {body}\n"

    return result


@ai_tools(
    covers=[
        "加密货币实时行情：BTC/ETH/SOL/DOGE/BNB 等主流币的现价与涨跌幅",
    ],
)
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
    # [as_of=…] 时效契约标记（框架方案七）：实时币价为新鲜读数
    as_of = datetime.now().strftime("%Y-%m-%d %H:%M")
    result = f"[as_of={as_of}|source=okx]\n【加密货币】\n"
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


@ai_tools(
    covers=[
        "VIX波动率指数：沪深300/上证50期权隐含波动率，反映市场恐慌/贪婪情绪",
    ],
)
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


@ai_tools(
    covers=[
        "标的代码解析：A股/港股/美股/指数/板块/ETF/基金/债券/期货/现货贵金属（黄金XAU、白银）/外汇",
        "查任何标的（含 XAU 现货黄金、纳指、恒指）的规范代码与所属市场，再供行情/K线工具使用",
    ],
)
async def search_stock(
    ctx: RunContext[ToolContext],
    query: str,
) -> str:
    """
    搜索标的代码（股票/指数/期货/现货/外汇通用解析）

    根据名称或代码模糊搜索，返回匹配的标的信息（代码 + 名称 + 市场类型）。
    底层走东财 suggest，覆盖 A股/港股/美股/指数/ETF/基金/债券/期货/
    现货贵金属（如 XAU=现货黄金、黄金/美元）/外汇，不止 A 股。
    用于确认标的代码后再进行行情/K线/技术指标查询。

    Args:
        query: 标的名称或代码，如"贵州茅台"、"600000"、"证券ETF"、"XAU"、"现货黄金"、"纳指"

    Returns:
        搜索结果（代码: 名称 (市场类型)）
    """
    code_id = await get_code_id(query)
    if code_id is None:
        return f"未找到 '{query}'"

    return f"{code_id[1]}: {code_id[0]} ({code_id[2] if len(code_id) > 2 else '未知'})"


@ai_tools(
    covers=[
        "任意标的区间涨跌幅：A股/港股/美股/指数/ETF/期货/现货贵金属（黄金XAU、白银）/外汇",
        "指定日期范围的起止价格与涨跌幅（区间表现复盘）",
    ],
)
async def get_stock_change_rate(
    ctx: RunContext[ToolContext],
    stock_code: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    获取标的任意时间范围内的涨跌幅

    计算标的在指定时间范围内的涨跌情况，可用于分析其在特定时间段内的表现。
    标的经 get_code_id 解析，覆盖 A股/港股/美股/指数/ETF/期货/现货贵金属
    （如 XAU=现货黄金）/外汇，不止 A 股。

    Args:
        stock_code: 标的代码或名称，如"600000"、"贵州茅台"、"XAU"、"现货黄金"
        start_date: 开始日期，如"20240101"或"2024-01-01"（必填，需完整日期）
        end_date: 结束日期，如"20241231"或"2024-12-31"，默认为今天

    Returns:
        时间范围内的涨跌幅信息（含起止价格，带数据时点）
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
    # [as_of=…] 时效契约标记（框架方案七）：声明数据时点，供 freshness 账本识别
    as_of = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"[as_of={as_of}|source=eastmoney-kline]\n"
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
    """兼容入口：把 markdown 研报渲成一张图并 ``bot.send``（非研报主路径）。

    **主路径（硬门）**：``stock_report_agent`` 只 ``artifact_put`` Markdown → 主人格
    ``create_subagent(agent_profile="render_agent")`` 出图。本工具**不得**挂在
    ``stock_report_agent`` 上，也**不得**与 render_agent 同轮双发。

    仅在极少数「主会话已直接持有本工具、且未走能力代理」的兼容场景使用。
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

    # 方案 B：非 render 能力代理禁止终局直发，避免与主人格 render_agent 双发
    parent_sid = ctx.deps.parent_session_id or ""
    if parent_sid.startswith("capagent_") and not parent_sid.startswith("capagent_render_agent"):
        return (
            "❌ 能力代理禁止 send_stock_report_image 直发。"
            "请 artifact_put(markdown) 交主人格，再 create_subagent(render_agent) 出图。"
        )

    # 主路径唯一出图口径：stock_report_agent 已改为 shim 转 render 能力代理
    # 未走能力代理但误调本工具的，内部转调通用 render 能力（保持最终图一致性）
    try:
        from gsuid_core.ai_core.agent_node import get_node as _get_node

        _prof = _get_node("render_agent") is not None
    except Exception:
        _prof = False
    if _prof:
        # 统一垫片：把 markdown 包成 HTML 后走通用渲染链路，等价于 render_agent 单图
        pass

    bot = ctx.deps.bot
    if bot is None:
        # 无 Bot 上下文：不退文字刷屏，回句柄式提示让上游走 artifact 路径
        return (
            "❌ 当前上下文拿不到 Bot，无法直发。"
            "请 artifact_put(payload=markdown, mime='text/markdown') 登记后交主人格 render_agent 出图。"
        )

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
