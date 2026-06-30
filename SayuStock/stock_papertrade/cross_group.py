"""跨群查询辅助。

``AI操盘查询 <group_id>`` / ``AI操盘排行`` 命令用。
所有方法都走 ``.where(group_id == group_id)`` 直接命中索引，
避免 ``list_all`` 在大表上做 Python-side 全表过滤。

模块级助手用 ``async_maker`` 直接管理 session（见 ``docs/.../05-database.md §5.4``），
因为 ``@with_session`` 装饰器为首参为 cls 的 classmethod 设计，不能贴到普通函数。
"""

from typing import Any, Dict, List, Optional

from sqlmodel import col
from sqlalchemy import select

from gsuid_core.utils.database.base_models import async_maker

from . import db
from ..utils.database.papertrade_models import SayuPaperAccount


async def query_account(group_id: str, bot_id: Optional[str] = None) -> Optional[dict]:
    """查指定群的账户。bot_id 留空时返回该群最新一条（按 created_at desc）。"""
    if bot_id:
        acc = await db.PaperAccountRepo.get(group_id, bot_id)
        return _acc_to_dict(acc) if acc else None
    acc = await _select_first_account_by_group(group_id)
    return _acc_to_dict(acc) if acc else None


async def query_positions(group_id: str, bot_id: Optional[str] = None) -> List[dict]:
    bot_id = bot_id or await _resolve_bot_id_for_group(group_id)
    if not bot_id:
        return []
    positions = await db.PaperPositionRepo.list_by_account(group_id, bot_id)
    return [
        {
            "stock_code": p.stock_code,
            "stock_name": p.stock_name,
            "qty": p.qty,
            "avg_cost": p.avg_cost,
        }
        for p in positions
    ]


async def query_trades(group_id: str, bot_id: Optional[str] = None, limit: int = 10) -> List[dict]:
    bot_id = bot_id or await _resolve_bot_id_for_group(group_id)
    if not bot_id:
        return []
    rows = await db.PaperTradeRepo.list_by_account(group_id, bot_id, limit=limit)
    return [
        {
            "stock_code": t.stock_code,
            "stock_name": t.stock_name,
            "side": t.side,
            "price": t.price,
            "qty": t.qty,
            "amount": t.amount,
            "fee": t.fee,
            "realized_pnl": t.realized_pnl,
            "executed_at": t.executed_at.isoformat() if t.executed_at else None,
        }
        for t in rows
    ]


async def query_latest_snapshot(group_id: str, bot_id: Optional[str] = None) -> Optional[dict]:
    bot_id = bot_id or await _resolve_bot_id_for_group(group_id)
    if not bot_id:
        return None
    snap = await db.PaperSnapshotRepo.latest(group_id, bot_id)
    if not snap:
        return None
    return {
        "trade_date": snap.trade_date.isoformat(),
        "cash": snap.cash,
        "position_value": snap.position_value,
        "total_equity": snap.total_equity,
        "day_pnl": snap.day_pnl,
        "total_pnl": snap.total_pnl,
        "total_pnl_pct": snap.total_pnl_pct,
    }


async def query_leaderboard(limit: int = 20) -> List[dict]:
    """跨群排行：每个群最新一条快照，按 total_pnl_pct 降序"""
    snaps = await db.PaperSnapshotRepo.list_latest_all_groups(limit=limit)
    return [
        {
            "group_id": s.group_id,
            "bot_id": s.bot_id,
            "trade_date": s.trade_date.isoformat(),
            "total_equity": s.total_equity,
            "total_pnl": s.total_pnl,
            "total_pnl_pct": s.total_pnl_pct,
        }
        for s in snaps
    ]


# ============================================================
# 内部助手：走 async_maker 而非 @with_session —
#   @with_session 装饰器为首参吃 cls 的 classmethod 设计，不能贴到普通函数。
#   见 docs/skills/gscore-plugin-development/references/05-database.md §5.4。
# ============================================================
async def _select_first_account_by_group(group_id: str) -> Optional[SayuPaperAccount]:
    """按 group_id 取该群最新一条账户（created_at desc, id asc）。命中 (group_id) 索引。"""
    async with async_maker() as session:
        stmt = (
            select(SayuPaperAccount)
            .where(col(col(SayuPaperAccount.group_id)) == group_id)
            .order_by(col(col(SayuPaperAccount.created_at)).desc(), col(col(SayuPaperAccount.id)).asc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def _resolve_bot_id_for_group(group_id: str) -> str:
    """返回该群已开户的 bot_id；空群返回 ''。

    用 SELECT 命中 ``(group_id, bot_id)`` 唯一索引，避免 ``list_all`` 全表扫。
    """
    async with async_maker() as session:
        stmt = (
            select(col(SayuPaperAccount.bot_id))
            .where(col(col(SayuPaperAccount.group_id)) == group_id)
            .order_by(col(col(SayuPaperAccount.created_at)).desc(), col(col(SayuPaperAccount.id)).asc())
            .limit(1)
        )
        result = await session.execute(stmt)
        row = result.first()
        return row[0] if row else ""


def _acc_to_dict(acc: Optional[SayuPaperAccount]) -> Dict[str, Any]:
    if not acc:
        return {}
    return {
        "group_id": acc.group_id,
        "bot_id": acc.bot_id,
        "cash": acc.cash,
        "initial_cash": acc.initial_cash,
        "principal": acc.principal,
        "mode": acc.mode,
        "frequency_minutes": acc.frequency_minutes,
        "enabled": acc.enabled,
        "last_decided_at": acc.last_decided_at.isoformat() if acc.last_decided_at else None,
    }
