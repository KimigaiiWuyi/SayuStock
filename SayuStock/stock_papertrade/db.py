"""模拟盘数据库 Repo 层（7 张表 CRUD 包装）。

所有方法走 ``@with_session`` 自动管理事务；返回 list / instance / None。
复杂聚合查询用 ``async_maker`` 手写 session。
"""

from typing import Any, Dict, List, Optional
from datetime import date, datetime

from sqlmodel import col
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.utils.database.base_models import with_session

from ..utils.database.papertrade_models import (
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperDecision,
    SayuPaperPosition,
    SayuPaperSnapshot,
    SayuPaperAgentPool,
    SayuPaperWatchlist,
)


# ============================================================
# Account Repo
# ============================================================
class PaperAccountRepo:
    # update() 的字段白名单 — 替代 hasattr/setattr 兜底（§17 红线）
    _UPDATABLE_FIELDS: frozenset[str] = frozenset(
        {
            "cash",
            "principal",
            "mode",
            "frequency_minutes",
            "enabled",
            "kanban_init_root_id",
            "kanban_period_root_id",
            "last_decided_at",
        }
    )

    @classmethod
    @with_session
    async def get(cls, session: AsyncSession, group_id: str, bot_id: str) -> Optional[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).where(
            and_(
                col(col(SayuPaperAccount.group_id)) == group_id,
                col(col(SayuPaperAccount.bot_id)) == bot_id,
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
        # 仅白名单字段可写 — 既保护业务不被乱改，又过 §17 hasatter 自省
        for k, v in fields.items():
            if k in cls._UPDATABLE_FIELDS:
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
        """原地增减现金；不做 realized_pnl 写入，principal 由 sell 路径单独维护。"""
        acc = await cls.get(group_id, bot_id)
        if not acc:
            return None
        acc.cash += delta
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def list_enabled(cls, session: AsyncSession) -> List[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).where(col(col(SayuPaperAccount.enabled)) == 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_all(cls, session: AsyncSession) -> List[SayuPaperAccount]:
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
                    col(col(SayuPaperPosition.group_id)) == group_id,
                    col(col(SayuPaperPosition.bot_id)) == bot_id,
                )
            )
        )
        deleted["position"] = int(r.rowcount or 0)

        # 2. 交易流水
        r = await session.execute(
            delete(SayuPaperTrade).where(
                and_(
                    col(col(SayuPaperTrade.group_id)) == group_id,
                    col(col(SayuPaperTrade.bot_id)) == bot_id,
                )
            )
        )
        deleted["trade"] = int(r.rowcount or 0)

        # 3. 决策日志
        r = await session.execute(
            delete(SayuPaperDecision).where(
                and_(
                    col(col(SayuPaperDecision.group_id)) == group_id,
                    col(col(SayuPaperDecision.bot_id)) == bot_id,
                )
            )
        )
        deleted["decision"] = int(r.rowcount or 0)

        # 4. 每日净值
        r = await session.execute(
            delete(SayuPaperSnapshot).where(
                and_(
                    col(col(SayuPaperSnapshot.group_id)) == group_id,
                    col(col(SayuPaperSnapshot.bot_id)) == bot_id,
                )
            )
        )
        deleted["snapshot"] = int(r.rowcount or 0)

        # 5. 群友关注列表
        r = await session.execute(
            delete(SayuPaperWatchlist).where(
                and_(
                    col(col(SayuPaperWatchlist.group_id)) == group_id,
                    col(col(SayuPaperWatchlist.bot_id)) == bot_id,
                )
            )
        )
        deleted["watchlist"] = int(r.rowcount or 0)

        # 6. AI 内部决策池
        r = await session.execute(
            delete(SayuPaperAgentPool).where(
                and_(
                    col(col(SayuPaperAgentPool.group_id)) == group_id,
                    col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                )
            )
        )
        deleted["agent_pool"] = int(r.rowcount or 0)

        # 7. 账户本身
        r = await session.execute(
            delete(SayuPaperAccount).where(
                and_(
                    col(col(SayuPaperAccount.group_id)) == group_id,
                    col(col(SayuPaperAccount.bot_id)) == bot_id,
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
                col(col(SayuPaperPosition.group_id)) == group_id,
                col(col(SayuPaperPosition.bot_id)) == bot_id,
                col(col(SayuPaperPosition.stock_code)) == stock_code,
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
                    col(col(SayuPaperPosition.group_id)) == group_id,
                    col(col(SayuPaperPosition.bot_id)) == bot_id,
                    col(col(SayuPaperPosition.qty)) > 0,
                )
            )
            .order_by(col(col(SayuPaperPosition.updated_at)).desc())
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
        stmt = select(col(SayuPaperPosition.stock_code)).where(
            and_(
                col(col(SayuPaperPosition.group_id)) == group_id,
                col(col(SayuPaperPosition.bot_id)) == bot_id,
                col(col(SayuPaperPosition.qty)) > 0,
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
        *,
        last_quote_price: Optional[float] = None,
        last_quote_at: Optional[datetime] = None,
    ) -> Optional[SayuPaperPosition]:
        """新建或更新持仓。qty=0 时删除持仓记录。

        ``last_quote_price`` / ``last_quote_at``（2026-07-01 新增）：让决策代理在
        买入/卖出撮合时把当前 quote 一起落库，省一次单独的报价写回 round-trip；
        留 None 时不覆盖已有值（保留历史的报价）。

        qty=0 分支直接走 DELETE 走当前 session，避开跨会话的 detached instance —
        原写法用 ``await cls.get(...).session.delete(existing)`` 会在外层
        session 上对来自内层 session 的对象执行 delete，依赖 SQLAlchemy 按 PK
        重新 fetch，单测看似能过但行为未定义。这里改用 ``session.execute``。
        """
        if qty <= 0:
            from sqlalchemy import delete as _sa_delete

            stmt = _sa_delete(SayuPaperPosition).where(
                and_(
                    col(col(SayuPaperPosition.group_id)) == group_id,
                    col(col(SayuPaperPosition.bot_id)) == bot_id,
                    col(col(SayuPaperPosition.stock_code)) == stock_code,
                )
            )
            await session.execute(stmt)
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
            if last_quote_price is not None:
                existing.last_quote_price = last_quote_price
                existing.last_quote_at = last_quote_at or now
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
            last_quote_price=last_quote_price,
            last_quote_at=last_quote_at or now if last_quote_price is not None else None,
            opened_at=now,
            updated_at=now,
        )
        session.add(pos)
        await session.flush()
        return pos

    @classmethod
    @with_session
    async def bulk_set_quote(
        cls,
        session: AsyncSession,
        quotes: List[Dict[str, Any]],
        group_id: str,
        bot_id: str,
    ) -> int:
        """批量写报价。``quotes`` 形如 ``[{stock_code, price, at}, ...]``。

        用于 ``quote_service.get_quotes_batch`` 一次拉多只股票后批量落库。
        实现上仍是逐条 ``UPDATE``（同一 ``session`` 内），不是单条合并 SQL，
        但共享一次 ``flush``，比调用方各自开 session 逐条提交更省。

        Returns:
            受影响总行数；调用方不强制使用。
        """
        from sqlalchemy import update as _sa_update

        affected: int = 0
        for q in quotes:
            code = q.get("stock_code")
            price = q.get("price")
            at = q.get("at")
            if not code or price is None:
                continue
            stmt = _sa_update(SayuPaperPosition).where(
                and_(
                    col(col(SayuPaperPosition.group_id)) == group_id,
                    col(col(SayuPaperPosition.bot_id)) == bot_id,
                    col(col(SayuPaperPosition.stock_code)) == code,
                )
            ).values(last_quote_price=price, last_quote_at=at)
            result = await session.execute(stmt)
            affected += int(result.rowcount or 0)
        await session.flush()
        return affected


# ============================================================
# Trade Repo（append-only）
# ============================================================
class PaperTradeRepo:
    @classmethod
    @with_session
    async def locked_qty_today(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
        today: Optional[date] = None,
    ) -> int:
        """查某股票今日已买入数量（A 股 T+1 锁定股数）。

        这是 A 股 T+1 结算的核心：在 T 日买入的股数，到 T+1 日开盘前都不能卖。
        "今天"按调用方传入的 ``today`` 决定（避免在工具里掺入隐式时区），缺省
        用系统 ``date.today()``。返回 ``>=0``。

        实现：``SELECT SUM(qty) FROM sayupapertrade WHERE side='buy' AND
        DATE(executed_at)=today AND group_id=? AND bot_id=? AND stock_code=?``。
        """
        if today is None:
            today = date.today()
        stmt = select(func.coalesce(func.sum(SayuPaperTrade.qty), 0)).where(
            and_(
                col(col(SayuPaperTrade.group_id)) == group_id,
                col(col(SayuPaperTrade.bot_id)) == bot_id,
                col(col(SayuPaperTrade.stock_code)) == stock_code,
                col(col(SayuPaperTrade.side)) == "buy",
                func.date(col(col(SayuPaperTrade.executed_at))) == today,
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar_one() or 0)

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
    async def append_with_cash_update(
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
        """原子地：写 trade 行 + 调整账户 cash + sell 时累计 principal。

        与 ``append`` 的区别：本方法在同一 session 内把 trade 流水与 account 现金绑定，
        避免 LLM 调 ``append`` 后忘记调 ``PaperAccountRepo.update_cash`` 导致
        trade 行跟 cash 自相矛盾。``with_session`` wrapper 在 commit 时一并持久化，
        若中间任何一步抛错会自动回滚，不会出现"trade 入表但 cash 没动"的脏状态。

        Args:
            side: ``buy`` → cash -= (amount + fee)，principal 不变；``sell`` →
                cash += (amount - fee) + realized_pnl，principal += realized_pnl。
                之所以 sell 时 cash 同时加上 realized_pnl，是因为前次 buy 已经
                把 amount 当作现金流出扣过（cash -= amount + fee_total_buy），
                现在 sell 回款只 + (amount - fee)，差额 (p) 自然体现在 cash 上；
                此处 + realized_pnl 是把"已实现盈亏"在 cash 上同一笔交易内闭环。

        Returns:
            SayuPaperTrade: 已 flush 的 trade 行（含 id）。

        Raises:
            ValueError: side 非法。
            RuntimeError: 该 (group_id, bot_id) 找不到 account（说明 setup_agent
                没跑 / 账户被删）。
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"side 非法: {side!r}（期望 buy 或 sell）")

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

        # 在同一 session 内查 account 并调整 cash / principal
        acc_stmt = select(SayuPaperAccount).where(
            and_(
                col(col(SayuPaperAccount.group_id)) == group_id,
                col(col(SayuPaperAccount.bot_id)) == bot_id,
            )
        )
        result = await session.execute(acc_stmt)
        acc: Optional[SayuPaperAccount] = result.scalar_one_or_none()
        if acc is None:
            raise RuntimeError(
                f"SayuPaperAccount 不存在 (group={group_id}, bot={bot_id})；"
                f"请先调 papertrade_account_create 建账户"
            )

        if side == "buy":
            # buy：现金要付出 amount + fee
            acc.cash -= (amount + fee)
        else:  # sell
            # sell：现金回 amount - fee；principal 累计 realized_pnl
            acc.cash += (amount - fee + realized_pnl)
            acc.principal += realized_pnl
            # last_decided_at 由 decision_insert 维护，这里不强写

        session.add(acc)
        # 同时把 account.last_decided_at 标记一下（避免又开新 session）
        acc.last_decided_at = datetime.now()
        session.add(acc)
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
                    col(col(SayuPaperTrade.group_id)) == group_id,
                    col(col(SayuPaperTrade.bot_id)) == bot_id,
                )
            )
            .order_by(col(col(SayuPaperTrade.executed_at)).desc())
            .limit(limit)
        )
        if stock_code:
            stmt = stmt.where(col(col(SayuPaperTrade.stock_code)) == stock_code)
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
        stmt = select(func.count(col(SayuPaperTrade.id))).where(
            and_(
                col(col(SayuPaperTrade.group_id)) == group_id,
                col(col(SayuPaperTrade.bot_id)) == bot_id,
                func.date(col(SayuPaperTrade.executed_at)) == today,
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
        """返回 {stock_code: count}（今日每只股票加仓次数）。

        GROUP BY 必须包含所有非聚合列，否则 PG 在严格模式下会报错
        （SQLite/MySQL 会自动扩展）。本 SQL 已按 (group_id, bot_id, stock_code)
        三列分组，跨方言都安全。
        """
        stmt = (
            select(col(SayuPaperTrade.stock_code), func.count(col(SayuPaperTrade.id)))
            .where(
                and_(
                    col(col(SayuPaperTrade.group_id)) == group_id,
                    col(col(SayuPaperTrade.bot_id)) == bot_id,
                    col(col(SayuPaperTrade.side)) == "buy",
                    func.date(col(SayuPaperTrade.executed_at)) == today,
                )
            )
            .group_by(
                col(SayuPaperTrade.group_id),
                col(SayuPaperTrade.bot_id),
                col(SayuPaperTrade.stock_code),
            )
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
        """聚合已实现盈亏等指标。无成交时全返回 0。"""
        stmt = select(
            func.coalesce(func.sum(col(SayuPaperTrade.realized_pnl)), 0.0).label("total_pnl"),
            func.coalesce(func.sum(col(SayuPaperTrade.amount)), 0.0).label("total_amount"),
            func.coalesce(func.sum(col(SayuPaperTrade.fee)), 0.0).label("total_fee"),
            func.coalesce(func.count(col(SayuPaperTrade.id)), 0).label("trade_count"),
        ).where(
            and_(
                col(col(SayuPaperTrade.group_id)) == group_id,
                col(col(SayuPaperTrade.bot_id)) == bot_id,
            )
        )
        if since:
            stmt = stmt.where(col(col(SayuPaperTrade.executed_at)) >= since)
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
                    col(col(SayuPaperDecision.group_id)) == group_id,
                    col(col(SayuPaperDecision.bot_id)) == bot_id,
                )
            )
            .order_by(col(col(SayuPaperDecision.created_at)).desc())
            .limit(limit)
        )
        if stock_code:
            stmt = stmt.where(col(col(SayuPaperDecision.stock_code)) == stock_code)
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
                    col(col(SayuPaperSnapshot.group_id)) == group_id,
                    col(col(SayuPaperSnapshot.bot_id)) == bot_id,
                )
            )
            .order_by(col(col(SayuPaperSnapshot.trade_date)).desc())
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
                    col(col(SayuPaperSnapshot.group_id)) == group_id,
                    col(col(SayuPaperSnapshot.bot_id)) == bot_id,
                )
            )
            .order_by(col(col(SayuPaperSnapshot.trade_date)).asc())
        )
        if since:
            stmt = stmt.where(col(col(SayuPaperSnapshot.trade_date)) >= since)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def prev_before(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        trade_date: date,
    ) -> Optional[SayuPaperSnapshot]:
        """取 ``trade_date`` **之前**最近的一条快照（用于算 day_pnl 的基准）。"""
        stmt = (
            select(SayuPaperSnapshot)
            .where(
                and_(
                    col(col(SayuPaperSnapshot.group_id)) == group_id,
                    col(col(SayuPaperSnapshot.bot_id)) == bot_id,
                    col(col(SayuPaperSnapshot.trade_date)) < trade_date,
                )
            )
            .order_by(col(col(SayuPaperSnapshot.trade_date)).desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def upsert_for_date(
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
        """按 ``(group_id, bot_id, trade_date)`` 幂等写快照：已存在则更新，否则新建。

        表本身是 append-only（无唯一约束），同一天收盘快照若重跑一次会产生重复行；
        这里先查当天行，命中就原地更新，避免排行/复盘取到重复日的净值。
        """
        stmt = (
            select(SayuPaperSnapshot)
            .where(
                and_(
                    col(col(SayuPaperSnapshot.group_id)) == group_id,
                    col(col(SayuPaperSnapshot.bot_id)) == bot_id,
                    col(col(SayuPaperSnapshot.trade_date)) == trade_date,
                )
            )
            .limit(1)
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.cash = cash
            existing.position_value = position_value
            existing.total_equity = total_equity
            existing.day_pnl = day_pnl
            existing.day_pnl_pct = day_pnl_pct
            existing.total_pnl = total_pnl
            existing.total_pnl_pct = total_pnl_pct
            existing.created_at = datetime.now()
            session.add(existing)
            await session.flush()
            return existing
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
    async def list_latest_all_groups(cls, session: AsyncSession, limit: int = 20) -> List[SayuPaperSnapshot]:
        """跨群排行：返回每个群最新一条快照 + total_pnl_pct。"""
        # SQL: 取每组 (group_id, bot_id) 最新 trade_date 那一行
        subq = (
            select(
                col(SayuPaperSnapshot.group_id),
                col(SayuPaperSnapshot.bot_id),
                func.max(col(SayuPaperSnapshot.trade_date)).label("max_date"),
            )
            .group_by(col(SayuPaperSnapshot.group_id), col(SayuPaperSnapshot.bot_id))
            .subquery()
        )
        stmt = (
            select(SayuPaperSnapshot)
            .join(
                subq,
                and_(
                    col(col(SayuPaperSnapshot.group_id)) == subq.c.group_id,
                    col(col(SayuPaperSnapshot.bot_id)) == subq.c.bot_id,
                    col(col(SayuPaperSnapshot.trade_date)) == subq.c.max_date,
                ),
            )
            .order_by(col(col(SayuPaperSnapshot.total_pnl_pct)).desc())
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
        # 注意：lookup 必须走本方法的 session；不能跨会话调用带 @with_session 的 helper
        stmt = select(SayuPaperWatchlist).where(
            and_(
                col(col(SayuPaperWatchlist.group_id)) == group_id,
                col(col(SayuPaperWatchlist.bot_id)) == bot_id,
                col(col(SayuPaperWatchlist.stock_code)) == stock_code,
            )
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
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
    async def remove(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
        stock_code: str,
    ) -> bool:
        from sqlalchemy import delete as _sa_delete

        stmt = _sa_delete(SayuPaperWatchlist).where(
            and_(
                col(col(SayuPaperWatchlist.group_id)) == group_id,
                col(col(SayuPaperWatchlist.bot_id)) == bot_id,
                col(col(SayuPaperWatchlist.stock_code)) == stock_code,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) > 0

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
                    col(col(SayuPaperWatchlist.group_id)) == group_id,
                    col(col(SayuPaperWatchlist.bot_id)) == bot_id,
                )
            )
            .order_by(col(col(SayuPaperWatchlist.created_at)).desc())
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
        stmt = select(col(SayuPaperWatchlist.stock_code)).where(
            and_(
                col(col(SayuPaperWatchlist.group_id)) == group_id,
                col(col(SayuPaperWatchlist.bot_id)) == bot_id,
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
                col(col(SayuPaperAgentPool.group_id)) == group_id,
                col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                col(col(SayuPaperAgentPool.stock_code)) == stock_code,
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
        # lookup 走本方法的 session，避免跨 @with_session 调用时 wrapper 把 session 当 cls
        stmt = select(SayuPaperAgentPool).where(
            and_(
                col(col(SayuPaperAgentPool.group_id)) == group_id,
                col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                col(col(SayuPaperAgentPool.stock_code)) == stock_code,
            )
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
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
        from sqlalchemy import delete as _sa_delete

        stmt = _sa_delete(SayuPaperAgentPool).where(
            and_(
                col(col(SayuPaperAgentPool.group_id)) == group_id,
                col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                col(col(SayuPaperAgentPool.stock_code)) == stock_code,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) > 0

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
        stmt = select(col(SayuPaperAgentPool.stock_code)).where(
            and_(
                col(col(SayuPaperAgentPool.group_id)) == group_id,
                col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                # 未过期或无过期时间
                (col(col(SayuPaperAgentPool.expires_at)).is_(None)) | (col(col(SayuPaperAgentPool.expires_at)) > now),
            )
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    @classmethod
    @with_session
    async def list_by_account(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> List[SayuPaperAgentPool]:
        """列出非过期的 AI 内部池全量条目（含 name / priority / expires_at）。"""
        now = datetime.now()
        stmt = (
            select(SayuPaperAgentPool)
            .where(
                and_(
                    col(col(SayuPaperAgentPool.group_id)) == group_id,
                    col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                    (col(col(SayuPaperAgentPool.expires_at)).is_(None))
                    | (col(col(SayuPaperAgentPool.expires_at)) > now),
                )
            )
            .order_by(col(col(SayuPaperAgentPool.priority)).desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def cleanup_expired(cls, session: AsyncSession) -> int:
        """清理过期项（全库）；返回删除条数"""
        from sqlalchemy import delete as _sa_delete

        now = datetime.now()
        stmt = _sa_delete(SayuPaperAgentPool).where(
            and_(
                col(col(SayuPaperAgentPool.expires_at)).is_not(None),
                col(col(SayuPaperAgentPool.expires_at)) <= now,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount or 0)

    @classmethod
    @with_session
    async def cleanup_expired_for(
        cls,
        session: AsyncSession,
        group_id: str,
        bot_id: str,
    ) -> int:
        """物理删除本账户下已过期的候选（refresh 每轮先调，让轮换真正腾出空间）。

        list_codes/list_by_account 只在读时过滤过期行，行仍留库；轮换逻辑要按
        created_at 排序淘汰最旧 auto 候选，必须先把过期行删掉再统计，否则计数偏高。
        """
        from sqlalchemy import delete as _sa_delete

        now = datetime.now()
        stmt = _sa_delete(SayuPaperAgentPool).where(
            and_(
                col(col(SayuPaperAgentPool.group_id)) == group_id,
                col(col(SayuPaperAgentPool.bot_id)) == bot_id,
                col(col(SayuPaperAgentPool.expires_at)).is_not(None),
                col(col(SayuPaperAgentPool.expires_at)) <= now,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount or 0)
