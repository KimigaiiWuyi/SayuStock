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

from ..utils.models import ItemType
from ..utils.request import NEWS_RETENTION_MS, get_news, clean_news
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

# get_news 会读写全局 NEWS 缓存；多个定时任务并发调用会向缓存重复 append，
# 用锁串行化所有取数入口
_FETCH_LOCK = asyncio.Lock()

# 所有新闻出站共用锁：整点多个 job 并发会同秒向多群发消息，QQ 风控对此最敏感
_SEND_LOCK = asyncio.Lock()

# 进程内发送去重（不持久化，重启即清空，仅作安全网）
# 不同群会推送相同新闻，因此以 group_id 为 key
# 结构: group_id -> 最近发送过的 news id 队列（最多 50 条，超出自动驱逐最旧）
_SENT_HISTORY: Dict[str, deque] = {}
_SENT_HISTORY_MAX = 50


def _already_sent(group_id: Optional[str], news_id: int) -> bool:
    """检查该群最近是否已发送过这条新闻"""
    if not group_id:
        return False
    history = _SENT_HISTORY.get(group_id)
    if not history:
        return False
    return news_id in history


def _mark_sent(group_id: Optional[str], news_id: int) -> None:
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


async def _update_watermark(subscribe: Subscribe, value: int) -> None:
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
        new = await get_news()
    if isinstance(new, int):
        logger.error(f"[SayuStock] 订阅新闻失败, 取消发送, 错误码：{new}!")
        return await bot.send(f"❌ [SayuStock] 订阅新闻失败！错误码：{new}!")

    await gs_subscribe.add_subscribe(
        "session",
        TASK_NAME,
        ev,
        extra_message=str(new[0]),
    )
    category = _resolve_category(ev.group_id, _load_category_sets())
    await bot.send(
        "✅ [SayuStock] 订阅雪球新闻成功！\n"
        f"📢 本群推送模式：{CATEGORY_DESC[category]}\n"
        "推送分级可在网页控制台 SayuStock 配置中按群调整"
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
            news = await get_news()
        if isinstance(news, int):
            logger.error(f"[SayuStock] 发送订阅新闻失败, 取消发送, 错误码：{news}!")
            return

        category_sets = _load_category_sets()

        for subscribe in datas:
            # 汇总类（2/3/4）的群由各自的定时任务推送，这里跳过，水位线也不动
            if _resolve_category(subscribe.group_id, category_sets) != CATEGORY_REALTIME:
                continue

            # 用真正发送出去的最大 ID 作为水位线，
            # 避免被雪球撤回的新闻卡死导致下一轮重发
            sent_max_id: int = int(subscribe.extra_message or 0)

            # 发送
            for new in reversed(news[1]["items"]):
                em = subscribe.extra_message
                if em and new["id"] > int(em) and new["mark"] in [1]:
                    # 同一群内同一条新闻去重
                    if _already_sent(subscribe.group_id, new["id"]):
                        continue
                    dt_local = _fmt_news_time(new["created_at"], "%Y-%m-%d %H:%M:%S")
                    sent = await _throttled_send(subscribe, f"【{dt_local}】雪球7x24消息\n{new['text']}")
                    if not sent:
                        # 断在这里：水位线停在最后一条真正发出的 id，本条及之后的留待下轮重发
                        logger.error(
                            f"[SayuStock] 雪球新闻推送到群 {subscribe.group_id} 失败，"
                            f"停在 id={sent_max_id}，剩余条目留待下轮重发"
                        )
                        break
                    sent_max_id = max(sent_max_id, new["id"])
                    _mark_sent(subscribe.group_id, new["id"])

            # 更新max_id
            await _update_watermark(subscribe, sent_max_id)


async def _send_digest(subscribe: Subscribe, items: List[ItemType], label: str) -> None:
    """给单个订阅发送汇总。

    多条新闻以 ``List[str]`` 交给 ``subscribe.send``，走核心现成的合并实现
    （segment.convert_message 会把纯字符串列表包成 MessageSegment.node 合并转发）；
    核心禁止合并转发时由 _digest_payload 退回纯文本，避免整条被丢弃。

    分批发送：只有确认发出去的批次才记 _SENT_HISTORY 并推进水位线，
    某批失败即中断，本批及之后的条目留到下个窗口重发。
    """
    watermark = int(subscribe.extra_message or 0)
    sent_max_id = watermark
    entries: List[Tuple[int, str]] = []
    first_dt = last_dt = ""

    for new in items:
        if new["id"] > watermark and new["mark"] in [1]:
            if _already_sent(subscribe.group_id, new["id"]):
                continue
            dt = _fmt_news_time(new["created_at"])
            if not entries:
                first_dt = dt
            last_dt = dt
            entries.append((new["id"], f"【{dt}】{new['text']}"))

    if not entries:
        return

    header = f"📰 雪球7x24 · {label}（共{len(entries)}条 · {first_dt}~{last_dt}）"
    for i in range(0, len(entries), _DIGEST_BATCH):
        batch = entries[i : i + _DIGEST_BATCH]
        texts = [line for _, line in batch]
        if i == 0:
            texts = [header] + texts

        if not await _throttled_send(subscribe, _digest_payload(texts)):
            logger.error(
                f"[SayuStock] 雪球新闻{label}推送到群 {subscribe.group_id} 失败，"
                f"停在 id={sent_max_id}，本批及后续留待下个窗口重发"
            )
            break

        for news_id, _ in batch:
            _mark_sent(subscribe.group_id, news_id)
        sent_max_id = max(sent_max_id, max(news_id for news_id, _ in batch))

    if sent_max_id > watermark:
        await _update_watermark(subscribe, sent_max_id)


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
        # 汇总要覆盖隔夜/隔日区间，进程重启后缓存是空的，必须回填到保留窗口
        news = await get_news(cover_ms=NEWS_RETENTION_MS)
    if isinstance(news, int):
        logger.error(f"[SayuStock] 发送雪球新闻{label}失败, 取消发送, 错误码：{news}!")
        return

    # 缓存内条目按 id 去重（并发取数的安全网），并按时间正序排列
    unique: Dict[int, ItemType] = {}
    for new in news[1]["items"]:
        unique[new["id"]] = new
    items = sorted(unique.values(), key=lambda x: (x["created_at"], x["id"]))

    for subscribe in targets:
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
