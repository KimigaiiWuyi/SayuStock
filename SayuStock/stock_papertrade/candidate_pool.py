"""AI 模拟盘候选池构建（6 路合并 + 50 只上限）。

6 路源（按优先级）：
P0: 当前持仓
P0: 群友关注列表
P1: AI 内部决策池
P2: 行业板块龙头（当日涨幅 TOP3 板块 × 成分股 TOP3）
P3: 大盘热股 TOP10
P3: 雪球新闻提及的股票

每路有独立上限，总上限 50，去重保序。
"""

import re
from typing import Set, List

from gsuid_core.logger import logger

from . import db

# 单路上限
SOURCE_CAPS = {
    "position": 20,
    "watchlist": 20,
    "agent_pool": 20,
    "sector": 15,  # 3 板块 × 5 只
    "hotmap": 10,
    "news": 10,
}
TOTAL_CAP = 50


# ============================================================
# 6 路源
# ============================================================
async def _from_position(group_id: str, bot_id: str) -> List[str]:
    return await db.PaperPositionRepo.list_codes(group_id, bot_id)


async def _from_watchlist(group_id: str, bot_id: str) -> List[str]:
    return await db.PaperWatchlistRepo.list_codes(group_id, bot_id)


async def _from_agent_pool(group_id: str, bot_id: str) -> List[str]:
    return await db.PaperAgentPoolRepo.list_codes(group_id, bot_id)


async def _from_sector_top_picks(top_sectors: int = 3, per_sector: int = 5) -> List[str]:
    """从 EASTMONEY_REQUESTER 拉行业板块 + 成分股，返回 [code, ...]

    拉 EASTMONEY_REQUESTER.get_menu(2) 行业板块菜单
    然后调 get_market_list 取每板块前 per_sector 只股票
    按板块涨幅排序取 top_sectors
    """
    try:
        from ..utils.eastmoney import EASTMONEY_REQUESTER

        menu = await EASTMONEY_REQUESTER.get_menu(2)  # 2 = 行业
        if not menu:
            return []
        # 拉每个板块行情，涨跌幅 f3 排序
        sector_codes = list(menu.values())[:20]  # 限制最多 20 个板块
        tasks_results = []
        for sec_code in sector_codes:
            try:
                market = await EASTMONEY_REQUESTER.get_market_list(sec_code, True, 1, per_sector)
                if not isinstance(market, dict):
                    continue
                diff = market.get("data", {}).get("diff", [])
                # 取 f3 涨跌幅
                f3 = diff[0].get("f3", 0) if diff else 0
                codes = [str(d.get("f12", "")) for d in diff[:per_sector] if d.get("f12")]
                if codes:
                    tasks_results.append((f3, codes))
            except Exception as e:
                logger.debug(f"[PaperTrade] 拉板块 {sec_code} 失败: {e}")
                continue
        tasks_results.sort(key=lambda x: -x[0])  # 涨幅降序
        out: List[str] = []
        for _, codes in tasks_results[:top_sectors]:
            out.extend(codes)
        return out[: SOURCE_CAPS["sector"]]
    except Exception as e:
        logger.warning(f"[PaperTrade] 板块轮动拉取失败: {e}")
        return []


async def _from_hotmap_top_n(n: int = 10) -> List[str]:
    """大盘热股 TOP N（从 stockhotmap 数据）"""
    try:
        from ..utils.eastmoney import EASTMONEY_REQUESTER

        data = await EASTMONEY_REQUESTER.get_hotmap()
        if not isinstance(data, dict):
            return []
        # 不同接口版本可能返回结构略不同
        items = data.get("data", []) if isinstance(data.get("data"), list) else []
        codes: List[str] = []
        for item in items:
            c = item.get("code") or item.get("f12") or item.get("stockCode")
            if c and len(c) == 6 and c.isdigit():
                codes.append(str(c))
            if len(codes) >= n:
                break
        return codes
    except Exception as e:
        logger.warning(f"[PaperTrade] 热股拉取失败: {e}")
        return []


# 常见 A 股 6 位代码模式
_TICKER_RE = re.compile(r"\b(\d{6})\b")

# 中文名 → 6 位代码的简易映射（仅作为兜底；实际通过 get_code_id 二次验证）
_KNOWN_NAMES = {
    "茅台": "600519",
    "贵州茅台": "600519",
    "五粮液": "000858",
    "宁德": "300750",
    "宁德时代": "300750",
    "平安": "601318",
    "中国平安": "601318",
    "招行": "600036",
    "招商银行": "600036",
    "中际旭创": "300308",
    "寒武纪": "688256",
    "海光": "688041",
    "海光信息": "688041",
}


async def _from_news_extract_tickers(limit: int = 50) -> List[str]:
    """从雪球 7x24 新闻文本里提取股票代码/名称 → 6 位代码。"""
    try:
        from ..utils.request import get_news

        news = await get_news()
        if isinstance(news, int):
            return []
        _, news_data = news
        items = news_data.get("items", [])[:limit]
        found: Set[str] = set()
        for it in items:
            text = it.get("text", "") or it.get("desc", "") or ""
            # 1) 提取 6 位数字
            for m in _TICKER_RE.findall(text):
                found.add(m)
            # 2) 提取已知中文名
            for name, code in _KNOWN_NAMES.items():
                if name in text:
                    found.add(code)
        return list(found)[: SOURCE_CAPS["news"]]
    except Exception as e:
        logger.warning(f"[PaperTrade] 新闻 ticker 提取失败: {e}")
        return []


# ============================================================
# 主入口
# ============================================================
async def build_candidate_pool(
    group_id: str,
    bot_id: str,
    *,
    include_sector: bool = True,
    include_hotmap: bool = True,
    include_news: bool = True,
) -> List[str]:
    """返回去重保序的股票代码列表（≤ 50 只）

    顺序：position → watchlist → agent_pool → sector → hotmap → news
    """
    pool: List[str] = []
    seen: Set[str] = set()

    def _add(codes: List[str], cap: int) -> None:
        n = 0
        for c in codes:
            if c in seen:
                continue
            if n >= cap:
                break
            if not (c and len(c) == 6 and c.isdigit()):
                continue
            seen.add(c)
            pool.append(c)
            n += 1

    # P0: 持仓
    try:
        pos_codes = await _from_position(group_id, bot_id)
        _add(pos_codes, SOURCE_CAPS["position"])
    except Exception as e:
        logger.debug(f"[PaperTrade] position 源失败: {e}")

    # P0: 群友关注
    try:
        wl_codes = await _from_watchlist(group_id, bot_id)
        _add(wl_codes, SOURCE_CAPS["watchlist"])
    except Exception as e:
        logger.debug(f"[PaperTrade] watchlist 源失败: {e}")

    # P1: AI 内部池
    try:
        ap_codes = await _from_agent_pool(group_id, bot_id)
        _add(ap_codes, SOURCE_CAPS["agent_pool"])
    except Exception as e:
        logger.debug(f"[PaperTrade] agent_pool 源失败: {e}")

    if include_sector:
        # P2: 板块龙头
        try:
            sec_codes = await _from_sector_top_picks()
            _add(sec_codes, SOURCE_CAPS["sector"])
        except Exception as e:
            logger.debug(f"[PaperTrade] sector 源失败: {e}")

    if include_hotmap:
        # P3: 热股
        try:
            hot_codes = await _from_hotmap_top_n()
            _add(hot_codes, SOURCE_CAPS["hotmap"])
        except Exception as e:
            logger.debug(f"[PaperTrade] hotmap 源失败: {e}")

    if include_news:
        # P3: 新闻
        try:
            news_codes = await _from_news_extract_tickers()
            _add(news_codes, SOURCE_CAPS["news"])
        except Exception as e:
            logger.debug(f"[PaperTrade] news 源失败: {e}")

    return pool[:TOTAL_CAP]


# ============================================================
# 决策后更新 AI 内部池
# ============================================================
async def post_decision_pool_update(
    group_id: str,
    bot_id: str,
    decisions: List[dict],
) -> None:
    """根据本次决策结果维护 agent_pool。

    decisions: [{action, code, name, secid, score, reason}, ...]
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    for d in decisions:
        action = d.get("action", "hold")
        code = d.get("code", "")
        if not code:
            continue
        try:
            if action == "buy":
                # 买入：加入池，7 天后过期
                await db.PaperAgentPoolRepo.upsert(
                    group_id,
                    bot_id,
                    stock_code=code,
                    stock_name=d.get("name", ""),
                    secid=d.get("secid", ""),
                    reason=f"已建仓，关注后续 (score={d.get('score', 0):.2f})",
                    added_by="ai",
                    priority=5,
                    expires_at=now + timedelta(days=7),
                )
            elif action == "sell":
                # 卖出：从池移除
                await db.PaperAgentPoolRepo.remove(group_id, bot_id, code)
            elif action == "hold" and d.get("score", 0) > 0.1:
                # hold 但信号不错，加入候选，3 天后过期
                await db.PaperAgentPoolRepo.upsert(
                    group_id,
                    bot_id,
                    stock_code=code,
                    stock_name=d.get("name", ""),
                    secid=d.get("secid", ""),
                    reason=f"信号强但未操作 (score={d.get('score', 0):.2f})",
                    added_by="ai",
                    priority=3,
                    expires_at=now + timedelta(days=3),
                )
        except Exception as e:
            logger.debug(f"[PaperTrade] post_decision_pool_update {code} 失败: {e}")
