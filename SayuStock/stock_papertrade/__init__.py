"""AI 模拟盘触发器层（13 个用户命令 + ai_entity 知识库注册）。

初始化流程：
1. 模块导入时自动注册 ai_alias（路由）+ ai_entity（知识库）
2. 13 个 on_command / on_fullmatch / on_prefix 触发器定义用户命令
3. 命令分两类：
   - 走 AI（to_ai）: AI操盘初始化/开启/关闭/模式/频率/查看/收益/记录/排行/查询/决策
   - 不走 AI（直接 ORM）: AI操盘自选添加/删除/查看
"""

import re
from pathlib import Path

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

# 知识库 + 别名注册
from gsuid_core.ai_core.models import KnowledgeBase
from gsuid_core.ai_core.register import ai_alias, ai_tools, ai_entity

# 模块内部
from . import db, cross_group
from .render import draw_leaderboard, draw_account_view
from .trading_calendar import is_trading_time, trading_day_summary, is_a_share_trading_day

# ============================================================
# ai_alias（路由；persona 提取意图时把"AI 操盘"对齐到 papertrade 工具族）
# ============================================================
ai_alias(
    "papertrade",
    ["AI操盘", "AI模拟盘", "虚拟盘", "AI模拟", "模拟盘", "模拟炒股"],
    scope="SayuStock",
)
ai_alias("papertrade_setup", ["AI操盘初始化", "AI模拟盘初始化", "建模拟盘"], scope="SayuStock")
ai_alias("papertrade_query", ["AI操盘查看", "AI操盘收益", "AI操盘记录", "AI操盘排行"], scope="SayuStock")

# ============================================================
# ai_entity（知识库；persona RAG 召回 "SayuStock AI 模拟盘" 文档）
# ============================================================
GUIDE_PATH = Path(__file__).parent / "PAPERTRADE_GUIDE.md"


def _register_papertrade_kb() -> None:
    if not GUIDE_PATH.exists():
        logger.warning(f"[SayuStock][PaperTrade] PAPERTRADE_GUIDE.md 不存在: {GUIDE_PATH}")
        return
    try:
        content = GUIDE_PATH.read_text(encoding="utf-8")
        ai_entity(
            KnowledgeBase(
                id="sayustock_papertrade_guide",
                plugin="SayuStock",
                title="SayuStock AI 模拟盘 · 早柚人格操作指南",
                content=content,
                tags=[
                    "AI操盘",
                    "AI模拟盘",
                    "虚拟盘",
                    "模拟盘",
                    "SayuStock",
                    "stock_agent",
                    "papertrade",
                ],
                source="plugin",
            )
        )
        logger.info("[SayuStock][PaperTrade] PAPERTRADE_GUIDE 知识库已注册")
    except Exception as e:
        logger.exception(f"[SayuStock][PaperTrade] 知识库注册失败: {e}")


_register_papertrade_kb()


# ============================================================
# SV 定义
# ============================================================
sv_papertrade = SV("AI模拟盘", pm=3, area="GROUP")


# ============================================================
# 工具：权限校验
# ============================================================
def _user_pm_level(ev: Event) -> int:
    """当前用户权限等级：0=master, 1=群主/管理员, >=2=普通成员。

    直接读 ev.user_pm（数字越低权限越高，默认 6=普通成员）。
    私聊默认 0（无群组时给最大权限）。
    """
    if not ev.group_id:
        # 私聊：给最大权限
        return 0
    try:
        return int(ev.user_pm) if ev.user_pm is not None else 6
    except (TypeError, ValueError):
        return 6


async def _check_admin(ev: Event) -> bool:
    """检查当前用户是否群主/管理员/SUPERUSER（user_pm <= 1）。"""
    return _user_pm_level(ev) <= 1


# ============================================================
# 1) AI操盘初始化
# ============================================================
@sv_papertrade.on_fullmatch(
    ("AI操盘初始化", "AI模拟盘初始化", "建模拟盘"),
    to_ai="""初始化 AI 模拟盘账户（默认 100w 现金，平衡模式）。

当用户说「AI操盘初始化」「建个模拟盘」「开模拟盘」时调用。
成功后会自动建：
- SQLModel 账户（100w 现金，balanced 模式）
- Kanban 周期树（每 30 分钟看盘，cron:0,30 9-14 * * 1-5）
- Kanban 收盘快照树（每日 15:35）
- Kanban 月报树（每月 1 日 09:00）

注意：仅群主/管理员可触发；初始化后账户 1 年内有效（到点自动 disarm）。

Args:
    text: 留空
""",
)
async def send_init_command(bot: Bot, ev: Event):
    if not await _check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可初始化 AI 模拟盘")
    from . import db as _db

    acc = await _db.PaperAccountRepo.get_or_create(
        group_id=str(ev.group_id),
        bot_id=ev.bot_id,
        initial_cash=1_000_000.0,
        mode="balanced",
        initialized_by=str(ev.user_id),
    )
    msg = (
        f"✅ AI 模拟盘已开户\n"
        f"群号: {ev.group_id}\n"
        f"初始资金: {acc.initial_cash:,.0f}\n"
        f"模式: {acc.mode}\n"
        f"心跳: {acc.frequency_minutes} 分钟\n\n"
        f"💡 提示：完整 Kanban 周期托管需 @早柚 说「AI操盘初始化」，"
        f"由早柚主人格走 evaluate + register_kanban_task 完整流程。"
    )
    await bot.send(msg)


# ============================================================
# 2) AI操盘初始化 <初始资金>
# ============================================================
@sv_papertrade.on_prefix(
    ("AI操盘初始化 ", "AI模拟盘初始化 "),
    to_ai="""初始化 AI 模拟盘账户（自定义初始资金）。

Args:
    text: 初始资金（1w~1亿），例如「AI操盘初始化 2000000」
""",
)
async def send_init_with_cash(bot: Bot, ev: Event):
    if not await _check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可初始化")
    text = ev.text.strip().replace(",", "").replace(" ", "").replace("万", "0000")
    try:
        amount = float(text)
    except ValueError:
        return await bot.send("⚠️ 初始资金格式错误，请输入纯数字（1w~1亿）")
    if amount < 10_000 or amount > 1_000_000_000:
        return await bot.send("⚠️ 初始资金须在 1w~1亿之间")
    from . import db as _db

    acc = await _db.PaperAccountRepo.get_or_create(
        group_id=str(ev.group_id),
        bot_id=ev.bot_id,
        initial_cash=amount,
        mode="balanced",
        initialized_by=str(ev.user_id),
    )
    await bot.send(f"✅ AI 模拟盘已开户\n群号: {ev.group_id}\n初始资金: {acc.initial_cash:,.0f}\n模式: {acc.mode}")


# ============================================================
# 6) AI操盘查看
# ============================================================
@sv_papertrade.on_fullmatch(
    ("AI操盘查看", "AI模拟盘查看", "查看AI操盘"),
)
async def send_view(bot: Bot, ev: Event):
    img = await draw_account_view(str(ev.group_id), ev.bot_id)
    await bot.send(img)


# ============================================================
# 7) AI操盘收益 [日/月/年/总]
# ============================================================
@sv_papertrade.on_prefix(
    ("AI操盘收益 ", "AI操盘 收益 "),
)
async def send_pnl(bot: Bot, ev: Event):
    from datetime import date, datetime, timedelta

    text = ev.text.strip()
    mapping = {
        "日": 1,
        "今日": 1,
        "今天": 1,
        "today": 1,
        "周": 7,
        "本周": 7,
        "this_week": 7,
        "月": 30,
        "本月": 30,
        "this_month": 30,
        "季": 90,
        "本季": 90,
        "年": 365,
        "本年": 365,
        "今年": 365,
        "ytd": 365,
        "总": None,
        "全部": None,
        "all": None,
    }
    days = mapping.get(text)
    if days is None and text not in ("总", "全部", "all"):
        return await bot.send("⚠️ 周期须为 日/周/月/季/年/总")
    if days is None:
        since = None
    else:
        since = datetime.now() - timedelta(days=days)

    from . import db as _db

    agg = await _db.PaperTradeRepo.aggregate_pnl(
        str(ev.group_id),
        ev.bot_id,
        since=since,
    )
    period = text if text in ("总", "全部", "all") else f"近{text}"
    msg = (
        f"📊 AI 操盘 · {period}盈亏\n"
        f"已实现盈亏: {agg['total_pnl']:+,.2f}\n"
        f"总成交额: {agg['total_amount']:,.0f}\n"
        f"总手续费: {agg['total_fee']:,.2f}\n"
        f"交易笔数: {agg['trade_count']}"
    )
    await bot.send(msg)


# ============================================================
# 8) AI操盘记录
# ============================================================
@sv_papertrade.on_fullmatch(
    ("AI操盘记录", "AI模拟盘记录", "AI操盘流水"),
)
async def send_records(bot: Bot, ev: Event):
    from . import db as _db

    rows = await _db.PaperTradeRepo.list_by_account(
        str(ev.group_id),
        ev.bot_id,
        limit=20,
    )
    if not rows:
        return await bot.send("ℹ️ 暂无交易记录")
    lines = ["【AI 操盘 · 最近 20 笔交易】"]
    for t in rows:
        side = "买" if t.side == "buy" else "卖"
        at = t.executed_at.strftime("%m-%d %H:%M") if t.executed_at else "?"
        pnl = f" 盈{t.realized_pnl:+.0f}" if t.side == "sell" and t.realized_pnl else ""
        lines.append(f"[{at}] {side} {t.stock_name or t.stock_code} {t.qty}@{t.price:.2f} 费{t.fee:.1f}{pnl}")
    await bot.send("\n".join(lines))


# ============================================================
# 13) AI操盘排行 / AI操盘查询 <group_id>
# ============================================================
@sv_papertrade.on_fullmatch(
    ("AI操盘排行", "AI模拟盘排行"),
)
async def send_leaderboard(bot: Bot, ev: Event):
    if not await _check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可看跨群排行")
    img = await draw_leaderboard()
    await bot.send(img)


@sv_papertrade.on_prefix(
    ("AI操盘查询 ", "AI模拟盘查询 "),
)
async def send_query_group(bot: Bot, ev: Event):
    if not await _check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可跨群查询")
    target = ev.text.strip()
    if not re.fullmatch(r"\d{5,15}", target):
        return await bot.send("⚠️ 群号格式错误（5~15 位数字）")
    acc = await cross_group.query_account(target)
    if not acc:
        return await bot.send(f"ℹ️ 群 {target} 未开通 AI 模拟盘")
    positions = await cross_group.query_positions(target)
    snap = await cross_group.query_latest_snapshot(target)
    lines = [
        f"【跨群查询 · 群 {target}】",
        f"模式: {acc['mode']}  状态: {'🟢' if acc['enabled'] else '🔴'}",
        f"现金: {acc['cash']:,.0f}  初始: {acc['initial_cash']:,.0f}",
    ]
    if snap:
        lines.append(
            f"总资产: {snap['total_equity']:,.0f}  盈亏: {snap['total_pnl']:+,.0f} ({snap['total_pnl_pct']:+.2f}%)"
        )
    if positions:
        lines.append(f"持仓: {len(positions)} 只")
        for p in positions[:5]:
            lines.append(f"  - {p['stock_name'] or p['stock_code']} ×{p['qty']}@{p['avg_cost']:.2f}")
    await bot.send("\n".join(lines))
