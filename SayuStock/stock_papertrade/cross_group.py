"""跨群查询辅助。

`AI操盘查询 <group_id>` / `AI操盘排行` 命令用。
所有方法都接受 group_id 显式参数，从 SQLModel 直接按 group_id 过滤。
"""

from typing import Any, Dict, List, Optional

from . import db


async def query_account(group_id: str, bot_id: Optional[str] = None) -> Optional[dict]:
    """查指定群的账户。bot_id 留空时取该群第一条（一般就 1 条）"""
    if bot_id:
        acc = await db.PaperAccountRepo.get(group_id, bot_id)
        return _acc_to_dict(acc) if acc else None
    all_accs = await db.PaperAccountRepo.list_all()
    matched = [a for a in all_accs if a.group_id == group_id]
    if not matched:
        return None
    return _acc_to_dict(matched[0])


async def query_positions(group_id: str, bot_id: Optional[str] = None) -> List[dict]:
    if bot_id:
        positions = await db.PaperPositionRepo.list_by_account(group_id, bot_id)
    else:
        all_accs = await db.PaperAccountRepo.list_all()
        bid = next((a.bot_id for a in all_accs if a.group_id == group_id), "")
        positions = await db.PaperPositionRepo.list_by_account(group_id, bid)
    return [
        {
            "stock_code": p.stock_code,
            "stock_name": p.stock_name,
            "qty": p.qty,
            "avg_cost": p.avg_cost,
        }
        for p in positions
    ]


async def query_trades(
    group_id: str, bot_id: Optional[str] = None, limit: int = 10
) -> List[dict]:
    if not bot_id:
        all_accs = await db.PaperAccountRepo.list_all()
        bid = next((a.bot_id for a in all_accs if a.group_id == group_id), "")
        bot_id = bid
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
    if not bot_id:
        all_accs = await db.PaperAccountRepo.list_all()
        bid = next((a.bot_id for a in all_accs if a.group_id == group_id), "")
        bot_id = bid
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


def _acc_to_dict(acc) -> Dict[str, Any]:
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
