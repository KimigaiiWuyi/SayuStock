"""跨盘查询辅助（``模拟盘查询 <盘名>`` / ``模拟盘列表`` / ``模拟盘排行`` 命令用）。

多账户改造前这里是"按 group_id 查某个群的盘"；现在盘不属于群，查询键换成
**盘名 / account_id**。原来的"按群查"语义被拆成两个更准确的问题：

- 「这个盘怎么样」→ ``query_account(name)``
- 「这个群能看到哪些盘的播报」→ ``query_accounts_by_group(group_id)``（走
  ``SayuPaperBroadcastTarget``，而不是 ``SayuPaperAccount.group_id``——后者只是
  创建时的原群，播报订阅关系改过之后它就不再代表任何东西了）
"""

from typing import Any, Dict, List, Optional

from . import db
from ..utils.database.papertrade_models import SayuPaperAccount


async def query_account(name_or_id: str) -> Optional[dict]:
    """按盘名（或纯数字的 account_id）查一个盘。"""
    key = (name_or_id or "").strip()
    if not key:
        return None
    acc: Optional[SayuPaperAccount] = None
    if key.isdigit():
        acc = await db.PaperAccountRepo.get_by_id(int(key))
    if acc is None:
        acc = await db.PaperAccountRepo.get_by_name(key)
    if acc is None:
        matches = await db.PaperAccountRepo.search(key)
        if len(matches) == 1:
            acc = matches[0]
    return _acc_to_dict(acc) if acc else None


async def query_accounts() -> List[dict]:
    """全部盘的摘要（``模拟盘列表`` 用）。"""
    return [_acc_to_dict(a) for a in await db.PaperAccountRepo.list_all()]


async def query_accounts_by_group(group_id: str) -> List[dict]:
    """某个群订阅了哪些盘的播报。

    刻意**不**查 ``SayuPaperAccount.group_id``：那是创建时的原群，只用于排障。
    "这个群和哪些盘有关系"的唯一权威来源是播报目标表。
    """
    targets = await db.PaperBroadcastRepo.list_by_group(group_id, enabled_only=True)
    out: List[dict] = []
    seen: set[int] = set()
    for t in targets:
        if t.account_id in seen:
            continue
        seen.add(t.account_id)
        acc = await db.PaperAccountRepo.get_by_id(t.account_id)
        if acc is not None:
            out.append(_acc_to_dict(acc))
    return out


async def query_positions(account_id: int) -> List[dict]:
    positions = await db.PaperPositionRepo.list_by_account(account_id)
    return [
        {
            "stock_code": p.stock_code,
            "stock_name": p.stock_name,
            "qty": p.qty,
            "avg_cost": p.avg_cost,
        }
        for p in positions
    ]


async def query_trades(account_id: int, limit: int = 10) -> List[dict]:
    rows = await db.PaperTradeRepo.list_by_account(account_id, limit=limit)
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


async def query_latest_snapshot(account_id: int) -> Optional[dict]:
    snap = await db.PaperSnapshotRepo.latest(account_id)
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
    """跨盘排行：每个盘最新一条快照，按 total_pnl_pct 降序。"""
    snaps = await db.PaperSnapshotRepo.list_latest_all_accounts(limit=limit)
    accounts = {a.id: a for a in await db.PaperAccountRepo.list_all()}
    out: List[dict] = []
    for s in snaps:
        acc = accounts.get(s.account_id)
        out.append(
            {
                "account_id": s.account_id,
                "account_name": acc.name if acc is not None else f"#{s.account_id}",
                "strategy_id": acc.strategy_id if acc is not None else "",
                "trade_date": s.trade_date.isoformat(),
                "total_equity": s.total_equity,
                "total_pnl": s.total_pnl,
                "total_pnl_pct": s.total_pnl_pct,
            }
        )
    return out


def _acc_to_dict(acc: Optional[SayuPaperAccount]) -> Dict[str, Any]:
    if not acc:
        return {}
    return {
        "account_id": acc.id,
        "account_name": acc.name,
        "strategy_id": acc.strategy_id,
        "strategy_params": acc.strategy_params,
        "origin_group_id": acc.group_id,
        "cash": acc.cash,
        "initial_cash": acc.initial_cash,
        "principal": acc.principal,
        "mode": acc.mode,
        "frequency_minutes": acc.frequency_minutes,
        "enabled": acc.enabled,
        "last_decided_at": acc.last_decided_at.isoformat() if acc.last_decided_at else None,
    }
