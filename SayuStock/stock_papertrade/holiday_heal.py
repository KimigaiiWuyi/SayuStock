"""假期脏成交自愈与还原。

背景：模拟盘心跳原先只靠一张人工维护的假期表判断交易日，2026 年那张表只覆盖到
2 月底，于是 4/6、5/1、5/4、5/5、6/19、9/25、10/1~10/7 这些法定休市日全被当成交易日
照常撮合，凭空写进 ``sayupapertrade``。日历改成数据驱动后不会再产生新的脏数据，
但历史那批必须还回去——否则现金、本金、持仓、净值曲线全是错的。

判定脏数据**不用**任何人工假期表：以 ``trading_calendar.resolve_closed_days()`` 返回的
权威休市集合（来自上证日 K「工作日却无 K 线」）为准，拿不到就整体放弃、绝不动账。

删除成交违反 ``SayuPaperTrade`` 的 append-only 约定，所以：
  * 只在 master 命令 / 启动钩子里跑，绝不进心跳路径
  * 删前把整行落到 ``DATA_PATH/papertrade_holiday_archive_*.json`` 留档
  * ``dry_run=True`` 时只出计划不落库
  * 幂等：没有脏成交就是空操作

重算一律复用撮合层公式（``apply_fill_qty_cost`` / ``cash_delta_for_fill``），不另立口径。
"""

from __future__ import annotations

import json
import asyncio
from typing import Dict, List, Tuple, Optional, FrozenSet, TypedDict
from datetime import date, datetime
from dataclasses import replace, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger
from gsuid_core.utils.database.base_models import async_maker

from .matcher import apply_fill_qty_cost, cash_delta_for_fill
from .trading_calendar import resolve_closed_days
from ..utils.resource_path import DATA_PATH
from ..utils.database.papertrade_migration import _row_date, _has_table, _has_column

_LOG = "[SayuStock][PaperTrade][假期还原]"

# 回溯历史收盘价时单盘最多拉多少只票的日 K，避免极端盘把请求数打爆
_MAX_PRICE_LOOKUP_CODES = 60

# ── 熔断阈值 ──
# 脏数据只可能来自少数几个长假，占比理应极低。超过下面任一阈值就意味着
# "休市表判据"本身可疑（上游残缺 / secid 走偏），此时删除是净损失。
_MAX_DIRTY_SHARE = 0.30
_MAX_DIRTY_ROWS = 500


class AccountCashFix(TypedDict):
    account_id: int
    account_name: str
    old_cash: float
    new_cash: float
    old_principal: float
    new_principal: float


class PositionFix(TypedDict):
    account_id: int
    stock_code: str
    stock_name: str
    old_qty: int
    new_qty: int
    old_avg_cost: float
    new_avg_cost: float
    removed: bool


class HolidayHealResult(TypedDict):
    skipped: Optional[str]
    source: str
    dry_run: bool
    dirty_days: List[str]
    deleted_trades: int
    deleted_decisions: int
    positions_changed: int
    positions_removed: int
    snapshots_updated: int
    snapshots_deleted: int
    price_fallback_codes: List[str]
    cash_fixed: List[AccountCashFix]
    positions: List[PositionFix]
    archive_path: Optional[str]


@dataclass(slots=True)
class _Trade:
    id: int
    account_id: int
    code: str
    name: str
    secid: str
    side: str
    price: float
    qty: int
    amount: float
    fee: float
    realized_pnl: float
    decided_at: datetime
    executed_on: Optional[date]


@dataclass(slots=True)
class _PosState:
    qty: int = 0
    avg: float = 0.0
    opened_on: Optional[date] = None
    name: str = ""
    secid: str = ""


def _empty_result(source: str, dry_run: bool, skipped: Optional[str] = None) -> HolidayHealResult:
    return HolidayHealResult(
        skipped=skipped,
        source=source,
        dry_run=dry_run,
        dirty_days=[],
        deleted_trades=0,
        deleted_decisions=0,
        positions_changed=0,
        positions_removed=0,
        snapshots_updated=0,
        snapshots_deleted=0,
        price_fallback_codes=[],
        cash_fixed=[],
        positions=[],
        archive_path=None,
    )


# ============================================================
# 回放：从流水重建任意某天的持仓
# ============================================================
def _apply(state: _PosState, t: _Trade) -> None:
    """把一笔成交作用到持仓状态上（口径与 db.append_with_cash_update 一致）。"""
    new_qty, new_avg = apply_fill_qty_cost(state.qty, state.avg, t.side, t.qty, t.price, t.fee)
    if t.name:
        state.name = t.name
    if t.secid:
        state.secid = t.secid
    if new_qty <= 0:
        # 清仓：与线上"删行后重建"一致，开仓日要跟着重置
        state.qty = 0
        state.avg = 0.0
        state.opened_on = None
        return
    if t.side == "buy" and state.opened_on is None:
        state.opened_on = t.executed_on
    state.qty = new_qty
    state.avg = new_avg


def _replay_final(trades: List[_Trade]) -> Dict[str, _PosState]:
    """回放全部留存流水，得到每只票的最终持仓。"""
    states: Dict[str, _PosState] = {}
    for t in trades:
        state = states.get(t.code)
        if state is None:
            state = _PosState()
            states[t.code] = state
        _apply(state, t)
    return states


def _holdings_on(trades: List[_Trade], marks: List[date]) -> Dict[date, Dict[str, _PosState]]:
    """单趟回放，在每个 mark 日记录当日收盘时的持仓。"""
    states: Dict[str, _PosState] = {}
    snap: Dict[date, Dict[str, _PosState]] = {}
    idx = 0
    for t in trades:
        day = t.executed_on
        while idx < len(marks) and day is not None and marks[idx] < day:
            snap[marks[idx]] = {c: replace(s) for c, s in states.items() if s.qty > 0}
            idx += 1
        state = states.get(t.code)
        if state is None:
            state = _PosState()
            states[t.code] = state
        _apply(state, t)
    while idx < len(marks):
        snap[marks[idx]] = {c: replace(s) for c, s in states.items() if s.qty > 0}
        idx += 1
    return snap


# ============================================================
# 历史收盘价回溯（重写快照的 position_value 用）
# ============================================================
async def _fetch_closes(codes: List[str], start: date, end: date) -> Tuple[Dict[str, Dict[date, float]], List[str]]:
    """拉各票日 K，返回 ({code: {date: close}}, 取价失败的 code)。"""
    from ..utils.market import KlinePeriod, get_market, is_market_error

    wanted = codes[:_MAX_PRICE_LOOKUP_CODES]
    table: Dict[str, Dict[date, float]] = {}
    failed: List[str] = []
    market = get_market()

    async def one(code: str) -> Tuple[str, Optional[Dict[date, float]]]:
        series = await market.kline(code, KlinePeriod.D1, start=start, end=end)
        if is_market_error(series):
            logger.warning(f"{_LOG} {code} 历史收盘价拉取失败: {series.message}")
            return code, None
        return code, {bar.ts.date(): bar.close for bar in series.bars}

    for code, bars in await asyncio.gather(*(one(c) for c in wanted)):
        if bars is None:
            failed.append(code)
        else:
            table[code] = bars
    if len(codes) > _MAX_PRICE_LOOKUP_CODES:
        logger.warning(
            f"{_LOG} 持仓 {len(codes)} 只，超出回溯上限 {_MAX_PRICE_LOOKUP_CODES}，"
            f"多出的 {len(codes) - _MAX_PRICE_LOOKUP_CODES} 只按成本价兜底"
        )
    return table, failed


def _close_for(table: Dict[str, Dict[date, float]], code: str, day: date, fallback: float) -> float:
    """取 day 当日收盘价；没有就用该票 day 之前最近一根，再没有就退回成本价。"""
    bars = table.get(code)
    if bars is None or not bars:
        return fallback
    hit = bars.get(day)
    if hit is not None:
        return hit
    earlier = [d for d in bars if d <= day]
    if earlier:
        return bars[max(earlier)]
    return fallback


# ============================================================
# 主流程
# ============================================================
def _as_datetime(value: object) -> Optional[datetime]:
    """裸 ``text()`` 查询不会做类型映射，datetime 列回来可能是 str。

    解析不了就返回 None——**绝不能**兜底成 ``now()``：那会把每一笔历史成交
    都标成"今天"，然后把整个账本当脏数据删光。
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        raw = value.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw[:26], fmt)
            except ValueError:
                continue
    return None


async def _load_trades(session: AsyncSession) -> List[_Trade]:
    rows = (
        await session.execute(
            text(
                "SELECT id, account_id, stock_code, stock_name, secid, side, price, qty, "
                "amount, fee, realized_pnl, decided_at, executed_at "
                "FROM sayupapertrade WHERE account_id > 0 ORDER BY executed_at ASC, id ASC"
            )
        )
    ).all()
    out: List[_Trade] = []
    skipped: List[int] = []
    for r in rows:
        decided = _as_datetime(r[11])
        if decided is None:
            skipped.append(int(r[0]))
            continue
        executed = _as_datetime(r[12]) or decided
        out.append(
            _Trade(
                id=int(r[0]),
                account_id=int(r[1]),
                code=str(r[2] or ""),
                name=str(r[3] or ""),
                secid=str(r[4] or ""),
                side=str(r[5] or ""),
                price=float(r[6] or 0.0),
                qty=int(r[7] or 0),
                amount=float(r[8] or 0.0),
                fee=float(r[9] or 0.0),
                realized_pnl=float(r[10] or 0.0),
                decided_at=decided,
                executed_on=executed.date(),
            )
        )
    if skipped:
        logger.error(f"{_LOG} {len(skipped)} 笔流水时间戳无法解析，已跳过（不删不改）: {skipped[:20]}")
    return out


async def _load_accounts(session: AsyncSession) -> Dict[int, Tuple[str, float, float, float]]:
    rows = (await session.execute(text("SELECT id, name, initial_cash, cash, principal FROM sayupaperaccount"))).all()
    return {int(r[0]): (str(r[1] or ""), float(r[2] or 0.0), float(r[3] or 0.0), float(r[4] or 0.0)) for r in rows}


async def _load_positions(session: AsyncSession) -> Dict[Tuple[int, str], Tuple[int, float, float]]:
    has_quote = await _has_column(session, "sayupaperposition", "last_quote_price")
    quote_col = "last_quote_price" if has_quote else "NULL"
    rows = (
        await session.execute(
            text(
                "SELECT account_id, stock_code, qty, avg_cost, stock_name, "
                f"{quote_col} AS last_quote_price FROM sayupaperposition WHERE account_id > 0"
            )
        )
    ).all()
    out: Dict[Tuple[int, str], Tuple[int, float, float]] = {}
    for r in rows:
        out[(int(r[0]), str(r[1] or ""))] = (
            int(r[2] or 0),
            float(r[3] or 0.0),
            float(r[5] or 0.0) if r[5] is not None else 0.0,
        )
    return out


def _plan_accounts(
    accounts: Dict[int, Tuple[str, float, float, float]],
    keep: List[_Trade],
    old_positions: Dict[Tuple[int, str], Tuple[int, float, float]],
) -> Tuple[List[AccountCashFix], List[PositionFix]]:
    """算每个盘的现金/本金更正与持仓更正。"""
    by_acc: Dict[int, List[_Trade]] = {}
    for t in keep:
        by_acc.setdefault(t.account_id, []).append(t)

    cash_fixes: List[AccountCashFix] = []
    pos_fixes: List[PositionFix] = []

    for aid, (name, initial, cash, principal) in accounts.items():
        trades = by_acc.get(aid, [])
        new_cash = initial
        realized = 0.0
        for t in trades:
            new_cash += cash_delta_for_fill(t.side, t.amount, t.fee)
            if t.side.strip().lower() == "sell":
                realized += t.realized_pnl
        new_principal = initial + realized
        if abs(new_cash - cash) > 0.005 or abs(new_principal - principal) > 0.005:
            cash_fixes.append(
                AccountCashFix(
                    account_id=aid,
                    account_name=name,
                    old_cash=round(cash, 2),
                    new_cash=round(new_cash, 2),
                    old_principal=round(principal, 2),
                    new_principal=round(new_principal, 2),
                )
            )

        finals = _replay_final(trades)
        # 两边的键都要统一成 (account_id, code)：finals 的键是裸 code，
        # 直接和 old_positions 的元组键取并集会退化成联合类型，key[1] 在裸
        # 字符串上取到的是第二个字符，股票代码会被写坏。
        keys: set[Tuple[int, str]] = {(aid, c) for c in finals} | set(old_positions)
        for key in sorted(keys):
            code = key[1]
            state = finals.get(code)
            new_qty = state.qty if state is not None else 0
            new_avg = state.avg if state is not None else 0.0
            old = old_positions.get(key)
            old_qty, old_avg, _ = old if old is not None else (0, 0.0, 0.0)
            if new_qty == old_qty and abs(new_avg - old_avg) <= 0.0001:
                continue
            pos_fixes.append(
                PositionFix(
                    account_id=aid,
                    stock_code=code,
                    stock_name=(state.name if state is not None and state.name else ""),
                    old_qty=old_qty,
                    new_qty=new_qty,
                    old_avg_cost=round(old_avg, 4),
                    new_avg_cost=round(new_avg, 4),
                    removed=new_qty <= 0,
                )
            )
    return cash_fixes, pos_fixes


async def _archive(trades: List[_Trade], days: List[str]) -> str:
    """把待删的整行落 JSON 留档，返回文件路径。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = DATA_PATH / f"papertrade_holiday_archive_{stamp}.json"
    payload = {
        "archived_at": datetime.now().isoformat(),
        "dirty_days": days,
        "count": len(trades),
        "trades": [
            {
                "id": t.id,
                "account_id": t.account_id,
                "stock_code": t.code,
                "stock_name": t.name,
                "secid": t.secid,
                "side": t.side,
                "price": t.price,
                "qty": t.qty,
                "amount": t.amount,
                "fee": t.fee,
                "realized_pnl": t.realized_pnl,
                "decided_at": t.decided_at.isoformat(),
            }
            for t in trades
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


async def _rewrite_snapshots(
    session: AsyncSession,
    keep: List[_Trade],
    first_dirty: date,
    closed: FrozenSet[str],
) -> Tuple[int, int, List[str]]:
    """删掉休市日的净值行，并重写脏数据首日之后的净值链。

    两件事：
      1. **休市日不该有净值行**。那天压根没开盘，留一行既不真实也污染曲线，
         直接删——曲线自然形成"节前最后一天 → 节后第一天"的断档。
      2. 脏数据首日**之前**的行不动（没被污染）；之后的按「留存流水 +
         当日真实收盘价」重算，``day_pnl`` 以**前一条留存的净值行**为基准，
         而不是区间首行（否则首行会拿到 total_pnl 当日盈亏）。
    """
    rows = (
        await session.execute(
            text(
                "SELECT id, account_id, trade_date FROM sayupapersnapshot "
                "WHERE trade_date >= :start ORDER BY account_id, trade_date, id"
            ),
            {"start": first_dirty.isoformat()},
        )
    ).all()
    if not rows:
        return 0, 0, []

    holiday_ids: List[int] = []
    for r in rows:
        day = _row_date(r[2])
        if day is not None and day.isoformat() in closed:
            holiday_ids.append(int(r[0]))
    if holiday_ids:
        ids = ",".join(str(i) for i in holiday_ids)
        await session.execute(text(f"DELETE FROM sayupapersnapshot WHERE id IN ({ids})"))
    keep_rows = [r for r in rows if int(r[0]) not in set(holiday_ids)]
    if not keep_rows:
        return 0, 0, []

    by_acc: Dict[int, List[_Trade]] = {}
    for t in keep:
        by_acc.setdefault(t.account_id, []).append(t)
    marks_by_acc: Dict[int, List[date]] = {}
    for r in keep_rows:
        d = _row_date(r[2])
        if d is not None:
            marks_by_acc.setdefault(int(r[1]), []).append(d)
    for lst in marks_by_acc.values():
        lst.sort()

    codes: List[str] = sorted({t.code for t in keep})
    span_start = min(lst[0] for lst in marks_by_acc.values() if lst)
    span_end = max(lst[-1] for lst in marks_by_acc.values() if lst)
    table, failed = await _fetch_closes(codes, span_start, span_end)

    updated = 0
    for aid, marks in marks_by_acc.items():
        acct = (
            await session.execute(
                text("SELECT initial_cash, cash FROM sayupaperaccount WHERE id = :id"),
                {"id": aid},
            )
        ).first()
        if acct is None:
            continue
        initial_cash = float(acct[0] or 0.0)
        holdings = _holdings_on(by_acc.get(aid, []), marks)

        # 现金时间线：按留存流水的成交日累加
        steps: List[Tuple[date, float]] = []
        running = initial_cash
        for t in by_acc.get(aid, []):
            if t.executed_on is None:
                continue
            running += cash_delta_for_fill(t.side, t.amount, t.fee)
            steps.append((t.executed_on, running))

        # day_pnl 基准：脏数据首日之前最后一条留存的净值
        seed = (
            await session.execute(
                text(
                    "SELECT total_equity FROM sayupapersnapshot WHERE account_id = :id "
                    "AND trade_date < :start ORDER BY trade_date DESC, id DESC LIMIT 1"
                ),
                {"id": aid, "start": first_dirty.isoformat()},
            )
        ).first()
        prev_equity: Optional[float] = float(seed[0]) if seed is not None else None

        for r in keep_rows:
            if int(r[1]) != aid:
                continue
            sid, day = int(r[0]), _row_date(r[2])
            if day is None:
                continue
            pos_value = 0.0
            for code, st in holdings.get(day, {}).items():
                pos_value += st.qty * _close_for(table, code, day, st.avg)
            pos_value = round(pos_value, 2)

            cash_v = running
            for d, v in steps:
                if d <= day:
                    cash_v = v
            cash_v = round(cash_v, 2)
            equity = round(cash_v + pos_value, 2)
            total_pnl = round(equity - initial_cash, 2)
            total_pct = round(total_pnl / initial_cash * 100, 4) if initial_cash else 0.0
            if prev_equity is None:
                day_pnl = round(equity - initial_cash, 2)
                base = initial_cash
            else:
                day_pnl = round(equity - prev_equity, 2)
                base = prev_equity
            day_pct = round(day_pnl / base * 100, 4) if base else 0.0
            await session.execute(
                text(
                    "UPDATE sayupapersnapshot SET cash = :cash, position_value = :pv, "
                    "total_equity = :eq, day_pnl = :dp, day_pnl_pct = :dpp, "
                    "total_pnl = :tp, total_pnl_pct = :tpp WHERE id = :sid"
                ),
                {
                    "cash": cash_v,
                    "pv": pos_value,
                    "eq": equity,
                    "dp": day_pnl,
                    "dpp": day_pct,
                    "tp": total_pnl,
                    "tpp": total_pct,
                    "sid": sid,
                },
            )
            updated += 1
            prev_equity = equity
    return updated, len(holiday_ids), failed


async def _apply_positions(session: AsyncSession, keep: List[_Trade], fixes: List[PositionFix]) -> Tuple[int, int]:
    """按重算结果改/删持仓行，返回 (改了几行, 删了几行)。"""
    finals: Dict[int, Dict[str, _PosState]] = {}
    for t in keep:
        finals.setdefault(t.account_id, {})
    by_acc: Dict[int, List[_Trade]] = {}
    for t in keep:
        by_acc.setdefault(t.account_id, []).append(t)
    for aid, trades in by_acc.items():
        finals[aid] = _replay_final(trades)

    has_quote = await _has_column(session, "sayupaperposition", "last_quote_price")
    has_opened = await _has_column(session, "sayupaperposition", "opened_at")
    now = datetime.now()
    changed = removed = 0
    for fix in fixes:
        state = finals.get(fix["account_id"], {}).get(fix["stock_code"])
        if fix["removed"] or state is None or state.qty <= 0:
            await session.execute(
                text("DELETE FROM sayupaperposition WHERE account_id = :aid AND stock_code = :code"),
                {"aid": fix["account_id"], "code": fix["stock_code"]},
            )
            removed += 1
            continue

        sets = ["qty = :qty", "avg_cost = :avg", "updated_at = :now"]
        params: Dict[str, object] = {
            "qty": state.qty,
            "avg": round(state.avg, 4),
            "now": now,
            "aid": fix["account_id"],
            "code": fix["stock_code"],
        }
        if state.name:
            sets.append("stock_name = :name")
            params["name"] = state.name
        if state.secid:
            sets.append("secid = :secid")
            params["secid"] = state.secid
        if has_opened and state.opened_on is not None:
            sets.append("opened_at = :opened")
            params["opened"] = datetime.combine(state.opened_on, datetime.min.time())
        if not has_quote:
            sets.append("last_quote_price = :px")
            params["px"] = round(state.avg, 4)
        await session.execute(
            text(f"UPDATE sayupaperposition SET {', '.join(sets)} WHERE account_id = :aid AND stock_code = :code"),
            params,
        )
        changed += 1
    return changed, removed


async def heal_holiday_trades(*, dry_run: bool = False) -> HolidayHealResult:
    """清掉落在非交易日的成交，并把现金 / 本金 / 持仓 / 净值快照重建回自洽。

    幂等：没有脏成交时是空操作。``dry_run=True`` 时只算计划不落库。
    权威休市集合拿不到就直接放弃——宁可不清，也不拿过期的人工表删数据。
    """
    closed, source = await resolve_closed_days()
    if closed is None:
        logger.warning(f"{_LOG} 权威休市表拿不到（{source}），本次不处理历史脏成交")
        return _empty_result(source, dry_run, skipped=f"权威休市表不可用（{source}）")
    if not closed:
        logger.warning(f"{_LOG} 权威休市表为空，本次不处理历史脏成交")
        return _empty_result(source, dry_run, skipped="权威休市表为空")

    result = _empty_result(source, dry_run)
    async with async_maker() as session:
        if not await _has_table(session, "sayupapertrade"):
            return _empty_result(source, dry_run, skipped="无 sayupapertrade 表")
        if not await _has_table(session, "sayupaperaccount"):
            return _empty_result(source, dry_run, skipped="无 sayupaperaccount 表")

        trades = await _load_trades(session)
        dirty = [t for t in trades if t.decided_at.date().isoformat() in closed]
        if not dirty:
            return result

        days = sorted({t.decided_at.date().isoformat() for t in dirty})
        first_dirty = min(t.decided_at.date() for t in dirty)
        result["dirty_days"] = days
        dirty_ids = {t.id for t in dirty}
        keep = [t for t in trades if t.id not in dirty_ids]

        # ── 熔断：脏数据占比过高一律不动账 ──
        # 正常情况下脏数据只可能来自少数几个长假，占比极低。一旦这个比例失控，
        # 更可能的解释是"休市表判据本身出了问题"（上游数据残缺、secid 走偏等），
        # 此时按脏数据删下去就是把整本账删光。宁可留着等人来看。
        share = len(dirty) / len(trades)
        if share > _MAX_DIRTY_SHARE:
            msg = (
                f"脏数据占比 {len(dirty)}/{len(trades)} = {share:.1%} 超过阈值 "
                f"{_MAX_DIRTY_SHARE:.0%}，判定休市表判据可疑，**已放弃本次清理**"
            )
            logger.error(f"{_LOG} {msg}")
            result["skipped"] = msg
            return result
        if not dry_run and len(dirty) > _MAX_DIRTY_ROWS:
            msg = (
                f"单次将删除 {len(dirty)} 笔流水，超过安全上限 {_MAX_DIRTY_ROWS} 笔，"
                f"**已放弃自动清理**；请人工核对后用「模拟盘假期还原 执行」"
            )
            logger.error(f"{_LOG} {msg}")
            result["skipped"] = msg
            return result

        accounts = await _load_accounts(session)
        old_positions = await _load_positions(session) if await _has_table(session, "sayupaperposition") else {}
        cash_fixes, pos_fixes = _plan_accounts(accounts, keep, old_positions)
        result["cash_fixed"] = cash_fixes
        result["positions"] = pos_fixes

        logger.warning(
            f"{_LOG} 发现 {len(dirty)} 笔非交易日成交（{days[0]} ~ {days[-1]}，共 {len(days)} 天，"
            f"来源 {source}）；{'预演' if dry_run else '准备清理'}"
        )
        if dry_run:
            result["deleted_trades"] = len(dirty)
            return result

        archive_path = await _archive(dirty, days)
        result["archive_path"] = archive_path

        ids = ",".join(str(i) for i in sorted(dirty_ids))
        await session.execute(text(f"DELETE FROM sayupapertrade WHERE id IN ({ids})"))
        result["deleted_trades"] = len(dirty)

        for fix in cash_fixes:
            await session.execute(
                text("UPDATE sayupaperaccount SET cash = :cash, principal = :p WHERE id = :id"),
                {"cash": fix["new_cash"], "p": fix["new_principal"], "id": fix["account_id"]},
            )

        if pos_fixes and await _has_table(session, "sayupaperposition"):
            changed, removed = await _apply_positions(session, keep, pos_fixes)
            result["positions_changed"] = changed
            result["positions_removed"] = removed

        if await _has_table(session, "sayupapersnapshot"):
            updated, dropped, failed = await _rewrite_snapshots(session, keep, first_dirty, closed)
            result["snapshots_updated"] = updated
            result["snapshots_deleted"] = dropped
            result["price_fallback_codes"] = failed

        if await _has_table(session, "sayupaperdecision"):
            dec_ids: List[int] = []
            for day in days:
                found = (
                    await session.execute(
                        text(
                            "SELECT id FROM sayupaperdecision WHERE substr(created_at,1,10) = :d "
                            "AND action IN ('buy','sell')"
                        ),
                        {"d": day},
                    )
                ).all()
                dec_ids.extend(int(x[0]) for x in found)
            if dec_ids:
                id_list = ",".join(str(i) for i in dec_ids)
                await session.execute(text(f"DELETE FROM sayupaperdecision WHERE id IN ({id_list})"))
            result["deleted_decisions"] = len(dec_ids)

        await session.commit()

    logger.warning(
        f"{_LOG} 完成：删流水 {result['deleted_trades']} 笔、决策 {result['deleted_decisions']} 条，"
        f"持仓改 {result['positions_changed']} / 删 {result['positions_removed']}，"
        f"快照 {result['snapshots_updated']} 行；留档 {result['archive_path']}"
    )
    return result
