"""模拟盘候选池构建 + 轮换。

两条职责：

1. ``build_candidate_pool``（程序化，供测试 / 潜在批处理用）——多路合并 + 50 上限。
   优先级：持仓 → 群友关注 → AI 内部池 → 行业龙头 → 概念龙头 → 热股 →
   涨幅榜 → 跌幅榜（超跌） → 成交额龙头 → 新闻。

2. 轮换支持（ai_tools.papertrade_candidate_refresh 用）——蓝筹底仓 + 多源动量 +
   涨停/过热过滤 + 决策反馈。这层解决"选完一批后永远只嚼同一批、不扩不减"的锚定：
   - 蓝筹底仓（``BLUECHIP_BASE``）保证池里始终有一批可交易的大盘股，而非全是
     超买微盘/北交所票（后者决策代理只会一直 hold → 账户永远空仓）。
   - 动量源**轮询交织**入池，避免「行业领涨一路占满」导致来源实质单一。
   - ``filter_overheated`` 在入池前用一次批量报价剔除涨停 / 过热标的（A 股涨停
     排队也难成交，追高风险大）。
   - ``post_decision_pool_update`` 让 sell 从池移除、buy 促成保留；hold **不**续期
     （旧实现 hold+强信号会不断续期 → 反而把标的钉死在池里，这里已修正）。
"""

import re
import random
from typing import Set, Dict, List, Tuple, Iterable, TypedDict
from datetime import datetime, timedelta

from gsuid_core.logger import logger

from . import db

# 单路上限（build_candidate_pool 用）
SOURCE_CAPS = {
    "position": 20,
    "watchlist": 20,
    "agent_pool": 20,
    "sector": 12,  # 行业 TOP 板块成分
    "concept": 9,  # 概念 TOP 板块成分
    "hotmap": 10,
    "gainer": 8,  # 沪深 A 涨幅榜
    "laggard": 6,  # 沪深 A 跌幅榜（超跌/反转候选）
    "amount": 8,  # 成交额龙头
    "quality": 8,  # 高 ROE / 财务质量筛选
    "news": 10,
}
TOTAL_CAP = 50

# ── 轮换参数（papertrade_candidate_refresh 用）────────────────────
# 目标约 16：底仓 ~5 + 动量 ~11，足够覆盖多风格又不过度拖慢每 30 分钟决策。
POOL_TARGET_SIZE = 16  # 每轮轮换后候选池目标只数（不含持仓/群友关注）
BASE_KEEP = 5  # 池中蓝筹底仓维持只数（其余名额留给多源动量）
ROTATE_OUT_PER_REFRESH = 4  # 每轮强制淘汰最旧的几只 auto 候选（保证新陈代谢）
AUTO_EXPIRE_HOURS = 6  # auto 扫描候选存活时长（原 3 天 → 日内轮换）
BASE_EXPIRE_HOURS = 24  # 蓝筹底仓存活时长（更稳定，隔日重新 seed）
# 入池前过滤：当日涨幅 ≥ 本板涨停幅度 × 此比例 视为涨停/过热，跳过
OVERHEATED_GAIN_RATIO = 0.8

# 财务质量筛选（高 ROE 池）——经 MarketDataPort.rank_list，无硬编码票池
QUALITY_ROE_MIN = 12.0  # 非银行 ROE 门槛（%）
QUALITY_ROE_MIN_BANK = 8.0  # 名称含「银行」时的 ROE 门槛（%）
QUALITY_DEBT_MAX_NONBANK = 75.0  # 非银行负债率上限 %
QUALITY_CACHE_HOURS = 6  # 进程内结果 TTL（避免每 30 分钟重打榜）
# ROE 榜拉取宽度（再本地过滤）；须 ≤ eastmoney rank_list 单页上限 100
QUALITY_RANK_LIMIT = 80

# 动量源写入顺序（refresh 时轮询交织，勿改成「某一路先塞满」）
MOMENTUM_SOURCE_ORDER: Tuple[str, ...] = (
    "sector",
    "concept",
    "hotmap",
    "gainer",
    "laggard",
    "amount",
    "quality",
    "news",
)


class _QualityRoeCache(TypedDict):
    expire_ts: float
    codes: list[str]


# 进程内质量池缓存（可变 dict，避免 importlib/global 重绑定踩坑）
_QUALITY_ROE_CACHE: _QualityRoeCache = {"expire_ts": 0.0, "codes": []}

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
    ("601138", "工业富联"),
    ("300308", "中际旭创"),
    ("688981", "中芯国际"),
    ("601919", "中远海控"),
    ("002463", "沪电股份"),
    ("600406", "国电南瑞"),
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
# 多路源
# ============================================================
async def _from_position(group_id: str, bot_id: str) -> List[str]:
    return await db.PaperPositionRepo.list_codes(group_id, bot_id)


async def _from_watchlist(group_id: str, bot_id: str) -> List[str]:
    return await db.PaperWatchlistRepo.list_codes(group_id, bot_id)


async def _from_agent_pool(group_id: str, bot_id: str) -> List[str]:
    return await db.PaperAgentPoolRepo.list_codes(group_id, bot_id)


def _valid_a_share_code(code: str | None) -> bool:
    return bool(code and len(code) == 6 and code.isdigit())


def interleave_source_pairs(
    pairs: Iterable[Tuple[str, str]],
    source_order: Tuple[str, ...] = MOMENTUM_SOURCE_ORDER,
) -> List[Tuple[str, str]]:
    """按 source 轮询交织，避免某一路先写满 momentum 名额。

    例：sector/hotmap/news 各 3 只 → s1,h1,n1,s2,h2,n2,... 而非 s1,s2,s3,h1,...
    """
    buckets: Dict[str, List[str]] = {s: [] for s in source_order}
    extra: Dict[str, List[str]] = {}
    for code, src in pairs:
        if src in buckets:
            buckets[src].append(code)
        else:
            extra.setdefault(src, []).append(code)
    out: List[Tuple[str, str]] = []
    # 已知源轮询
    while any(buckets.values()):
        for s in source_order:
            if buckets[s]:
                out.append((buckets[s].pop(0), s))
    # 未知 source 名追加末尾
    for s, codes in extra.items():
        for c in codes:
            out.append((c, s))
    return out


async def _stocks_from_ranked_boards(
    board_kind: str,
    *,
    top_boards: int = 4,
    per_board: int = 3,
    cap: int = 12,
) -> List[str]:
    """从「行业板块 / 概念板块」涨幅榜取 TOP 板块，再取各板块成分股涨幅 TOP。"""
    try:
        from ..utils.market import get_market, is_market_error

        market = get_market()
        fetch_n = max(top_boards * 3, 20)
        snap = await market.board(board_kind, limit=fetch_n, sort_asc=False)
        if is_market_error(snap) or not snap.rows:
            return []
        boards = sorted(
            list(snap.rows),
            key=lambda r: r.change_pct if r.change_pct is not None else -999.0,
            reverse=True,
        )[:top_boards]
        out: List[str] = []
        seen: Set[str] = set()
        for b in boards:
            bcode = str(b.code or "").strip()
            if not bcode:
                continue
            try:
                sub = await market.board(bcode, limit=per_board, sort_asc=False)
            except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
                logger.debug(f"[PaperTrade] 拉板块成分 {bcode} 失败: {e}")
                continue
            if is_market_error(sub) or not sub.rows:
                continue
            for row in sub.rows[:per_board]:
                c = row.code
                if not _valid_a_share_code(c) or c in seen:
                    continue
                seen.add(c)
                out.append(c)
                if len(out) >= cap:
                    return out
        return out
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[PaperTrade] 板块源 {board_kind} 拉取失败: {e}")
        return []


async def _from_sector_top_picks(top_sectors: int = 4, per_sector: int = 3) -> List[str]:
    """行业板块轮动：按板块涨幅 TOP 取成分股（不再用 menu 前 20 个乱序切片）。"""
    return await _stocks_from_ranked_boards(
        "行业板块",
        top_boards=top_sectors,
        per_board=per_sector,
        cap=SOURCE_CAPS["sector"],
    )


async def _from_concept_top_picks(top_concepts: int = 3, per_concept: int = 3) -> List[str]:
    """概念板块轮动：题材/事件驱动候选（与行业源互补）。"""
    return await _stocks_from_ranked_boards(
        "概念板块",
        top_boards=top_concepts,
        per_board=per_concept,
        cap=SOURCE_CAPS["concept"],
    )


async def _from_hotmap_top_n(n: int = 10) -> List[str]:
    """大盘热股 TOP N。"""
    try:
        from ..utils.market import get_market, is_market_error

        snap = await get_market().hotmap()
        if is_market_error(snap):
            return []
        codes: List[str] = []
        for row in snap.rows:
            c = row.code
            if _valid_a_share_code(c):
                codes.append(c)
            if len(codes) >= n:
                break
        return codes
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[PaperTrade] 热股拉取失败: {e}")
        return []


async def _from_hs_a_board(
    n: int = 10,
    *,
    sort_asc: bool = False,
) -> List[str]:
    """沪深 A 涨跌幅榜（board 默认按涨跌幅排序）。"""
    try:
        from ..utils.market import get_market, is_market_error

        snap = await get_market().board("沪深A", limit=max(n, 10), sort_asc=sort_asc)
        if is_market_error(snap) or not snap.rows:
            return []
        codes: List[str] = []
        for row in snap.rows:
            c = row.code
            if _valid_a_share_code(c):
                codes.append(c)
            if len(codes) >= n:
                break
        return codes
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[PaperTrade] 沪深A 榜单拉取失败 sort_asc={sort_asc}: {e}")
        return []


async def _from_market_gainers(n: int = 8) -> List[str]:
    """全市场涨幅榜（与板块源有重叠但覆盖非主题个股异动）。"""
    return await _from_hs_a_board(n, sort_asc=False)


async def _from_market_laggards(n: int = 6) -> List[str]:
    """全市场跌幅榜：超跌/反转候选，打破「只扫领涨」的单一风格。"""
    return await _from_hs_a_board(n, sort_asc=True)


async def _from_amount_leaders(n: int = 8) -> List[str]:
    """成交额龙头：Port.rank_list(amount)，真 fid=成交额排序。"""
    try:
        from ..utils.market import RankBy, get_market, is_market_error

        snap = await get_market().rank_list(RankBy.AMOUNT, limit=max(n, 10))
        if is_market_error(snap):
            return []
        codes: List[str] = []
        for row in snap.rows:
            if _valid_a_share_code(row.code):
                codes.append(row.code)
            if len(codes) >= n:
                break
        return codes
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[PaperTrade] 成交额榜拉取失败: {e}")
        return []


async def _from_quality_roe(n: int = 8) -> List[str]:
    """财务质量池：Port.rank_list(ROE) + 本地门槛；无硬编码票单。

    1. ``rank_list(roe)`` 拉宽页；
    2. 按 ROE / 负债率过滤（银行名称启发式 + 更低 ROE）；
    3. 进程内缓存 ``QUALITY_CACHE_HOURS``。

    榜空时降级：成交额/行业/涨幅宇宙 + ``financial_snapshot``。
    """
    import time
    import asyncio

    now = time.time()
    expire_ts = _QUALITY_ROE_CACHE["expire_ts"]
    cached_codes = _QUALITY_ROE_CACHE["codes"]
    if expire_ts > now and cached_codes:
        return list(cached_codes[:n])

    scored: List[Tuple[str, float]] = []
    try:
        from ..utils.market import RankBy, get_market, is_market_error

        snap = await get_market().rank_list(RankBy.ROE, limit=QUALITY_RANK_LIMIT)
        if not is_market_error(snap):
            for row in snap.rows:
                if not _valid_a_share_code(row.code):
                    continue
                roe = row.metric
                if roe is None:
                    continue
                is_bank = "银行" in (row.name or "")
                if is_bank:
                    if roe < QUALITY_ROE_MIN_BANK:
                        continue
                else:
                    if roe < QUALITY_ROE_MIN:
                        continue
                    debt = row.debt_ratio
                    if debt is not None and debt > QUALITY_DEBT_MAX_NONBANK:
                        continue
                scored.append((row.code, roe))
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(f"[PaperTrade] quality ROE 榜失败: {e}")

    if scored:
        seen: Set[str] = set()
        codes: List[str] = []
        for c, _ in scored:
            if c in seen:
                continue
            seen.add(c)
            codes.append(c)
        _QUALITY_ROE_CACHE["expire_ts"] = now + QUALITY_CACHE_HOURS * 3600.0
        _QUALITY_ROE_CACHE["codes"] = codes
        return codes[:n]

    logger.debug("[PaperTrade] quality ROE 榜无有效行，降级 amount+sector + snapshot")
    from ..utils.market import get_market, is_market_error

    universe: List[str] = []
    for src in (
        _from_amount_leaders(n=20),
        _from_sector_top_picks(top_sectors=5, per_sector=4),
        _from_market_gainers(n=15),
    ):
        try:
            for c in await src:
                if c not in universe and _valid_a_share_code(c):
                    universe.append(c)
        except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
            logger.debug(f"[PaperTrade] quality 降级 universe 源失败: {e}")
            continue
    universe = universe[:40]
    sem = asyncio.Semaphore(6)
    snap_scored: List[Tuple[str, float]] = []

    async def _one(code: str) -> None:
        async with sem:
            try:
                fin = await get_market().financial_snapshot(code)
            except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
                logger.debug(f"[PaperTrade] quality snapshot {code} 失败: {e}")
                return
            if is_market_error(fin):
                return
            roe_f = fin.roe
            if roe_f is None:
                return
            if fin.industry_type == "bank":
                if roe_f < QUALITY_ROE_MIN_BANK:
                    return
            else:
                if roe_f < QUALITY_ROE_MIN:
                    return
                debt = fin.debt_ratio
                if debt is not None and debt > QUALITY_DEBT_MAX_NONBANK:
                    return
            snap_scored.append((code, roe_f))

    await asyncio.gather(*[_one(c) for c in universe])
    snap_scored.sort(key=lambda x: -x[1])
    codes = [c for c, _ in snap_scored]
    _QUALITY_ROE_CACHE["expire_ts"] = now + QUALITY_CACHE_HOURS * 3600.0
    _QUALITY_ROE_CACHE["codes"] = codes
    return codes[:n]


# 常见 A 股 6 位代码模式
_TICKER_RE = re.compile(r"\b(\d{6})\b")

# 中文名 → 6 位代码的简易映射（仅作为兜底；新闻文本常不写代码）
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
    "中芯国际": "688981",
    "工业富联": "601138",
    "比亚迪": "002594",
    "紫金矿业": "601899",
    "立讯精密": "002475",
    "药明康德": "603259",
    "恒瑞医药": "600276",
    "长江电力": "600900",
    "中远海控": "601919",
    "沪电股份": "002463",
    "新易盛": "300502",
    "天孚通信": "300394",
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
    include_extra_momentum: bool = True,
) -> List[str]:
    """返回去重保序的股票代码列表（≤ 50 只）

    顺序：position → watchlist → agent_pool → sector → concept → hotmap →
    gainer → laggard → amount → quality → news
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
            if not _valid_a_share_code(c):
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
        try:
            _add(await _from_sector_top_picks(), SOURCE_CAPS["sector"])
        except Exception as e:
            logger.debug(f"[PaperTrade] sector 源失败: {e}")
        if include_extra_momentum:
            try:
                _add(await _from_concept_top_picks(), SOURCE_CAPS["concept"])
            except Exception as e:
                logger.debug(f"[PaperTrade] concept 源失败: {e}")

    if include_hotmap:
        try:
            _add(await _from_hotmap_top_n(), SOURCE_CAPS["hotmap"])
        except Exception as e:
            logger.debug(f"[PaperTrade] hotmap 源失败: {e}")

    if include_extra_momentum:
        try:
            _add(await _from_market_gainers(), SOURCE_CAPS["gainer"])
        except Exception as e:
            logger.debug(f"[PaperTrade] gainer 源失败: {e}")
        try:
            _add(await _from_market_laggards(), SOURCE_CAPS["laggard"])
        except Exception as e:
            logger.debug(f"[PaperTrade] laggard 源失败: {e}")
        try:
            _add(await _from_amount_leaders(), SOURCE_CAPS["amount"])
        except Exception as e:
            logger.debug(f"[PaperTrade] amount 源失败: {e}")
        try:
            _add(await _from_quality_roe(), SOURCE_CAPS["quality"])
        except Exception as e:
            logger.debug(f"[PaperTrade] quality 源失败: {e}")

    if include_news:
        try:
            _add(await _from_news_extract_tickers(), SOURCE_CAPS["news"])
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
