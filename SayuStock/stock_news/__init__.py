import random
import asyncio
from typing import Dict, List, Tuple, Union, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import deque

from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.database.models import Subscribe
from gsuid_core.utils.plugins_config.gs_config import sp_config

from ..utils.news import (
    NEWS_RETENTION_MS,
    NewsFeed,
    NewsItem,
    id_newer,
    source_label,
    get_news_port,
    is_news_error,
    should_rebase,
    split_watermark,
)
from ..utils.request import clean_news
from ..utils.time_range import now_bjt

sv_stock_subscribe = SV("订阅新闻", pm=2, area="GROUP")

TASK_NAME = "雪球新闻订阅"

# ── 推送分级 ──
# 1=逐条实时推送（默认，未归类的群）；2=小时汇总；3=交易时段(08/12/16/22点)汇总；4=每日(08点)汇总
CATEGORY_REALTIME = 1
CATEGORY_HOURLY = 2
CATEGORY_TRADING = 3
CATEGORY_DAILY = 4

CATEGORY_DESC = {
    CATEGORY_REALTIME: "逐条实时推送",
    CATEGORY_HOURLY: "小时汇总（每小时整点合并推送上一小时的消息）",
    CATEGORY_TRADING: "交易时段汇总（每日 08:00 / 12:00 / 16:00 / 22:00 各合并推送一次）",
    CATEGORY_DAILY: "每日汇总（每日 08:00 合并推送一次）",
}

# 汇总推送单条合并消息最多容纳的新闻数，超出则拆成多条合并消息发送
_DIGEST_BATCH = 50

# 新闻时间统一按北京时间展示（调度器时区也是 Asia/Shanghai）
_BJT = ZoneInfo("Asia/Shanghai")

# 多个定时任务会同时拉快讯。锁住取数，避免并发打同一新闻源
_FETCH_LOCK = asyncio.Lock()

# 所有新闻出站共用锁：整点多个 job 并发会同秒向多群发消息，QQ 风控对此最敏感
_SEND_LOCK = asyncio.Lock()

# 进程内发送去重（不持久化，重启即清空，仅作安全网）
# 不同群会推送相同新闻，因此以 group_id 为 key
# 结构: group_id -> 最近发送过的 news id 队列（最多 50 条，超出自动驱逐最旧）
_SENT_HISTORY: Dict[str, deque] = {}
_SENT_HISTORY_MAX = 50


def _already_sent(group_id: Optional[str], news_id: str) -> bool:
    """检查该群最近是否已发送过这条新闻"""
    if not group_id:
        return False
    history = _SENT_HISTORY.get(group_id)
    if not history:
        return False
    return news_id in history


def _mark_sent(group_id: Optional[str], news_id: str) -> None:
    """记录该群已发送过这条新闻"""
    if not group_id:
        return
    history = _SENT_HISTORY.get(group_id)
    if history is None:
        history = deque(maxlen=_SENT_HISTORY_MAX)
        _SENT_HISTORY[group_id] = history
    history.append(news_id)


# 全局关闭合并转发时 Core 会直接丢弃 node（整条消息不发），只能退回纯文本
_FORWARD_DISABLED = "禁止(不发送任何消息)"


def _digest_payload(texts: List[str]) -> Union[str, List[str]]:
    """把一批汇总正文转成可发送载荷；合并转发被禁用时退回纯文本"""
    if sp_config.get_config("EnableForwardMessage").data == _FORWARD_DISABLED:
        return "\n".join(texts)
    return texts


async def _throttled_send(subscribe: Subscribe, message: Union[str, List[str]]) -> bool:
    """全局串行发送单条订阅消息，发送后强制间隔 2-5s（含在锁内）。

    返回是否真的送达。``Subscribe.send`` 在机器人离线 / WS_BOT_ID 失效时返回 -1
    而不抛异常，调用方据此决定要不要推进水位线，避免丢消息。
    """
    async with _SEND_LOCK:
        result = await subscribe.send(message)
        await asyncio.sleep(2 + random.random() * 3)
    return result != -1


def _fmt_news_time(created_at: int, fmt: str = "%m-%d %H:%M") -> str:
    """新闻时间戳（毫秒）转北京时间文本"""
    return datetime.fromtimestamp(created_at / 1000, _BJT).strftime(fmt)


def _load_category_sets() -> Tuple[frozenset, frozenset, frozenset]:
    """从配置面板读三类汇总群列表；每次调用都读，网页控制台改完立即生效"""
    from ..stock_config.stock_config import STOCK_CONFIG

    def _read(key: str) -> frozenset:
        raw = STOCK_CONFIG.get_config(key).data or []
        return frozenset(str(i).strip() for i in raw if str(i).strip())

    return (
        _read("news_push_hourly_groups"),
        _read("news_push_trading_session_groups"),
        _read("news_push_daily_groups"),
    )


def _resolve_category(
    group_id: Optional[str],
    category_sets: Tuple[frozenset, frozenset, frozenset],
) -> int:
    """群 -> 推送类别。没有归类到任何汇总列表的群默认为类别1（逐条实时）。

    同一群出现在多个列表时按 小时 > 交易时段 > 每日 优先。
    """
    gid = str(group_id).strip() if group_id else ""
    if not gid:
        return CATEGORY_REALTIME
    hourly, trading, daily = category_sets
    if gid in hourly:
        return CATEGORY_HOURLY
    if gid in trading:
        return CATEGORY_TRADING
    if gid in daily:
        return CATEGORY_DAILY
    return CATEGORY_REALTIME


def _advance_mark(current: str, candidate: str) -> str:
    """水位线只往同一源里更大的 id 走。"""
    if not current:
        return candidate
    cur_id = split_watermark(current)[1]
    new_id = split_watermark(candidate)[1]
    if id_newer(new_id, cur_id):
        return candidate
    return current


def _max_item(feed: NewsFeed) -> NewsItem | None:
    if not feed.items:
        return None
    best = feed.items[0]
    for item in feed.items[1:]:
        if id_newer(item.id, best.id):
            best = item
    return best


def _pending_important(feed: NewsFeed) -> list[NewsItem]:
    """同一源内、要闻、按时间从旧到新，方便水位线逐条推进。"""
    return sorted(
        (item for item in feed.items if item.important),
        key=lambda item: (item.published_ms, item.id),
    )


async def _rebase_if_needed(subscribe: Subscribe, feed: NewsFeed) -> bool:
    """换源或旧雪球纯数字水位线不可比时，抬到当前最大 id，不补发历史。"""
    if not should_rebase(subscribe.extra_message, feed.source):
        return False
    newest = _max_item(feed)
    if newest is None:
        return True
    old_source, _old_id = split_watermark(subscribe.extra_message)
    await _update_watermark(subscribe, newest.watermark())
    logger.info(
        f"[SayuStock] 群 {subscribe.group_id} 新闻水位线从 {old_source or '旧雪球'} 重置到 {newest.watermark()}"
    )
    return True


async def _update_watermark(subscribe: Subscribe, value: str) -> None:
    """更新订阅的水位线（extra_message 存已发送的最大新闻 id）"""
    opt: Dict[str, Union[str, int, None]] = {
        "bot_id": subscribe.bot_id,
        "task_name": TASK_NAME,
    }

    for i in [
        "user_id",
        "bot_id",
        "group_id",
        "bot_self_id",
        "user_type",
    ]:
        if i not in opt:
            opt[i] = subscribe.__getattribute__(i)

    await Subscribe.update_data_by_data(
        opt,
        {"extra_message": str(value)},
    )


@sv_stock_subscribe.on_fullmatch(
    ("订阅雪球新闻", "订阅雪球热点"),
    to_ai="""订阅雪球7x24小时财经新闻推送

    当用户说"订阅新闻"、"开启新闻推送"、"订阅雪球热点"、
    "帮我订阅财经新闻"、"开启新闻提醒"时调用。
    订阅后会自动推送最新的雪球财经新闻。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_add_subscribe_info(bot: Bot, ev: Event) -> list[str] | None:
    logger.info("✅ [SayuStock] 开始执行[订阅新闻]")
    async with _FETCH_LOCK:
        feed = await get_news_port().latest()
    if is_news_error(feed):
        logger.error(f"[SayuStock] 订阅新闻失败, 取消发送, 错误码：{feed.code}!")
        return await bot.send(f"❌ [SayuStock] 订阅新闻失败！错误码：{feed.code}!")
    newest = _max_item(feed)
    if newest is None:
        return await bot.send("❌ [SayuStock] 订阅新闻失败！当前没有快讯")

    await gs_subscribe.add_subscribe(
        "session",
        TASK_NAME,
        ev,
        extra_message=newest.watermark(),
    )
    category = _resolve_category(ev.group_id, _load_category_sets())
    await bot.send(
        "✅ [SayuStock] 订阅财经快讯成功！\n"
        f"📰 当前源：{source_label(feed.source)}\n"
        f"📢 本群推送模式：{CATEGORY_DESC[category]}\n"
        "推送分级和新闻源顺序可在网页控制台 SayuStock 配置中调整"
    )


@sv_stock_subscribe.on_fullmatch(
    ("取消订阅雪球新闻", "取消订阅雪球热点"),
    to_ai="""取消订阅雪球财经新闻推送

    当用户说"取消订阅新闻"、"关闭新闻推送"、"取消雪球热点"、
    "不要再推送新闻了"、"关闭新闻提醒"时调用。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_delete_subscribe_info(bot: Bot, ev: Event) -> None:
    logger.info("✅ [SayuStock] 开始执行[取消订阅新闻]")
    await gs_subscribe.delete_subscribe("session", TASK_NAME, ev)
    await bot.send("✅ [SayuStock] 取消订阅雪球新闻成功！")


# 每隔十分钟检查一次订阅
@scheduler.scheduled_job("cron", minute="1-59/5")
async def send_subscribe_info() -> None:
    await asyncio.sleep(15 + random.random() * 10)
    datas = await gs_subscribe.get_subscribe(TASK_NAME)
    if datas:
        async with _FETCH_LOCK:
            feed = await get_news_port().latest()
        if is_news_error(feed):
            logger.error(f"[SayuStock] 发送订阅新闻失败, 取消发送, 错误码：{feed.code}!")
            return

        category_sets = _load_category_sets()
        pending = _pending_important(feed)

        for subscribe in datas:
            # 汇总类（2/3/4）的群由各自的定时任务推送，这里跳过，水位线也不动
            if _resolve_category(subscribe.group_id, category_sets) != CATEGORY_REALTIME:
                continue
            if await _rebase_if_needed(subscribe, feed):
                continue

            _source, watermark_id = split_watermark(subscribe.extra_message)
            sent_mark = subscribe.extra_message or ""

            for item in pending:
                if not id_newer(item.id, watermark_id):
                    continue
                mark = item.watermark()
                if _already_sent(subscribe.group_id, mark):
                    continue
                dt_local = _fmt_news_time(item.published_ms, "%Y-%m-%d %H:%M:%S")
                label = source_label(item.source)
                sent = await _throttled_send(subscribe, f"【{dt_local}】{label}\n{item.text}")
                if not sent:
                    logger.error(
                        f"[SayuStock] 快讯推送到群 {subscribe.group_id} 失败，停在 {sent_mark}，剩余条目留待下轮重发"
                    )
                    break
                sent_mark = _advance_mark(sent_mark, mark)
                _mark_sent(subscribe.group_id, mark)

            if sent_mark and sent_mark != (subscribe.extra_message or ""):
                await _update_watermark(subscribe, sent_mark)


async def _send_digest(subscribe: Subscribe, items: List[NewsItem], label: str) -> None:
    """给单个订阅发送汇总。

    多条新闻以 ``List[str]`` 交给 ``subscribe.send``，走核心现成的合并实现
    （segment.convert_message 会把纯字符串列表包成 MessageSegment.node 合并转发）；
    核心禁止合并转发时由 _digest_payload 退回纯文本，避免整条被丢弃。

    分批发送：只有确认发出去的批次才记 _SENT_HISTORY 并推进水位线，
    某批失败即中断，本批及之后的条目留到下个窗口重发。
    """
    _source, watermark_id = split_watermark(subscribe.extra_message)
    sent_mark = subscribe.extra_message or ""
    entries: List[Tuple[str, str]] = []
    first_dt = last_dt = ""
    origin = ""

    for item in items:
        if not item.important or not id_newer(item.id, watermark_id):
            continue
        mark = item.watermark()
        if _already_sent(subscribe.group_id, mark):
            continue
        dt = _fmt_news_time(item.published_ms)
        if not entries:
            first_dt = dt
            origin = source_label(item.source)
        last_dt = dt
        entries.append((mark, f"【{dt}】{item.text}"))

    if not entries:
        return

    header = f"📰 {origin} · {label}（共{len(entries)}条 · {first_dt}~{last_dt}）"
    for i in range(0, len(entries), _DIGEST_BATCH):
        batch = entries[i : i + _DIGEST_BATCH]
        texts = [line for _, line in batch]
        if i == 0:
            texts = [header] + texts

        if not await _throttled_send(subscribe, _digest_payload(texts)):
            logger.error(
                f"[SayuStock] 快讯{label}推送到群 {subscribe.group_id} 失败，"
                f"停在 {sent_mark}，本批及后续留待下个窗口重发"
            )
            break

        for news_id, _ in batch:
            _mark_sent(subscribe.group_id, news_id)
            sent_mark = _advance_mark(sent_mark, news_id)

    if sent_mark and sent_mark != (subscribe.extra_message or ""):
        await _update_watermark(subscribe, sent_mark)


async def _push_digest(category: int, label: str) -> None:
    """给指定类别的订阅群推送自上次推送以来的新闻汇总"""
    await asyncio.sleep(15 + random.random() * 10)
    datas = await gs_subscribe.get_subscribe(TASK_NAME)
    if not datas:
        return

    category_sets = _load_category_sets()
    targets = [s for s in datas if _resolve_category(s.group_id, category_sets) == category]
    if not targets:
        return

    async with _FETCH_LOCK:
        # 汇总要覆盖隔夜/隔日区间，由新闻源自己翻页，不依赖进程内缓存
        feed = await get_news_port().latest(cover_ms=NEWS_RETENTION_MS)
    if is_news_error(feed):
        logger.error(f"[SayuStock] 发送快讯{label}失败, 取消发送, 错误码：{feed.code}!")
        return

    items = _pending_important(feed)
    for subscribe in targets:
        if await _rebase_if_needed(subscribe, feed):
            continue
        await _send_digest(subscribe, items, label)


# 类别2：每小时整点推送上一小时的新闻汇总
@scheduler.scheduled_job("cron", minute=0)
async def push_hourly_digest() -> None:
    await _push_digest(CATEGORY_HOURLY, "小时汇总")


# 22 点切段是为了让夜间睡眠段（23:00-08:00）不被单条汇总覆盖；时段名取 now_bjt().hour
_TRADING_SESSION_LABELS = {
    8: "隔夜汇总",
    12: "午间汇总",
    16: "收盘汇总",
    22: "晚间汇总",
}


@scheduler.scheduled_job("cron", hour="8,12,16,22", minute=0)
async def push_trading_session_digest() -> None:
    # 按北京时间墙钟取时段名，避免部署时区与调度器配置不一致时标错段
    label = _TRADING_SESSION_LABELS.get(now_bjt().hour, "交易时段汇总")
    await _push_digest(CATEGORY_TRADING, label)


# 类别4：每日 08:00 推送一次
@scheduler.scheduled_job("cron", hour=8, minute=0)
async def push_daily_digest() -> None:
    await _push_digest(CATEGORY_DAILY, "每日汇总")


# 每天凌晨零点，清理超过 24h 的旧新闻缓存（保留隔夜部分供早间汇总推送取用）
@scheduler.scheduled_job("cron", hour=0, minute=0)
async def clean_news_data() -> None:
    logger.info("[SayuStock] 开始执行[清理过期新闻缓存]")
    await clean_news()
    logger.success("[SayuStock] 清理过期新闻缓存成功!")
