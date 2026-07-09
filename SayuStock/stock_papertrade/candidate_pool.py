"""模拟盘候选池构建 + 轮换。

两条职责：

1. ``build_candidate_pool``（程序化，供测试 / 潜在批处理用）——6 路合并 + 50 上限。
   6 路源（按优先级）：持仓 → 群友关注 → AI 内部池 → 板块龙头 → 大盘热股 → 新闻。

2. 轮换支持（ai_tools.papertrade_candidate_refresh 用）——蓝筹底仓 + 涨停/过热过滤
   + 决策反馈。这层解决"选完一批后永远只嚼同一批、不扩不减"的锚定：
   - 蓝筹底仓（``BLUECHIP_BASE``）保证池里始终有一批可交易的大盘股，而非全是
     超买微盘/北交所票（后者决策代理只会一直 hold → 账户永远空仓）。
   - ``_filter_overheated`` 在入池前用一次批量报价剔除涨停 / 过热标的（A 股涨停
     排队也难成交，追高风险大）。
   - ``post_decision_pool_update`` 让 sell 从池移除、buy 促成保留；hold **不**续期
     （旧实现 hold+强信号会不断续期 → 反而把标的钉死在池里，这里已修正）。
"""

import re
import random
from typing import Set, List, Tuple
from datetime import datetime, timedelta

from gsuid_core.logger import logger

from . import db

# 单路上限（build_candidate_pool 用）
SOURCE_CAPS = {
    "position": 20,
    "watchlist": 20,
    "agent_pool": 20,
    "sector": 15,  # 3 板块 × 5 只
    "hotmap": 10,
    "news": 10,
}
TOTAL_CAP = 50

# ── 轮换参数（papertrade_candidate_refresh 用）────────────────────
POOL_TARGET_SIZE = 10  # 每轮轮换后候选池目标只数（不含持仓/群友关注）
BASE_KEEP = 4  # 池中蓝筹底仓维持只数（其余名额留给动量标的）
ROTATE_OUT_PER_REFRESH = 3  # 每轮强制淘汰最旧的几只 auto 候选（保证新陈代谢）
AUTO_EXPIRE_HOURS = 6  # auto 扫描候选存活时长（原 3 天 → 日内轮换）
BASE_EXPIRE_HOURS = 24  # 蓝筹底仓存活时长（更稳定，隔日重新 seed）
# 入池前过滤：当日涨幅 ≥ 本板涨停幅度 × 此比例 视为涨停/过热，跳过
OVERHEATED_GAIN_RATIO = 0.8

# 蓝筹底仓池：跨行业大盘蓝筹 / 指数成分，作为候选池的质量地基。
# 每轮随机抽 BASE_KEEP 只补入，既保证有可交易标的又不长期锚定同一批。
BLUECHIP_BASE: Tuple[Tuple[str, str], ...] = (
    ("600519", "贵州茅台"),
    ("000858", "五粮液"),
    ("000568", "泸州老窖"),
    ("600036", "招商银行"),
    ("601398", "工商银行"),
    ("601166", "兴业银行"),
    ("601318", "中国平安"),
    ("600030", "中信证券"),
    ("600900", "长江电力"),
    ("300750", "宁德时代"),
    ("002594", "比亚迪"),
    ("601012", "隆基绿能"),
    ("000333", "美的集团"),
    ("000651", "格力电器"),
    ("600887", "伊利股份"),
    ("600276", "恒瑞医药"),
    ("603259", "药明康德"),
    ("002415", "海康威视"),
    ("002475", "立讯精密"),
    ("601899", "紫金矿业"),
    ("601088", "中国神华"),
    ("600028", "中国石化"),
    ("600941", "中国移动"),
    ("600309", "万华化学"),
)


def derive_secid(code: str) -> str:
    """6 位代码推 东财 secid：沪市(6 开头) → 1.xxx，其余(深/创/北) → 0.xxx。"""
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def _board_limit_pct(code: str) -> float:
    """按板块返回当日涨跌停幅度：科创/创业 ±20%，北交所 ±30%，其余主板 ±10%。"""
    if code.startswith(("300", "301", "688")):
        return 20.0
    if code.startswith(("4", "8", "920")):
        return 30.0
    return 10.0


def pick_base_slice(n: int) -> List[Tuple[str, str]]:
    """从蓝筹底仓随机抽 n 只 ``(code, name)``（不足 n 则全给）。"""
    n = max(0, min(n, len(BLUECHIP_BASE)))
    return random.sample(list(BLUECHIP_BASE), n) if n else []


async def filter_overheated(codes: List[str], *, gain_ratio: float = OVERHEATED_GAIN_RATIO) -> List[str]:
    """用一次批量报价剔除涨停/过热标的（当日涨幅 ≥ 本板涨停 × gain_ratio）。

    报价缺失（change_pct=None）的不误杀，交给决策代理深度分析时再判。
    """
    if not codes:
        return []
    from .quote_service import quote_service

    secids = [derive_secid(c) for c in codes]
    try:
        details = await quote_service.get_details_batch(secids)
    except Exception as e:
        logger.debug(f"[PaperTrade] filter_overheated 批量报价失败（不过滤）: {e}")
        return codes
    out: List[str] = []
    for c in codes:
        entry = details.get(derive_secid(c))
        chg = entry.change_pct if entry is not None else None
        if chg is not None and chg >= _board_limit_pct(c) * gain_ratio:
            continue
        out.append(c)
    return out


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
    """根据本次决策结果维护 agent_pool（决策 → 池 的反馈闭环）。

    decisions: [{action, code, name, secid, score, reason}, ...]

    语义：
      - ``buy``  → 加入/提权（priority=5，7 天过期），标记为在跟的建仓标的。
      - ``sell`` → 从池移除（已离场，不再每轮重复分析；要再进由扫描重新拉入）。
      - ``hold`` → **不动池**。旧实现 hold+强信号会不断 upsert 续期，等于把标的钉死
        在池里 → 每轮嚼同一批的锚定根因之一。现在 hold 一律不续期，让 auto 候选
        按 ``AUTO_EXPIRE_HOURS`` 自然老化、被轮换淘汰。
    """
    now = datetime.now()
    for d in decisions:
        action = d.get("action", "hold")
        code = d.get("code", "")
        if not code:
            continue
        secid = d.get("secid", "") or derive_secid(code)
        try:
            if action == "buy":
                await db.PaperAgentPoolRepo.upsert(
                    group_id,
                    bot_id,
                    stock_code=code,
                    stock_name=d.get("name", ""),
                    secid=secid,
                    reason=f"已建仓，关注后续 (score={d.get('score', 0):.2f})",
                    added_by="ai",
                    priority=5,
                    expires_at=now + timedelta(days=7),
                )
            elif action == "sell":
                await db.PaperAgentPoolRepo.remove(group_id, bot_id, code)
            # hold：不动池（见 docstring）
        except Exception as e:
            logger.debug(f"[PaperTrade] post_decision_pool_update {code} 失败: {e}")
