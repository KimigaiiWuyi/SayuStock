"""用户命令（``sv_papertrade`` 注册，pm=3）。

公开命令：
1. ``send_init_command``  — ``AI操盘初始化`` (群主 / 管理员权限)
2. ``send_view``          — ``模拟盘查看``
3. ``send_pnl``           — ``模拟盘收益`` (周期: 日/周/月/季/年/ytd/总)
4. ``send_records``       — ``模拟盘记录``
5. ``send_leaderboard``   — ``模拟盘排行`` (群主 / 管理员权限)
6. ``send_query_group``   — ``模拟盘查询 <group_id>`` (群主 / 管理员权限)

所有 DB 操作走 ``from . import db as _db`` 函数内 lazy import；渲染走
``from .render import draw_xxx``；跨群查询走 ``from . import cross_group as _cross``。
"""

import re
import datetime as _dt

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from . import db as _db, cross_group as _cross
from .sv import sv_papertrade
from .render import draw_leaderboard, draw_account_view
from .permissions import check_admin


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
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可初始化 AI 模拟盘")

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
    )
    await bot.send(msg)


# ============================================================
# 2) 模拟盘查看
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘查看",),
)
async def send_view(bot: Bot, ev: Event):
    img = await draw_account_view(str(ev.group_id), ev.bot_id)
    await bot.send(img)


# ============================================================
# 3) 模拟盘收益
# ============================================================
@sv_papertrade.on_prefix(
    ("模拟盘收益",),
)
async def send_pnl(bot: Bot, ev: Event):
    text: str = ev.text.strip()
    # 把"近 N 天"和"自某日期"两类周期统一到 since: datetime | None。
    # 关键修复：ytd = 今年 1/1 至今，不是 now-365d，否则 6 月份触发会少算 6 个月。
    mapping: dict[str, int | None] = {
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
        # 注意："ytd" 单独走 since_calc，不进 mapping
        "总": None,
        "全部": None,
        "all": None,
    }

    if text == "ytd":
        now = _dt.datetime.now()
        since: _dt.datetime | None = _dt.datetime(now.year, 1, 1)
    elif text not in mapping:
        return await bot.send("⚠️ 周期须为 日/周/月/季/年/ytd/总")
    else:
        days = mapping[text]
        since = None if days is None else _dt.datetime.now() - _dt.timedelta(days=days)

    agg = await _db.PaperTradeRepo.aggregate_pnl(
        str(ev.group_id),
        ev.bot_id,
        since=since,
    )
    period: str = text if text in ("总", "全部", "all") else f"近{text}"
    msg = (
        f"📊 AI 操盘 · {period}盈亏\n"
        f"已实现盈亏: {agg['total_pnl']:+,.2f}\n"
        f"总成交额: {agg['total_amount']:,.0f}\n"
        f"总手续费: {agg['total_fee']:,.2f}\n"
        f"交易笔数: {agg['trade_count']}"
    )
    await bot.send(msg)


# ============================================================
# 4) 模拟盘记录
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘记录",),
)
async def send_records(bot: Bot, ev: Event):
    rows = await _db.PaperTradeRepo.list_by_account(
        str(ev.group_id),
        ev.bot_id,
        limit=20,
    )
    if not rows:
        return await bot.send("ℹ️ 暂无交易记录")
    lines: list[str] = ["【AI 操盘 · 最近 20 笔交易】"]
    for t in rows:
        side = "买" if t.side == "buy" else "卖"
        executed_at: _dt.datetime | None = t.executed_at
        at = executed_at.strftime("%m-%d %H:%M") if executed_at else "?"
        pnl = f" 盈{t.realized_pnl:+.0f}" if t.side == "sell" and t.realized_pnl else ""
        lines.append(f"[{at}] {side} {t.stock_name or t.stock_code} {t.qty}@{t.price:.2f} 费{t.fee:.1f}{pnl}")
    await bot.send("\n".join(lines))


# ============================================================
# 5) 模拟盘排行
# ============================================================
@sv_papertrade.on_fullmatch(
    ("模拟盘排行",),
)
async def send_leaderboard(bot: Bot, ev: Event):
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可看跨群排行")
    img = await draw_leaderboard()
    await bot.send(img)


# ============================================================
# 6) 模拟盘查询
# ============================================================
@sv_papertrade.on_prefix(
    ("模拟盘查询",),
)
async def send_query_group(bot: Bot, ev: Event):
    if not await check_admin(ev):
        return await bot.send("⚠️ 仅群主/管理员可跨群查询")
    target: str = ev.text.strip()
    if not re.fullmatch(r"\d{5,15}", target):
        return await bot.send("⚠️ 群号格式错误（5~15 位数字）")
    acc = await _cross.query_account(target)
    if not acc:
        return await bot.send(f"ℹ️ 群 {target} 未开通 AI 模拟盘")
    positions = await _cross.query_positions(target)
    snap = await _cross.query_latest_snapshot(target)
    lines: list[str] = [
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
