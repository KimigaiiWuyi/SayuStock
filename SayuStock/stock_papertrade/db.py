"""模拟盘数据库 Repo 层（8 张表 CRUD 包装）。

**账本主键是 ``account_id``**：模拟盘是"命名的盘"，不是"群的财产"。所有子表
的读写都按 ``account_id`` 过滤；``group_id`` / ``bot_id`` 仍然双写（等于账户的
origin），只为人肉查库时能看出这条流水属于哪个盘的原群，业务读路径**不许**用它们。

所有方法走 ``@with_session`` 自动管理事务；返回 list / instance / None。
"""

import json
from typing import Any, Dict, List, Optional
from datetime import date, datetime

from sqlmodel import col
from sqlalchemy import or_, and_, func, select
from sqlalchemy.engine import Result, CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.utils.database.base_models import with_session

from ..utils.database.papertrade_models import (
    DEFAULT_STRATEGY_ID,
    DEFAULT_ACCOUNT_NAME,
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperDecision,
    SayuPaperPosition,
    SayuPaperSnapshot,
    SayuPaperAgentPool,
    SayuPaperWatchlist,
    SayuPaperBroadcastTarget,
)


def _rowcount(result: Result[Any]) -> int:
    # execute() 静态返回 Result（无 rowcount），DML 运行时实为 CursorResult
    return result.rowcount if isinstance(result, CursorResult) else 0


# ============================================================
# Account Repo
# ============================================================
class PaperAccountRepo:
    # update() 的字段白名单 — 替代 hasattr/setattr 兜底（§17 红线）
    # name 刻意不在其中：改名走 rename()，那里做唯一性校验。
    _UPDATABLE_FIELDS: frozenset[str] = frozenset(
        {
            "cash",
            "principal",
            "mode",
            "strategy_id",
            "strategy_params",
            "frequency_minutes",
            "enabled",
            "kanban_init_root_id",
            "kanban_period_root_id",
            "last_decided_at",
        }
    )

    @classmethod
    @with_session
    async def get_by_id(cls, session: AsyncSession, account_id: int) -> Optional[SayuPaperAccount]:
        if account_id <= 0:
            return None
        stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.id) == account_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_by_name(cls, session: AsyncSession, name: str) -> Optional[SayuPaperAccount]:
        """精确匹配盘名（已 strip）。空名返回 None。"""
        key = (name or "").strip()
        if not key:
            return None
        stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.name) == key)
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def get_by_kanban_root(cls, session: AsyncSession, root_task_id: str) -> Optional[SayuPaperAccount]:
        """按 Kanban 根任务 ID 反查账户 —— **写工具唯一的账户解析入口**。

        写路径不能信 LLM 传的盘名：拼错一个字轻则整轮心跳被 ``deny_write_reason``
        拒到空转，重则把成交写进别的盘。而 ``root_task_id`` 是框架派发任务时注入
        的，LLM 无法伪造，且与 ``deny_write_reason`` 查的是同一份数据 —— 解析成功
        必然鉴权通过，不会出现"解析到 A、鉴权按 B"的裂缝。
        """
        key = (root_task_id or "").strip()
        if not key:
            return None
        stmt = select(SayuPaperAccount).where(
            or_(
                col(SayuPaperAccount.kanban_init_root_id) == key,
                col(SayuPaperAccount.kanban_period_root_id) == key,
            )
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def search(cls, session: AsyncSession, keyword: str) -> List[SayuPaperAccount]:
        """盘名包含匹配（精确匹配请用 ``get_by_name``）。"""
        key = (keyword or "").strip()
        if not key:
            return []
        stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.name).contains(key))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_by_origin_group(cls, session: AsyncSession, group_id: str) -> List[SayuPaperAccount]:
        """按创建原群查盘（兼容「模拟盘查询 <旧gid>」）。"""
        key = (group_id or "").strip()
        if not key:
            return []
        stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.group_id) == key)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def create(
        cls,
        session: AsyncSession,
        name: str,
        group_id: str,
        bot_id: str,
        *,
        strategy_id: str = DEFAULT_STRATEGY_ID,
        strategy_params: str = "{}",
        initial_cash: float = 1_000_000.0,
        mode: str = "balanced",
        initialized_by: Optional[str] = None,
    ) -> SayuPaperAccount:
        """建一个新盘。调用方须先用 ``get_by_name`` 查重（这里不再查，避免双查）。"""
        now = datetime.now()
        acc = SayuPaperAccount(
            name=name.strip(),
            strategy_id=strategy_id,
            strategy_params=strategy_params or "{}",
            group_id=group_id,
            bot_id=bot_id,
            cash=initial_cash,
            initial_cash=initial_cash,
            principal=initial_cash,
            mode=mode,
            frequency_minutes=30,
            enabled=1,
            schema_migrated_v2=1,
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
        account_id: int,
        **fields: Any,
    ) -> Optional[SayuPaperAccount]:
        acc = await cls.get_by_id(account_id)
        if not acc:
            return None
        # 仅白名单字段可写 — 既保护业务不被乱改，又过 §17 hasattr 自省
        for k, v in fields.items():
            if k in cls._UPDATABLE_FIELDS:
                setattr(acc, k, v)
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def rename(cls, session: AsyncSession, account_id: int, new_name: str) -> Optional[SayuPaperAccount]:
        """改名。调用方须先查重；这里只做 strip + 落库。"""
        acc = await cls.get_by_id(account_id)
        if not acc:
            return None
        acc.name = new_name.strip()
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def update_cash(
        cls,
        session: AsyncSession,
        account_id: int,
        delta: float,
    ) -> Optional[SayuPaperAccount]:
        """原地增减现金；不做 realized_pnl 写入，principal 由 sell 路径单独维护。"""
        acc = await cls.get_by_id(account_id)
        if not acc:
            return None
        acc.cash += delta
        session.add(acc)
        await session.flush()
        return acc

    @classmethod
    @with_session
    async def get_earliest(cls, session: AsyncSession) -> Optional[SayuPaperAccount]:
        """全库最早建的那个账户。

        多账户改造后不再用于"钉死全局账户键"，只保留给迁移路径挑默认盘、
        以及"库里只有一个盘时的兜底解析"。

        用 created_at asc + id asc 双排序：老库 created_at 可能为 NULL，
        单靠它排序在各方言下 NULL 的位置不一致，id 兜底保证结果稳定唯一。
        """
        stmt = (
            select(SayuPaperAccount)
            .order_by(
                col(SayuPaperAccount.created_at).asc(),
                col(SayuPaperAccount.id).asc(),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def list_enabled(cls, session: AsyncSession) -> List[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.enabled) == 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_all(cls, session: AsyncSession) -> List[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).order_by(col(SayuPaperAccount.id).asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_by_strategy(cls, session: AsyncSession, strategy_id: str) -> List[SayuPaperAccount]:
        stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.strategy_id) == strategy_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def bind_kanban_init(
        cls,
        session: AsyncSession,
        account_id: int,
        root_id: str,
    ) -> None:
        acc = await cls.get_by_id(account_id)
        if acc:
            acc.kanban_init_root_id = root_id
            session.add(acc)
            await session.flush()

    @classmethod
    @with_session
    async def bind_kanban_period(
        cls,
        session: AsyncSession,
        account_id: int,
        root_id: str,
    ) -> None:
        acc = await cls.get_by_id(account_id)
        if acc:
            acc.kanban_period_root_id = root_id
            session.add(acc)
            await session.flush()

    @classmethod
    @with_session
    async def touch_decided(cls, session: AsyncSession, account_id: int) -> None:
        acc = await cls.get_by_id(account_id)
        if acc:
            acc.last_decided_at = datetime.now()
            session.add(acc)
            await session.flush()

    @classmethod
    @with_session
    async def reset_account(cls, session: AsyncSession, account_id: int) -> Dict[str, int]:
        """重置**指定盘**：清空账户 + 持仓 + 流水 + 决策 + 快照 + 内部池 + 关注 + 播报目标。

        多账户改造后按 ``account_id`` 删——绝不能再按 group_id 删，否则会把同一个
        群里另一个盘的数据一起清掉。

        Returns:
            {"account": 1, "position": N, "trade": N, ...} 各表删除条数
        """
        from sqlalchemy import delete

        deleted: Dict[str, int] = {}
        if account_id <= 0:
            return deleted

        child_specs: tuple[tuple[str, Any, Any], ...] = (
            ("position", SayuPaperPosition, SayuPaperPosition.account_id),
            ("trade", SayuPaperTrade, SayuPaperTrade.account_id),
            ("decision", SayuPaperDecision, SayuPaperDecision.account_id),
            ("snapshot", SayuPaperSnapshot, SayuPaperSnapshot.account_id),
            ("watchlist", SayuPaperWatchlist, SayuPaperWatchlist.account_id),
            ("agent_pool", SayuPaperAgentPool, SayuPaperAgentPool.account_id),
            # 播报目标必须一起删：孤儿目标会在 id 被复用时误播到别的群
            ("broadcast", SayuPaperBroadcastTarget, SayuPaperBroadcastTarget.account_id),
        )
        for label, model, account_col in child_specs:
            r = await session.execute(delete(model).where(col(account_col) == account_id))
            deleted[label] = _rowcount(r)

        r = await session.execute(delete(SayuPaperAccount).where(col(SayuPaperAccount.id) == account_id))
        deleted["account"] = _rowcount(r)
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
        account_id: int,
        stock_code: str,
    ) -> Optional[SayuPaperPosition]:
        stmt = select(SayuPaperPosition).where(
            and_(
                col(SayuPaperPosition.account_id) == account_id,
                col(SayuPaperPosition.stock_code) == stock_code,
            )
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def list_by_account(cls, session: AsyncSession, account_id: int) -> List[SayuPaperPosition]:
        stmt = (
            select(SayuPaperPosition)
            .where(
                and_(
                    col(SayuPaperPosition.account_id) == account_id,
                    col(SayuPaperPosition.qty) > 0,
                )
            )
            .order_by(col(SayuPaperPosition.updated_at).desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_codes(cls, session: AsyncSession, account_id: int) -> List[str]:
        stmt = select(col(SayuPaperPosition.stock_code)).where(
            and_(
                col(SayuPaperPosition.account_id) == account_id,
                col(SayuPaperPosition.qty) > 0,
            )
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    @classmethod
    @with_session
    async def upsert(
        cls,
        session: AsyncSession,
        account_id: int,
        stock_code: str,
        stock_name: str,
        secid: str,
        qty: int,
        avg_cost: float,
        *,
        group_id: str = "",
        bot_id: str = "",
        last_quote_price: Optional[float] = None,
        last_quote_at: Optional[datetime] = None,
    ) -> Optional[SayuPaperPosition]:
        """新建或更新持仓。qty=0 时删除持仓记录。

        ``last_quote_price`` / ``last_quote_at``：让决策代理在买入/卖出撮合时把
        当前 quote 一起落库，省一次单独的报价写回 round-trip；留 None 时不覆盖
        已有值（保留历史的报价）。

        qty=0 分支直接走 DELETE，避开跨会话的 detached instance。
        """
        if qty <= 0:
            from sqlalchemy import delete as _sa_delete

            stmt = _sa_delete(SayuPaperPosition).where(
                and_(
                    col(SayuPaperPosition.account_id) == account_id,
                    col(SayuPaperPosition.stock_code) == stock_code,
                )
            )
            await session.execute(stmt)
            await session.flush()
            return None
        existing = await cls.get(account_id, stock_code)
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
            account_id=account_id,
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
        account_id: int,
    ) -> int:
        """批量写报价。``quotes`` 形如 ``[{stock_code, price, at}, ...]``。

        用于 ``quote_service.get_quotes_batch`` 一次拉多只股票后批量落库。
        实现上仍是逐条 ``UPDATE``（同一 ``session`` 内），共享一次 ``flush``。

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
            stmt = (
                _sa_update(SayuPaperPosition)
                .where(
                    and_(
                        col(SayuPaperPosition.account_id) == account_id,
                        col(SayuPaperPosition.stock_code) == code,
                    )
                )
                .values(last_quote_price=price, last_quote_at=at)
            )
            result = await session.execute(stmt)
            affected += _rowcount(result)
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
        account_id: int,
        stock_code: str,
        today: Optional[date] = None,
    ) -> int:
        """查某股票今日已买入数量（A 股 T+1 锁定股数）。

        这是 A 股 T+1 结算的核心：在 T 日买入的股数，到 T+1 日开盘前都不能卖。
        "今天"按调用方传入的 ``today`` 决定（避免在工具里掺入隐式时区），缺省
        用系统 ``date.today()``。返回 ``>=0``。
        """
        if today is None:
            today = date.today()
        stmt = select(func.coalesce(func.sum(SayuPaperTrade.qty), 0)).where(
            and_(
                col(SayuPaperTrade.account_id) == account_id,
                col(SayuPaperTrade.stock_code) == stock_code,
                col(SayuPaperTrade.side) == "buy",
                func.date(col(SayuPaperTrade.executed_at)) == today,
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar_one() or 0)

    @classmethod
    @with_session
    async def append(
        cls,
        session: AsyncSession,
        account_id: int,
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
        group_id: str = "",
        bot_id: str = "",
    ) -> SayuPaperTrade:
        trade = SayuPaperTrade(
            account_id=account_id,
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
        account_id: int,
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
                现在 sell 回款只 + (amount - fee)，差额自然体现在 cash 上。

        Returns:
            SayuPaperTrade: 已 flush 的 trade 行（含 id）。

        Raises:
            ValueError: side 非法。
            RuntimeError: 该 account_id 找不到 account（账户被删 / 未初始化）。
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"side 非法: {side!r}（期望 buy 或 sell）")

        # 先取 account：既做存在性校验，又拿到 origin 用于双写
        acc_stmt = select(SayuPaperAccount).where(col(SayuPaperAccount.id) == account_id)
        acc: Optional[SayuPaperAccount] = (await session.execute(acc_stmt)).scalars().first()
        if acc is None:
            raise RuntimeError(f"SayuPaperAccount 不存在 (account_id={account_id})；请先创建模拟盘")

        trade = SayuPaperTrade(
            account_id=account_id,
            group_id=acc.group_id,
            bot_id=acc.bot_id,
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

        if side == "buy":
            # buy：现金要付出 amount + fee
            acc.cash -= amount + fee
        else:  # sell
            # sell：现金回 amount - fee；principal 累计 realized_pnl
            acc.cash += amount - fee + realized_pnl
            acc.principal += realized_pnl

        acc.last_decided_at = datetime.now()
        session.add(acc)
        await session.flush()
        return trade

    @classmethod
    @with_session
    async def list_by_account(
        cls,
        session: AsyncSession,
        account_id: int,
        limit: int = 50,
        stock_code: Optional[str] = None,
    ) -> List[SayuPaperTrade]:
        stmt = (
            select(SayuPaperTrade)
            .where(col(SayuPaperTrade.account_id) == account_id)
            .order_by(col(SayuPaperTrade.executed_at).desc())
            .limit(limit)
        )
        if stock_code:
            stmt = stmt.where(col(SayuPaperTrade.stock_code) == stock_code)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def count_today(
        cls,
        session: AsyncSession,
        account_id: int,
        today: date,
    ) -> int:
        stmt = select(func.count(col(SayuPaperTrade.id))).where(
            and_(
                col(SayuPaperTrade.account_id) == account_id,
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
        account_id: int,
        today: date,
    ) -> Dict[str, int]:
        """返回 {stock_code: count}（今日每只股票加仓次数）。

        GROUP BY 必须包含所有非聚合列，否则 PG 在严格模式下会报错
        （SQLite/MySQL 会自动扩展）。
        """
        stmt = (
            select(col(SayuPaperTrade.stock_code), func.count(col(SayuPaperTrade.id)))
            .where(
                and_(
                    col(SayuPaperTrade.account_id) == account_id,
                    col(SayuPaperTrade.side) == "buy",
                    func.date(col(SayuPaperTrade.executed_at)) == today,
                )
            )
            .group_by(
                col(SayuPaperTrade.account_id),
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
        account_id: int,
        since: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """聚合已实现盈亏等指标。无成交时全返回 0。"""
        stmt = select(
            func.coalesce(func.sum(col(SayuPaperTrade.realized_pnl)), 0.0).label("total_pnl"),
            func.coalesce(func.sum(col(SayuPaperTrade.amount)), 0.0).label("total_amount"),
            func.coalesce(func.sum(col(SayuPaperTrade.fee)), 0.0).label("total_fee"),
            func.coalesce(func.count(col(SayuPaperTrade.id)), 0).label("trade_count"),
        ).where(col(SayuPaperTrade.account_id) == account_id)
        if since:
            stmt = stmt.where(col(SayuPaperTrade.executed_at) >= since)
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
        account_id: int,
        action: str,
        stock_code: Optional[str] = None,
        stock_name: Optional[str] = None,
        score: float = 0.0,
        reason: str = "",
        indicators: str = "",
        trade_id: Optional[int] = None,
        blocked_by: str = "",
        group_id: str = "",
        bot_id: str = "",
    ) -> SayuPaperDecision:
        d = SayuPaperDecision(
            account_id=account_id,
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
        account_id: int,
        limit: int = 50,
        stock_code: Optional[str] = None,
    ) -> List[SayuPaperDecision]:
        stmt = (
            select(SayuPaperDecision)
            .where(col(SayuPaperDecision.account_id) == account_id)
            .order_by(col(SayuPaperDecision.created_at).desc())
            .limit(limit)
        )
        if stock_code:
            stmt = stmt.where(col(SayuPaperDecision.stock_code) == stock_code)
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
        account_id: int,
        trade_date: date,
        cash: float,
        position_value: float,
        total_equity: float,
        day_pnl: float = 0.0,
        day_pnl_pct: float = 0.0,
        total_pnl: float = 0.0,
        total_pnl_pct: float = 0.0,
        group_id: str = "",
        bot_id: str = "",
    ) -> SayuPaperSnapshot:
        snap = SayuPaperSnapshot(
            account_id=account_id,
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
    async def latest(cls, session: AsyncSession, account_id: int) -> Optional[SayuPaperSnapshot]:
        stmt = (
            select(SayuPaperSnapshot)
            .where(col(SayuPaperSnapshot.account_id) == account_id)
            .order_by(col(SayuPaperSnapshot.trade_date).desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def list_range(
        cls,
        session: AsyncSession,
        account_id: int,
        since: Optional[date] = None,
    ) -> List[SayuPaperSnapshot]:
        stmt = (
            select(SayuPaperSnapshot)
            .where(col(SayuPaperSnapshot.account_id) == account_id)
            .order_by(col(SayuPaperSnapshot.trade_date).asc())
        )
        if since:
            stmt = stmt.where(col(SayuPaperSnapshot.trade_date) >= since)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def prev_before(
        cls,
        session: AsyncSession,
        account_id: int,
        trade_date: date,
    ) -> Optional[SayuPaperSnapshot]:
        """取 ``trade_date`` **之前**最近的一条快照（用于算 day_pnl 的基准）。"""
        stmt = (
            select(SayuPaperSnapshot)
            .where(
                and_(
                    col(SayuPaperSnapshot.account_id) == account_id,
                    col(SayuPaperSnapshot.trade_date) < trade_date,
                )
            )
            .order_by(col(SayuPaperSnapshot.trade_date).desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def upsert_for_date(
        cls,
        session: AsyncSession,
        account_id: int,
        trade_date: date,
        cash: float,
        position_value: float,
        total_equity: float,
        day_pnl: float = 0.0,
        day_pnl_pct: float = 0.0,
        total_pnl: float = 0.0,
        total_pnl_pct: float = 0.0,
        group_id: str = "",
        bot_id: str = "",
    ) -> SayuPaperSnapshot:
        """按 ``(account_id, trade_date)`` 幂等写快照：已存在则更新，否则新建。

        表本身是 append-only（无唯一约束），同一天收盘快照若重跑一次会产生重复行；
        这里先查当天行，命中就原地更新，避免排行/复盘取到重复日的净值。
        """
        stmt = (
            select(SayuPaperSnapshot)
            .where(
                and_(
                    col(SayuPaperSnapshot.account_id) == account_id,
                    col(SayuPaperSnapshot.trade_date) == trade_date,
                )
            )
            .limit(1)
        )
        existing = (await session.execute(stmt)).scalars().first()
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
            account_id=account_id,
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
    async def list_latest_all_accounts(cls, session: AsyncSession, limit: int = 20) -> List[SayuPaperSnapshot]:
        """跨盘排行：返回每个盘最新一条快照 + total_pnl_pct。"""
        subq = (
            select(
                col(SayuPaperSnapshot.account_id),
                func.max(col(SayuPaperSnapshot.trade_date)).label("max_date"),
            )
            .where(col(SayuPaperSnapshot.account_id) > 0)
            .group_by(col(SayuPaperSnapshot.account_id))
            .subquery()
        )
        stmt = (
            select(SayuPaperSnapshot)
            .join(
                subq,
                and_(
                    col(SayuPaperSnapshot.account_id) == subq.c.account_id,
                    col(SayuPaperSnapshot.trade_date) == subq.c.max_date,
                ),
            )
            .order_by(col(SayuPaperSnapshot.total_pnl_pct).desc())
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
        account_id: int,
        user_id: str,
        stock_code: str,
        stock_name: str = "",
        secid: str = "",
        note: str = "",
        group_id: str = "",
        bot_id: str = "",
    ) -> SayuPaperWatchlist:
        # 同一盘同一股票已存在则覆盖（last writer wins）
        # 注意：lookup 必须走本方法的 session；不能跨会话调用带 @with_session 的 helper
        stmt = select(SayuPaperWatchlist).where(
            and_(
                col(SayuPaperWatchlist.account_id) == account_id,
                col(SayuPaperWatchlist.stock_code) == stock_code,
            )
        )
        result = await session.execute(stmt)
        existing = result.scalars().first()
        if existing:
            existing.user_id = user_id
            existing.stock_name = stock_name
            existing.secid = secid
            existing.note = note
            session.add(existing)
            await session.flush()
            return existing
        item = SayuPaperWatchlist(
            account_id=account_id,
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
    async def remove(cls, session: AsyncSession, account_id: int, stock_code: str) -> bool:
        from sqlalchemy import delete as _sa_delete

        stmt = _sa_delete(SayuPaperWatchlist).where(
            and_(
                col(SayuPaperWatchlist.account_id) == account_id,
                col(SayuPaperWatchlist.stock_code) == stock_code,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return _rowcount(result) > 0

    @classmethod
    @with_session
    async def list_by_account(cls, session: AsyncSession, account_id: int) -> List[SayuPaperWatchlist]:
        stmt = (
            select(SayuPaperWatchlist)
            .where(col(SayuPaperWatchlist.account_id) == account_id)
            .order_by(col(SayuPaperWatchlist.created_at).desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_codes(cls, session: AsyncSession, account_id: int) -> List[str]:
        stmt = select(col(SayuPaperWatchlist.stock_code)).where(col(SayuPaperWatchlist.account_id) == account_id)
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
        account_id: int,
        stock_code: str,
    ) -> Optional[SayuPaperAgentPool]:
        stmt = select(SayuPaperAgentPool).where(
            and_(
                col(SayuPaperAgentPool.account_id) == account_id,
                col(SayuPaperAgentPool.stock_code) == stock_code,
            )
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    @with_session
    async def upsert(
        cls,
        session: AsyncSession,
        account_id: int,
        stock_code: str,
        stock_name: str = "",
        secid: str = "",
        reason: str = "",
        added_by: str = "ai",
        priority: int = 0,
        expires_at: Optional[datetime] = None,
        group_id: str = "",
        bot_id: str = "",
    ) -> SayuPaperAgentPool:
        # lookup 走本方法的 session，避免跨 @with_session 调用时 wrapper 把 session 当 cls
        stmt = select(SayuPaperAgentPool).where(
            and_(
                col(SayuPaperAgentPool.account_id) == account_id,
                col(SayuPaperAgentPool.stock_code) == stock_code,
            )
        )
        result = await session.execute(stmt)
        existing = result.scalars().first()
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
            account_id=account_id,
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
    async def remove(cls, session: AsyncSession, account_id: int, stock_code: str) -> bool:
        from sqlalchemy import delete as _sa_delete

        stmt = _sa_delete(SayuPaperAgentPool).where(
            and_(
                col(SayuPaperAgentPool.account_id) == account_id,
                col(SayuPaperAgentPool.stock_code) == stock_code,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return _rowcount(result) > 0

    @classmethod
    @with_session
    async def list_codes(cls, session: AsyncSession, account_id: int) -> List[str]:
        """列出非过期的 AI 内部池股票代码"""
        now = datetime.now()
        stmt = select(col(SayuPaperAgentPool.stock_code)).where(
            and_(
                col(SayuPaperAgentPool.account_id) == account_id,
                # 未过期或无过期时间
                (col(SayuPaperAgentPool.expires_at).is_(None)) | (col(SayuPaperAgentPool.expires_at) > now),
            )
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    @classmethod
    @with_session
    async def list_by_account(cls, session: AsyncSession, account_id: int) -> List[SayuPaperAgentPool]:
        """列出非过期的 AI 内部池全量条目（含 name / priority / expires_at）。"""
        now = datetime.now()
        stmt = (
            select(SayuPaperAgentPool)
            .where(
                and_(
                    col(SayuPaperAgentPool.account_id) == account_id,
                    (col(SayuPaperAgentPool.expires_at).is_(None)) | (col(SayuPaperAgentPool.expires_at) > now),
                )
            )
            .order_by(col(SayuPaperAgentPool.priority).desc())
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
                col(SayuPaperAgentPool.expires_at).is_not(None),
                col(SayuPaperAgentPool.expires_at) <= now,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return _rowcount(result)

    @classmethod
    @with_session
    async def cleanup_expired_for(cls, session: AsyncSession, account_id: int) -> int:
        """物理删除本盘下已过期的候选（refresh 每轮先调，让轮换真正腾出空间）。

        list_codes/list_by_account 只在读时过滤过期行，行仍留库；轮换逻辑要按
        created_at 排序淘汰最旧 auto 候选，必须先把过期行删掉再统计，否则计数偏高。
        """
        from sqlalchemy import delete as _sa_delete

        now = datetime.now()
        stmt = _sa_delete(SayuPaperAgentPool).where(
            and_(
                col(SayuPaperAgentPool.account_id) == account_id,
                col(SayuPaperAgentPool.expires_at).is_not(None),
                col(SayuPaperAgentPool.expires_at) <= now,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return _rowcount(result)


# ============================================================
# Broadcast Target Repo
# ============================================================
class PaperBroadcastRepo:
    """播报目标：一个盘 → 任意多个群。

    ``add`` 是**幂等**的：Unique(account_id, bot_id, group_id) 命中时改为更新
    （重新启用 + 刷新 ws_bot_id / bot_self_id），而不是抛唯一约束异常——用户重复
    发一次「模拟盘推送添加」应该看到"已添加"，不是一堆报错。
    """

    @classmethod
    @with_session
    async def list_by_account(
        cls,
        session: AsyncSession,
        account_id: int,
        enabled_only: bool = False,
    ) -> List[SayuPaperBroadcastTarget]:
        stmt = select(SayuPaperBroadcastTarget).where(col(SayuPaperBroadcastTarget.account_id) == account_id)
        if enabled_only:
            stmt = stmt.where(col(SayuPaperBroadcastTarget.enabled) == 1)
        stmt = stmt.order_by(col(SayuPaperBroadcastTarget.id).asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def list_by_group(
        cls,
        session: AsyncSession,
        group_id: str,
        enabled_only: bool = True,
    ) -> List[SayuPaperBroadcastTarget]:
        """某个群订阅了哪些盘（「模拟盘推送列表」在群里查用）。"""
        stmt = select(SayuPaperBroadcastTarget).where(col(SayuPaperBroadcastTarget.group_id) == group_id)
        if enabled_only:
            stmt = stmt.where(col(SayuPaperBroadcastTarget.enabled) == 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def add(
        cls,
        session: AsyncSession,
        account_id: int,
        bot_id: str,
        group_id: str,
        *,
        bot_self_id: str = "",
        ws_bot_id: str = "",
        created_by: Optional[str] = None,
    ) -> SayuPaperBroadcastTarget:
        stmt = select(SayuPaperBroadcastTarget).where(
            and_(
                col(SayuPaperBroadcastTarget.account_id) == account_id,
                col(SayuPaperBroadcastTarget.bot_id) == bot_id,
                col(SayuPaperBroadcastTarget.group_id) == group_id,
            )
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            existing.enabled = 1
            # 重发命令时顺手补齐路由字段（迁移种子留空的那些就是靠这个补上的）
            if ws_bot_id:
                existing.ws_bot_id = ws_bot_id
            if bot_self_id:
                existing.bot_self_id = bot_self_id
            session.add(existing)
            await session.flush()
            return existing
        item = SayuPaperBroadcastTarget(
            account_id=account_id,
            bot_id=bot_id,
            bot_self_id=bot_self_id,
            ws_bot_id=ws_bot_id,
            group_id=group_id,
            enabled=1,
            created_by=created_by,
        )
        session.add(item)
        await session.flush()
        return item

    @classmethod
    @with_session
    async def remove(
        cls,
        session: AsyncSession,
        account_id: int,
        group_id: str,
        bot_id: str = "",
    ) -> int:
        """删除播报目标。``bot_id`` 留空表示删该群下该盘的所有 bot 记录。"""
        from sqlalchemy import delete as _sa_delete

        conds = [
            col(SayuPaperBroadcastTarget.account_id) == account_id,
            col(SayuPaperBroadcastTarget.group_id) == group_id,
        ]
        if bot_id:
            conds.append(col(SayuPaperBroadcastTarget.bot_id) == bot_id)
        result = await session.execute(_sa_delete(SayuPaperBroadcastTarget).where(and_(*conds)))
        await session.flush()
        return _rowcount(result)

    @classmethod
    @with_session
    async def set_enabled(
        cls,
        session: AsyncSession,
        target_id: int,
        enabled: int,
    ) -> bool:
        stmt = select(SayuPaperBroadcastTarget).where(col(SayuPaperBroadcastTarget.id) == target_id)
        item = (await session.execute(stmt)).scalars().first()
        if item is None:
            return False
        item.enabled = 1 if enabled else 0
        session.add(item)
        await session.flush()
        return True


# ============================================================
# 策略参数解析（strategy_params JSON → dict）
# ============================================================
def parse_strategy_params(raw: str) -> Dict[str, Any]:
    """把 ``account.strategy_params`` 解析成 dict；非法 JSON 返回空 dict。

    刻意不在这里 merge 默认参数——那是策略注册表的职责（它才知道默认值和类型）。
    """
    text = (raw or "").strip()
    if not text or text == "{}":
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): v for k, v in parsed.items()}


__all__ = [
    "DEFAULT_ACCOUNT_NAME",
    "DEFAULT_STRATEGY_ID",
    "PaperAccountRepo",
    "PaperPositionRepo",
    "PaperTradeRepo",
    "PaperDecisionRepo",
    "PaperSnapshotRepo",
    "PaperWatchlistRepo",
    "PaperAgentPoolRepo",
    "PaperBroadcastRepo",
    "parse_strategy_params",
]
