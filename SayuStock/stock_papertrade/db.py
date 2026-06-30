"""AI 模拟盘数据库 Repo 层（7 张表 CRUD 包装）。

所有方法走 ``@with_session`` 自动管理事务；返回 list / instance / None。
复杂聚合查询用 ``async_maker`` 手写 session。
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.utils.database.base_models import with_session, async_maker

from ..utils.database.papertrade_models import (
    SayuPaperAccount,
    SayuPaperAgentPool,
    SayuPaperDecision,
    SayuPaperPosition,
    SayuPaperSnapshot,
    SayuPaperTrade,
    SayuPaperWatchlist,
)


# ============================================================
# Account Repo
# ============================================================
class PaperAccountRepo:
    @classmethod
    @with_session
    async def get(
        cls, session: AsyncSession, group_id: str, bot_id: str
    ) -> Optional[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).where(
            and_(
                SayuPaperAccount.group_id == group_id,
                SayuPaperAccount.bot_id == bot_id,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_or_create(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        initial_cash: float = 1_000_000.0,
        mode: str = "balanced",
        initialized_by: Optional[str] = None,
    ) -> SayuPaperAccount:
        existing = await cls.get(group_id, bot_id)
        if existing:
            return existing
        now = datetime.now()
        acc = SayuPaperAccount(
            group_id=group_id,
            bot_id=bot_id,
            cash=initial_cash,
            initial_cash=initial_cash,
            principal=initial_cash,
            mode=mode,
            frequency_minutes=30,
            enabled=1,
            initialized_by=initialized_by,
            created_at=now,
            started_at=now,
        )
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def update(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        **fields: Any,
    ) -> Optional[SayuPaperAccount]:
        acc = await cls.get(group_id, bot_id)
        if not acc:
            return None
        for k, v in fields.items():
            if hasattr(acc, k):
                setattr(acc, k, v)
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def update_cash(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        delta: float,
    ) -> Optional[SayuPaperAccount]:
        acc = await cls.get(group_id, bot_id)
        if not acc:
            return None
        acc.cash += delta
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def list_enabled(
        cls, session: AsyncSession
    ) -> List[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).where(SayuPaperAccount.enabled == 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_all(
        cls, session: AsyncSession
    ) -> List[SayuPaperAccount]:
        stmt = select(SayuPaperAccount)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def bind_kanban_init(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        root_id: str,
    ) -> None:
        acc = await cls.get(group_id, bot_id)
        if acc:
            acc.kanban_init_root_id = root_id
            session.add(acc)
            await session.flush()

    @classmethod
    @with_session
    async def bind_kanban_period(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        root_id: str,
    ) -> None:
        acc = await cls.get(group_id, bot_id)
        if acc:
            acc.kanban_period_root_id = root_id
            session.add(acc)
            await session.flush()

    @classmethod
    @with_session
    async def touch_decided(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> None:
        acc = await cls.get(group_id, bot_id)
        if acc:
            acc.last_decided_at = datetime.now()
            session.add(acc)
            await session.flush()

    @classmethod
    @with_session
    async def reset_account(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> Dict[str, int]:
        """重置账户：清空账户 + 持仓 + 流水 + 决策 + 快照 + AI 内部池 + 群友关注列表。

        Returns:
            {"account": 1, "position": N, "trade": N, ...} 各表删除条数
        """
        from sqlalchemy import delete

        deleted: Dict[str, int] = {}

        # 1. 持仓
        r = await session.execute(
            delete(SayuPaperPosition).where(
                and_(
                    SayuPaperPosition.group_id == group_id,
                    SayuPaperPosition.bot_id == bot_id,
                )
            )
        )
        deleted["position"] = int(r.rowcount or 0)

        # 2. 交易流水
        r = await session.execute(
            delete(SayuPaperTrade).where(
                and_(
                    SayuPaperTrade.group_id == group_id,
                    SayuPaperTrade.bot_id == bot_id,
                )
            )
        )
        deleted["trade"] = int(r.rowcount or 0)

        # 3. 决策日志
        r = await session.execute(
            delete(SayuPaperDecision).where(
                and_(
                    SayuPaperDecision.group_id == group_id,
                    SayuPaperDecision.bot_id == bot_id,
                )
            )
        )
        deleted["decision"] = int(r.rowcount or 0)

        # 4. 每日净值
        r = await session.execute(
            delete(SayuPaperSnapshot).where(
                and_(
                    SayuPaperSnapshot.group_id == group_id,
                    SayuPaperSnapshot.bot_id == bot_id,
                )
            )
        )
        deleted["snapshot"] = int(r.rowcount or 0)

        # 5. 群友关注列表
        r = await session.execute(
            delete(SayuPaperWatchlist).where(
                and_(
                    SayuPaperWatchlist.group_id == group_id,
                    SayuPaperWatchlist.bot_id == bot_id,
                )
            )
        )
        deleted["watchlist"] = int(r.rowcount or 0)

        # 6. AI 内部决策池
        r = await session.execute(
            delete(SayuPaperAgentPool).where(
                and_(
                    SayuPaperAgentPool.group_id == group_id,
                    SayuPaperAgentPool.bot_id == bot_id,
                )
            )
        )
        deleted["agent_pool"] = int(r.rowcount or 0)

        # 7. 账户本身
        r = await session.execute(
            delete(SayuPaperAccount).where(
                and_(
                    SayuPaperAccount.group_id == group_id,
                    SayuPaperAccount.bot_id == bot_id,
                )
            )
        )
        deleted["account"] = int(r.rowcount or 0)

        return deleted


# ============================================================
# Position Repo
# ============================================================
class PaperPositionRepo:
    @classmethod
    @with_session
    async def get(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
    ) -> Optional[SayuPaperPosition]:
        stmt = select(SayuPaperPosition).where(
            and_(
                SayuPaperPosition.group_id == group_id,
                SayuPaperPosition.bot_id == bot_id,
                SayuPaperPosition.stock_code == stock_code,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def list_by_account(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> List[SayuPaperPosition]:
        stmt = (
            select(SayuPaperPosition)
            .where(
                and_(
                    SayuPaperPosition.group_id == group_id,
                    SayuPaperPosition.bot_id == bot_id,
                    SayuPaperPosition.qty > 0,
                )
            )
            .order_by(SayuPaperPosition.updated_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_codes(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> List[str]:
        stmt = select(SayuPaperPosition.stock_code).where(
            and_(
                SayuPaperPosition.group_id == group_id,
                SayuPaperPosition.bot_id == bot_id,
                SayuPaperPosition.qty > 0,
            )
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    @classmethod
    @with_session
    async def upsert(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        qty: int,
        avg_cost: float,
    ) -> Optional[SayuPaperPosition]:
        """新建或更新持仓。qty=0 时删除持仓记录。"""
        if qty <= 0:
            existing = await cls.get(group_id, bot_id, stock_code)
            if existing:
                await session.delete(existing)
                await session.flush()
            return None
        existing = await cls.get(group_id, bot_id, stock_code)
        now = datetime.now()
        if existing:
            existing.qty = qty
            existing.avg_cost = avg_cost
            existing.stock_name = stock_name
            existing.secid = secid
            existing.updated_at = now
            session.add(existing)
            await session.flush()
            return existing
        pos = SayuPaperPosition(
            group_id=group_id,
            bot_id=bot_id,
            stock_code=stock_code,
            stock_name=stock_name,
            secid=secid,
            qty=qty,
            avg_cost=avg_cost,
            opened_at=now,
            updated_at=now,
        )
        session.add(pos)
        await session.flush()
        return pos


# ============================================================
# Trade Repo（append-only）
# ============================================================
class PaperTradeRepo:
    @classmethod
    @with_session
    async def append(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        side: str,
        price: float,
        qty: int,
        amount: float,
        fee: float,
        realized_pnl: float = 0.0,
        reason: str = "",
        snapshot: str = "",
        decision_id: Optional[int] = None,
        mode: str = "balanced",
    ) -> SayuPaperTrade:
        trade = SayuPaperTrade(
            group_id=group_id,
            bot_id=bot_id,
            stock_code=stock_code,
            stock_name=stock_name,
            secid=secid,
            side=side,
            price=price,
            qty=qty,
            amount=amount,
            fee=fee,
            realized_pnl=realized_pnl,
            reason=reason,
            snapshot=snapshot,
            decided_at=datetime.now(),
            executed_at=datetime.now(),
            decision_id=decision_id,
            mode=mode,
        )
        session.add(trade)
        await session.flush()
        return trade

    @classmethod
    @with_session
    async def list_by_account(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        limit: int = 50,
        stock_code: Optional[str] = None,
    ) -> List[SayuPaperTrade]:
        stmt = (
            select(SayuPaperTrade)
            .where(
                and_(
                    SayuPaperTrade.group_id == group_id,
                    SayuPaperTrade.bot_id == bot_id,
                )
            )
            .order_by(SayuPaperTrade.executed_at.desc())
            .limit(limit)
        )
        if stock_code:
            stmt = stmt.where(SayuPaperTrade.stock_code == stock_code)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def count_today(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        today: date,
    ) -> int:
        stmt = select(func.count(SayuPaperTrade.id)).where(
            and_(
                SayuPaperTrade.group_id == group_id,
                SayuPaperTrade.bot_id == bot_id,
                func.date(SayuPaperTrade.executed_at) == today,
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar() or 0)

    @classmethod
    @with_session
    async def count_today_by_code(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        today: date,
    ) -> Dict[str, int]:
        """返回 {stock_code: count}（今日每只股票加仓次数）"""
        stmt = (
            select(SayuPaperTrade.stock_code, func.count(SayuPaperTrade.id))
            .where(
                and_(
                    SayuPaperTrade.group_id == group_id,
                    SayuPaperTrade.bot_id == bot_id,
                    SayuPaperTrade.side == "buy",
                    func.date(SayuPaperTrade.executed_at) == today,
                )
            )
            .group_by(SayuPaperTrade.stock_code)
        )
        result = await session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    @classmethod
    @with_session
    async def aggregate_pnl(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        since: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """聚合已实现盈亏等指标"""
        stmt = select(
            func.coalesce(func.sum(SayuPaperTrade.realized_pnl), 0.0).label("total_pnl"),
            func.coalesce(func.sum(SayuPaperTrade.amount), 0.0).label("total_amount"),
            func.coalesce(func.sum(SayuPaperTrade.fee), 0.0).label("total_fee"),
            func.count(SayuPaperTrade.id).label("trade_count"),
        ).where(
            and_(
                SayuPaperTrade.group_id == group_id,
                SayuPaperTrade.bot_id == bot_id,
            )
        )
        if since:
            stmt = stmt.where(SayuPaperTrade.executed_at >= since)
        result = await session.execute(stmt)
        row = result.one()
        return {
            "total_pnl": float(row.total_pnl),
            "total_amount": float(row.total_amount),
            "total_fee": float(row.total_fee),
            "trade_count": int(row.trade_count),
        }


# ============================================================
# Decision Repo（append-only）
# ============================================================
class PaperDecisionRepo:
    @classmethod
    @with_session
    async def append(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        action: str,
        stock_code: Optional[str] = None,
        stock_name: Optional[str] = None,
        score: float = 0.0,
        reason: str = "",
        indicators: str = "",
        trade_id: Optional[int] = None,
        blocked_by: str = "",
    ) -> SayuPaperDecision:
        d = SayuPaperDecision(
            group_id=group_id,
            bot_id=bot_id,
            action=action,
            stock_code=stock_code,
            stock_name=stock_name,
            score=score,
            reason=reason,
            indicators=indicators,
            trade_id=trade_id,
            blocked_by=blocked_by,
        )
        session.add(d)
        await session.flush()
        return d

    @classmethod
    @with_session
    async def list_recent(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        limit: int = 50,
        stock_code: Optional[str] = None,
    ) -> List[SayuPaperDecision]:
        stmt = (
            select(SayuPaperDecision)
            .where(
                and_(
                    SayuPaperDecision.group_id == group_id,
                    SayuPaperDecision.bot_id == bot_id,
                )
            )
            .order_by(SayuPaperDecision.created_at.desc())
            .limit(limit)
        )
        if stock_code:
            stmt = stmt.where(SayuPaperDecision.stock_code == stock_code)
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ============================================================
# Snapshot Repo
# ============================================================
class PaperSnapshotRepo:
    @classmethod
    @with_session
    async def append(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        trade_date: date,
        cash: float,
        position_value: float,
        total_equity: float,
        day_pnl: float = 0.0,
        day_pnl_pct: float = 0.0,
        total_pnl: float = 0.0,
        total_pnl_pct: float = 0.0,
    ) -> SayuPaperSnapshot:
        snap = SayuPaperSnapshot(
            group_id=group_id,
            bot_id=bot_id,
            trade_date=trade_date,
            cash=cash,
            position_value=position_value,
            total_equity=total_equity,
            day_pnl=day_pnl,
            day_pnl_pct=day_pnl_pct,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
        )
        session.add(snap)
        await session.flush()
        return snap

    @classmethod
    @with_session
    async def latest(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> Optional[SayuPaperSnapshot]:
        stmt = (
            select(SayuPaperSnapshot)
            .where(
                and_(
                    SayuPaperSnapshot.group_id == group_id,
                    SayuPaperSnapshot.bot_id == bot_id,
                )
            )
            .order_by(SayuPaperSnapshot.trade_date.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def list_range(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        since: Optional[date] = None,
    ) -> List[SayuPaperSnapshot]:
        stmt = (
            select(SayuPaperSnapshot)
            .where(
                and_(
                    SayuPaperSnapshot.group_id == group_id,
                    SayuPaperSnapshot.bot_id == bot_id,
                )
            )
            .order_by(SayuPaperSnapshot.trade_date.asc())
        )
        if since:
            stmt = stmt.where(SayuPaperSnapshot.trade_date >= since)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_latest_all_groups(
        cls, session: AsyncSession, limit: int = 20
    ) -> List[SayuPaperSnapshot]:
        """跨群排行：返回每个群最新一条快照 + total_pnl_pct。"""
        # SQL: 取每组 (group_id, bot_id) 最新 trade_date 那一行
        from sqlalchemy import distinct
        subq = (
            select(
                SayuPaperSnapshot.group_id,
                SayuPaperSnapshot.bot_id,
                func.max(SayuPaperSnapshot.trade_date).label("max_date"),
            )
            .group_by(SayuPaperSnapshot.group_id, SayuPaperSnapshot.bot_id)
            .subquery()
        )
        stmt = (
            select(SayuPaperSnapshot)
            .join(
                subq,
                and_(
                    SayuPaperSnapshot.group_id == subq.c.group_id,
                    SayuPaperSnapshot.bot_id == subq.c.bot_id,
                    SayuPaperSnapshot.trade_date == subq.c.max_date,
                ),
            )
            .order_by(SayuPaperSnapshot.total_pnl_pct.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ============================================================
# Watchlist Repo（公开）
# ============================================================
class PaperWatchlistRepo:
    @classmethod
    @with_session
    async def add(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        user_id: str,
        stock_code: str,
        stock_name: str = "",
        secid: str = "",
        note: str = "",
    ) -> SayuPaperWatchlist:
        # 同一群同一股票已存在则覆盖（last writer wins）
        existing = await cls._get_by_code(session, group_id, bot_id, stock_code)
        if existing:
            existing.user_id = user_id
            existing.stock_name = stock_name
            existing.secid = secid
            existing.note = note
            session.add(existing)
            await session.flush()
            return existing
        item = SayuPaperWatchlist(
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
            stock_code=stock_code,
            stock_name=stock_name,
            secid=secid,
            note=note,
        )
        session.add(item)
        await session.flush()
        return item

    @classmethod
    @with_session
    async def _get_by_code(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
    ) -> Optional[SayuPaperWatchlist]:
        stmt = select(SayuPaperWatchlist).where(
            and_(
                SayuPaperWatchlist.group_id == group_id,
                SayuPaperWatchlist.bot_id == bot_id,
                SayuPaperWatchlist.stock_code == stock_code,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def remove(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
    ) -> bool:
        item = await cls._get_by_code(session, group_id, bot_id, stock_code)
        if not item:
            return False
        await session.delete(item)
        await session.flush()
        return True

    @classmethod
    @with_session
    async def list_by_account(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> List[SayuPaperWatchlist]:
        stmt = (
            select(SayuPaperWatchlist)
            .where(
                and_(
                    SayuPaperWatchlist.group_id == group_id,
                    SayuPaperWatchlist.bot_id == bot_id,
                )
            )
            .order_by(SayuPaperWatchlist.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_codes(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> List[str]:
        stmt = select(SayuPaperWatchlist.stock_code).where(
            and_(
                SayuPaperWatchlist.group_id == group_id,
                SayuPaperWatchlist.bot_id == bot_id,
            )
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]


# ============================================================
# AgentPool Repo（AI 私有）
# ============================================================
class PaperAgentPoolRepo:
    @classmethod
    @with_session
    async def get(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
    ) -> Optional[SayuPaperAgentPool]:
        stmt = select(SayuPaperAgentPool).where(
            and_(
                SayuPaperAgentPool.group_id == group_id,
                SayuPaperAgentPool.bot_id == bot_id,
                SayuPaperAgentPool.stock_code == stock_code,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def upsert(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str = "",
        secid: str = "",
        reason: str = "",
        added_by: str = "ai",
        priority: int = 0,
        expires_at: Optional[datetime] = None,
    ) -> SayuPaperAgentPool:
        existing = await cls.get(group_id, bot_id, stock_code)
        if existing:
            existing.stock_name = stock_name
            existing.secid = secid
            existing.reason = reason
            existing.priority = priority
            existing.expires_at = expires_at
            session.add(existing)
            await session.flush()
            return existing
        item = SayuPaperAgentPool(
            group_id=group_id,
            bot_id=bot_id,
            stock_code=stock_code,
            stock_name=stock_name,
            secid=secid,
            reason=reason,
            added_by=added_by,
            priority=priority,
            expires_at=expires_at,
        )
        session.add(item)
        await session.flush()
        return item

    @classmethod
    @with_session
    async def remove(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
    ) -> bool:
        item = await cls.get(group_id, bot_id, stock_code)
        if not item:
            return False
        await session.delete(item)
        await session.flush()
        return True

    @classmethod
    @with_session
    async def list_codes(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> List[str]:
        """列出非过期的 AI 内部池股票代码"""
        now = datetime.now()
        stmt = select(SayuPaperAgentPool.stock_code).where(
            and_(
                SayuPaperAgentPool.group_id == group_id,
                SayuPaperAgentPool.bot_id == bot_id,
                # 未过期或无过期时间
                (SayuPaperAgentPool.expires_at.is_(None)) | (SayuPaperAgentPool.expires_at > now),
            )
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    @classmethod
    @with_session
    async def cleanup_expired(
        cls, session: AsyncSession
    ) -> int:
        """清理过期项；返回删除条数"""
        now = datetime.now()
        stmt = select(SayuPaperAgentPool).where(
            and_(
                SayuPaperAgentPool.expires_at.is_not(None),
                SayuPaperAgentPool.expires_at <= now,
            )
        )
        result = await session.execute(stmt)
        items = list(result.scalars().all())
        for it in items:
            await session.delete(it)
        await session.flush()
        return len(items)
