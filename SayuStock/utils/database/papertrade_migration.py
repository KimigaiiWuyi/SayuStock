"""模拟盘「去群主键」数据迁移（v2）。

在 ``on_core_start_before(priority=-70)`` 阶段跑一次。之所以是 ``-70``：框架自己
的三个钩子分别是 ``-100`` import models、``-90`` ``create_all``、``-80``
``trans_adapter``（跑 ``exec_list`` 的 ALTER）。本函数依赖新列已经存在，所以必须
排在 ``-80`` **之后**（数值更大 = 更晚）。写成 ``-85`` 会看到一张没有 ``name``
列的旧表。

另一个必须自卫的点：``trans_adapter`` 对每条 SQL 都是 ``except: pass``，**加列
失败没有任何日志**。所以本模块每一步之前都先用 ``_has_column`` 探测，探测不到就
记 error 并整体放弃迁移——宁可让模拟盘退化成单盘继续跑，也不能让一条 SQL 异常
把整个 core 的启动拖挂。

迁移内容：
  1. 账户命名：最早的 → ``默认模拟盘``，其余 → ``历史盘-{id}``（重名自动加后缀）
  2. ``strategy_id`` / ``strategy_params`` 填默认值
  3. 6 张子表回填 ``account_id``（按 ``(group_id, bot_id)`` join 回账户）
  4. 播报目标种子：默认盘 → 原群 + 旧配置 ``papertrade_broadcast_group``
  5. 拆掉旧的 ``(group_id, bot_id)`` 唯一约束（SQLite 下需整表重建，见
     ``_drop_legacy_unique``）+ 建 ``name`` 唯一索引

全程幂等：``schema_migrated_v2`` 标记 + ``WHERE account_id = 0`` 形态的 UPDATE，
中途被 kill 下次启动继续跑，不会把用户改过的盘名改回去。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Optional
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger
from gsuid_core.server import on_core_start_before
from gsuid_core.utils.database.base_models import async_maker

from .papertrade_models import DEFAULT_STRATEGY_ID, DEFAULT_ACCOUNT_NAME

_ACCOUNT_TABLE = "sayupaperaccount"
_BROADCAST_TABLE = "sayupaperbroadcasttarget"
# 子表 → 是否参与 (group_id, bot_id) 回填（全部都参与，列出来是为了顺序稳定）
_CHILD_TABLES: Tuple[str, ...] = (
    "sayupaperposition",
    "sayupapertrade",
    "sayupaperdecision",
    "sayupapersnapshot",
    "sayupaperwatchlist",
    "sayupaperagentpool",
)

_LOG = "[SayuStock][PaperTrade][迁移v2]"
_HEAL_LOG = "[SayuStock][PaperTrade][对账]"


# ============================================================
# 探测 helpers
# ============================================================
async def _has_table(session: AsyncSession, table: str) -> bool:
    try:
        await session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        await session.rollback()
        return False


async def _has_column(session: AsyncSession, table: str, column: str) -> bool:
    """跨方言列探测：SELECT 一下，报错即视为无此列。

    比查 information_schema / sqlite_master 简单且三方言通用；失败后必须
    ``rollback``，否则 PG 会把整个事务标记为 aborted，后续语句全部报错。
    """
    try:
        await session.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
        return True
    except Exception:
        await session.rollback()
        return False


async def _exec(session: AsyncSession, sql: str, *, label: str) -> bool:
    """执行一条 DDL/DML；失败记 warning 并返回 False（不抛）。"""
    try:
        await session.execute(text(sql))
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logger.debug(f"{_LOG} {label} 失败（已忽略）: {type(e).__name__}: {e}")
        return False


# ============================================================
# 步骤 1+2：账户命名 + 策略默认值
# ============================================================
async def _backfill_account_names(session: AsyncSession) -> int:
    """给未迁移过的账户命名 + 填策略默认值；返回处理条数。

    排序用 ``created_at ASC, id ASC``：很老的库 ``created_at`` 可能全为 NULL，
    各方言下 NULL 的排序位置不一致，``id`` 兜底保证"谁是最早的"结果稳定。
    """
    rows = (
        await session.execute(
            text(
                f"SELECT id, name, strategy_id, schema_migrated_v2 FROM {_ACCOUNT_TABLE} "
                f"ORDER BY created_at ASC, id ASC"
            )
        )
    ).all()
    if not rows:
        return 0

    # 已被占用的盘名（含本轮已分配的），用来做重名规避
    taken: set[str] = {str(r[1]).strip() for r in rows if r[1] and str(r[1]).strip()}
    handled = 0

    for idx, row in enumerate(rows):
        acc_id = int(row[0])
        cur_name = str(row[1]).strip() if row[1] else ""
        cur_strategy = str(row[2]).strip() if row[2] else ""
        migrated = int(row[3] or 0)
        if migrated == 1:
            # 已迁移过：用户可能改过名字，绝不能再动
            continue

        want_name = cur_name
        if not want_name or (idx > 0 and want_name == DEFAULT_ACCOUNT_NAME):
            # 最早那个吃 DEFAULT_ACCOUNT_NAME；其余用 历史盘-{id}
            want_name = DEFAULT_ACCOUNT_NAME if idx == 0 else f"历史盘-{acc_id}"
        # 重名规避：加 -{id}，仍冲突再加序号
        if want_name != cur_name:
            base = want_name
            suffix = 0
            while want_name in taken:
                suffix += 1
                want_name = f"{base}-{acc_id}" if suffix == 1 else f"{base}-{acc_id}-{suffix}"
        taken.discard(cur_name)
        taken.add(want_name)

        want_strategy = cur_strategy or DEFAULT_STRATEGY_ID
        ok = await _exec(
            session,
            (
                f"UPDATE {_ACCOUNT_TABLE} SET name = '{_sql_escape(want_name)}', "
                f"strategy_id = '{_sql_escape(want_strategy)}', "
                f"strategy_params = COALESCE(NULLIF(strategy_params, ''), '{{}}'), "
                f"schema_migrated_v2 = 1 WHERE id = {acc_id}"
            ),
            label=f"命名账户 id={acc_id} → {want_name}",
        )
        if ok:
            handled += 1
            logger.info(f"{_LOG} 账户 id={acc_id} → 盘名「{want_name}」策略={want_strategy}")
    return handled


def _sql_escape(value: str) -> str:
    """单引号转义。盘名来自 DB / 常量，这里只做最基本的字面量安全。"""
    return value.replace("'", "''")


# ============================================================
# 步骤 3：子表回填 account_id
# ============================================================
async def _backfill_child_account_ids(session: AsyncSession) -> None:
    for table in _CHILD_TABLES:
        if not await _has_column(session, table, "account_id"):
            logger.error(f"{_LOG} {table}.account_id 列缺失，跳过该表回填")
            continue
        await _exec(
            session,
            (
                f"UPDATE {table} SET account_id = ("
                f"  SELECT a.id FROM {_ACCOUNT_TABLE} a "
                f"  WHERE a.group_id = {table}.group_id AND a.bot_id = {table}.bot_id"
                f") WHERE account_id IS NULL OR account_id = 0"
            ),
            label=f"回填 {table}.account_id",
        )
        # 回填不上的是孤儿行（账户已删但流水还在）——读路径按 account_id > 0
        # 过滤，永不展示；这里只统计并告警，不删数据。
        try:
            orphan = (
                await session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE account_id IS NULL OR account_id = 0"))
            ).scalar()
        except Exception:
            await session.rollback()
            orphan = None
        if orphan:
            logger.warning(f"{_LOG} {table} 仍有 {orphan} 行无法归属账户（孤儿行，已忽略）")
        # 孤儿行的 account_id 可能是 NULL，统一归零便于查询
        await _exec(
            session,
            f"UPDATE {table} SET account_id = 0 WHERE account_id IS NULL",
            label=f"{table}.account_id NULL → 0",
        )


# ============================================================
# 步骤 4：播报目标种子
# ============================================================
def _legacy_broadcast_group() -> str:
    """读旧配置 ``papertrade_broadcast_group``（仅迁移时读这一次）。"""
    try:
        from ...stock_config.stock_config import STOCK_CONFIG

        raw = STOCK_CONFIG.get_config("papertrade_broadcast_group").data
        return raw.strip() if isinstance(raw, str) else ""
    except Exception:
        return ""


async def _seed_broadcast_targets(session: AsyncSession) -> None:
    if not await _has_table(session, _BROADCAST_TABLE):
        logger.error(f"{_LOG} {_BROADCAST_TABLE} 表不存在，跳过播报种子")
        return
    existing = (await session.execute(text(f"SELECT COUNT(*) FROM {_BROADCAST_TABLE}"))).scalar()
    if existing:
        # 已经有目标了（可能是用户配的）——不再种子，避免把删掉的目标又加回来
        return

    row = (
        await session.execute(
            text(
                f"SELECT id, group_id, bot_id FROM {_ACCOUNT_TABLE} "
                f"WHERE name = '{_sql_escape(DEFAULT_ACCOUNT_NAME)}' LIMIT 1"
            )
        )
    ).first()
    if row is None:
        return
    acc_id, origin_group, bot_id = int(row[0]), str(row[1] or ""), str(row[2] or "")

    seeds: List[str] = []
    legacy = _legacy_broadcast_group()
    if legacy:
        seeds.append(legacy)
    if origin_group and origin_group not in seeds:
        seeds.append(origin_group)

    for gid in seeds:
        # ws_bot_id / bot_self_id 迁移期无 ev 可抄，留空；投递时 broadcast 会回退在线连接。
        # 运维应在每个目标群重发一次「模拟盘推送添加」补齐，避免多适配器挑错线。
        await _exec(
            session,
            (
                f"INSERT INTO {_BROADCAST_TABLE} "
                f"(account_id, bot_id, bot_self_id, ws_bot_id, group_id, enabled, created_by, created_at) "
                f"VALUES ({acc_id}, '{_sql_escape(bot_id)}', '', '', '{_sql_escape(gid)}', 1, 'migration', "
                f"CURRENT_TIMESTAMP)"
            ),
            label=f"播报种子 account={acc_id} → 群 {gid}",
        )
        logger.info(f"{_LOG} 播报种子：默认模拟盘 → 群 {gid}（ws_bot_id 待补，建议重发「模拟盘推送添加」）")


# ============================================================
# 步骤 5：索引收尾
# ============================================================
async def _finalize_indexes(session: AsyncSession) -> None:
    # 顺序不能反：先拆旧唯一约束（SQLite 分支要重建整张表），再建 name 唯一索引。
    # 反过来的话重建表时刚建好的索引会被 DROP TABLE 一起带走。
    await _drop_legacy_unique(session)

    # 建 name 唯一索引前先确认没有重名（重名会让索引静默建不上）
    dup = (
        await session.execute(text(f"SELECT name, COUNT(*) c FROM {_ACCOUNT_TABLE} GROUP BY name HAVING COUNT(*) > 1"))
    ).all()
    if dup:
        names = ", ".join(str(d[0]) for d in dup)
        logger.error(f"{_LOG} 存在重复盘名（{names}），name 唯一索引未建立；请手动改名后重启")
    else:
        await _exec(
            session,
            f"CREATE UNIQUE INDEX IF NOT EXISTS ux_sayupaperaccount_name ON {_ACCOUNT_TABLE} (name)",
            label="建 name 唯一索引",
        )

    if await _legacy_unique_alive(session):
        logger.error(
            f"{_LOG} 旧的 (group_id, bot_id) 唯一约束仍在，同一个群将无法创建第二个模拟盘；请手动移除该约束后重启"
        )


async def _drop_legacy_unique(session: AsyncSession) -> None:
    """拆掉 v1 的 ``(group_id, bot_id)`` 唯一约束。

    这是唯一一个"静默失败会让核心新功能整个不可用"的步骤：不拆掉就开不出第二个盘。

    ⚠️ 它在 v1 里是**建表时写死的表级 CONSTRAINT**，不是独立索引。SQLite 把这种约束
    实现成 ``sqlite_autoindex_*``，``DROP INDEX`` 拆不掉（"index associated with UNIQUE
    constraint may not be dropped"），只能整表重建。早期版本只发 DROP INDEX 就以为
    完事了，实测线上库约束原封不动 —— 而复验又只查名为 ``ux_sayupaperaccount_gid_bid``
    的索引，查不到便报告"已删"，于是错误被一路放行到用户建第二个盘时才爆。
    """
    if not await _legacy_unique_alive(session):
        return

    # MySQL / PostgreSQL：命名约束可以直接拆，不用重建表
    for sql in (
        "DROP INDEX IF EXISTS ux_sayupaperaccount_gid_bid",
        f"ALTER TABLE {_ACCOUNT_TABLE} DROP INDEX ux_sayupaperaccount_gid_bid",
        f"ALTER TABLE {_ACCOUNT_TABLE} DROP CONSTRAINT IF EXISTS ux_sayupaperaccount_gid_bid",
    ):
        await _exec(session, sql, label="删旧唯一约束")
    if not await _legacy_unique_alive(session):
        logger.info(f"{_LOG} 旧唯一约束已移除（DROP 生效）")
        return

    await _rebuild_account_table_without_legacy_unique(session)


async def _rebuild_account_table_without_legacy_unique(session: AsyncSession) -> None:
    """SQLite 专用：复制建表语句 → 去掉唯一约束 → 搬数据 → 换名。

    整个过程放在**一个事务**里（SQLite 的 DDL 是事务性的），中途失败就整体回滚，
    不会留下半张表。索引 DDL 要先抄下来：``DROP TABLE`` 会把它们一起带走。
    """
    try:
        ddl_row = (
            await session.execute(text(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{_ACCOUNT_TABLE}'"))
        ).first()
    except Exception:
        await session.rollback()
        return
    if ddl_row is None or not ddl_row[0]:
        return

    ddl: str = str(ddl_row[0])
    new_ddl = _strip_legacy_unique_clause(ddl)
    if new_ddl == ddl:
        logger.error(f"{_LOG} 未能从建表语句里定位旧唯一约束，已放弃重建（建表语句可能被手工改过）")
        return

    tmp_table = f"{_ACCOUNT_TABLE}_mig_v2"
    new_ddl = new_ddl.replace(_ACCOUNT_TABLE, tmp_table, 1)

    # 只搬两边都有的列；索引 DDL 里的 sql 为 NULL 的是 autoindex，跳过
    cols = [str(r[1]) for r in (await session.execute(text(f"PRAGMA table_info({_ACCOUNT_TABLE})"))).all()]
    if not cols:
        return
    col_list = ", ".join(f'"{c}"' for c in cols)
    index_ddls = [
        str(r[0])
        for r in (
            await session.execute(
                text(f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='{_ACCOUNT_TABLE}'")
            )
        ).all()
        if r[0]
    ]

    try:
        await session.execute(text(new_ddl))
        await session.execute(text(f"INSERT INTO {tmp_table} ({col_list}) SELECT {col_list} FROM {_ACCOUNT_TABLE}"))
        await session.execute(text(f"DROP TABLE {_ACCOUNT_TABLE}"))
        await session.execute(text(f"ALTER TABLE {tmp_table} RENAME TO {_ACCOUNT_TABLE}"))
        for idx in index_ddls:
            await session.execute(text(idx))
        await session.commit()
        logger.info(f"{_LOG} 已重建 {_ACCOUNT_TABLE} 并移除旧的 (group_id, bot_id) 唯一约束")
    except Exception as e:
        await session.rollback()
        logger.error(f"{_LOG} 重建 {_ACCOUNT_TABLE} 失败，旧唯一约束仍在: {type(e).__name__}: {e}")
        await _exec(session, f"DROP TABLE IF EXISTS {tmp_table}", label="清理重建残留")


_LEGACY_UNIQUE_PATTERNS: Tuple[str, ...] = (
    r",?\s*CONSTRAINT\s+[\"'`\[\]\w]+\s+UNIQUE\s*\(\s*[\"'`\[\]]?group_id[\"'`\[\]]?\s*,\s*[\"'`\[\]]?bot_id[\"'`\[\]]?\s*\)",
    r",?\s*UNIQUE\s*\(\s*[\"'`\[\]]?group_id[\"'`\[\]]?\s*,\s*[\"'`\[\]]?bot_id[\"'`\[\]]?\s*\)",
)


def _strip_legacy_unique_clause(ddl: str) -> str:
    """从建表语句里摘掉 ``UNIQUE (group_id, bot_id)`` 子句。

    约束是列表里的最后一项时，删掉它会留下 ``PRIMARY KEY (id), )`` 这种尾逗号，
    SQLite 会直接报语法错，所以最后要补一次收尾清理。
    """
    out = ddl
    for pattern in _LEGACY_UNIQUE_PATTERNS:
        new = re.sub(pattern, "", out, count=1, flags=re.IGNORECASE)
        if new != out:
            out = new
            break
    return re.sub(r",(\s*)\)(\s*)$", r"\1)\2", out)


async def _legacy_unique_alive(session: AsyncSession) -> bool:
    """SQLite 下判断 ``(group_id, bot_id)`` 上还有没有唯一约束。

    走 ``PRAGMA index_list`` 而不是按名字查 ``sqlite_master``：表级 CONSTRAINT 落成的
    是 ``sqlite_autoindex_*``，按名字查永远查不到，会给出"已经删干净了"的假阳性。
    其它方言 PRAGMA 会失败，此时返回 False（不误报）。
    """
    try:
        rows = (await session.execute(text(f"PRAGMA index_list({_ACCOUNT_TABLE})"))).all()
    except Exception:
        await session.rollback()
        return False
    for row in rows:
        idx_name, unique = str(row[1]), bool(row[2])
        if not unique:
            continue
        try:
            cols = {str(r[2]) for r in (await session.execute(text(f"PRAGMA index_info('{idx_name}')"))).all()}
        except Exception:
            await session.rollback()
            continue
        if cols == {"group_id", "bot_id"}:
            return True
    return False


# ============================================================
# 入口
# ============================================================
async def run_papertrade_v2_migration() -> Optional[str]:
    """执行迁移；返回 None 表示成功（或无需迁移），返回 str 表示放弃的原因。"""
    async with async_maker() as session:
        if not await _has_table(session, _ACCOUNT_TABLE):
            return "账户表不存在（全新库由 create_all 建好，无需迁移）"

        # 前置条件：新列必须真的加上了（trans_adapter 是静默失败的）
        for column in ("name", "strategy_id", "strategy_params", "schema_migrated_v2"):
            if not await _has_column(session, _ACCOUNT_TABLE, column):
                return f"{_ACCOUNT_TABLE}.{column} 列缺失（ALTER 未生效），已跳过多账户迁移"

        total = (await session.execute(text(f"SELECT COUNT(*) FROM {_ACCOUNT_TABLE}"))).scalar()
        if not total:
            # 空库也要收尾索引，保证后续创建盘时唯一性生效
            await _finalize_indexes(session)
            return None

        named = await _backfill_account_names(session)
        await _backfill_child_account_ids(session)
        await _seed_broadcast_targets(session)
        await _finalize_indexes(session)
        if named:
            logger.info(f"{_LOG} 完成：{named} 个账户已命名，子表 account_id 已回填")
        return None


def _empty_heal() -> Dict[str, Any]:
    return {"orphans": [], "snapshots_updated": 0, "broadcast_filled": 0, "cash_restated": []}


def _row_date(value: object) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _as_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
    return 0


def _as_float(value: object) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _pos_mv(qty: object, last_quote: object, avg_cost: object) -> float:
    q = _as_int(qty)
    px = _as_float(last_quote) if last_quote is not None else _as_float(avg_cost)
    return float(q) * px


def _trade_cash_delta(side: object, amount: object, fee: object) -> float:
    """正确现金变化：buy 付 amount+fee，sell 收回 amount-fee。不含 realized_pnl。"""
    amt = _as_float(amount)
    fee_v = _as_float(fee)
    side_text = str(side).strip().lower() if side is not None else ""
    if side_text == "buy":
        return -(amt + fee_v)
    return amt - fee_v


def _cash_on(timeline: List[Tuple[date, float]], snap_date: date, stored: float) -> float:
    last: Optional[float] = None
    for d, cash in timeline:
        if d <= snap_date:
            last = cash
        else:
            break
    return stored if last is None else last


async def heal_orphan_ledger() -> Dict[str, Any]:
    """清幽灵仓、更正「卖出现金误加 realized_pnl」、回写净值、补播报路由。

    幂等。服务端部署本版后重启或发「模拟盘对账」即可，不必拷开发机的库。
    """
    out: Dict[str, Any] = _empty_heal()
    async with async_maker() as session:
        if not await _has_table(session, _ACCOUNT_TABLE):
            return out
        if not await _has_table(session, "sayupaperposition"):
            return out
        if not await _has_column(session, "sayupaperposition", "qty"):
            return out
        if not await _has_column(session, "sayupaperposition", "avg_cost"):
            return out
        has_quote = await _has_column(session, "sayupaperposition", "last_quote_price")
        has_name = await _has_column(session, "sayupaperposition", "stock_name")
        has_opened = await _has_column(session, "sayupaperposition", "opened_at")
        quote_col = "last_quote_price" if has_quote else "NULL"
        name_col = "stock_name" if has_name else "''"
        opened_col = "opened_at" if has_opened else "NULL"
        pos_sql = (
            "SELECT id, account_id, stock_code, "
            f"{name_col} AS stock_name, qty, avg_cost, "
            f"{quote_col} AS last_quote_price, {opened_col} AS opened_at "
            "FROM sayupaperposition WHERE qty > 0 AND account_id > 0"
        )
        pos_rows = (await session.execute(text(pos_sql))).all()
        nets: Dict[Tuple[int, str], int] = {}
        if (
            await _has_table(session, "sayupapertrade")
            and await _has_column(session, "sayupapertrade", "side")
            and await _has_column(session, "sayupapertrade", "qty")
        ):
            net_rows = (
                await session.execute(
                    text(
                        "SELECT account_id, stock_code, "
                        "COALESCE(SUM(CASE WHEN side = 'buy' THEN qty ELSE -qty END), 0) "
                        "FROM sayupapertrade WHERE account_id > 0 "
                        "GROUP BY account_id, stock_code"
                    )
                )
            ).all()
            nets = {(int(r[0]), str(r[1])): int(r[2]) for r in net_rows}

        orphans: List[Dict[str, Any]] = []
        phantom_mv: Dict[int, float] = {}
        opened_on: Dict[int, date] = {}
        for row in pos_rows:
            pid, acc_id, code = int(row[0]), int(row[1]), str(row[2] or "")
            if nets.get((acc_id, code), 0) > 0:
                continue
            mv = _pos_mv(row[4], row[6], row[5])
            od = _row_date(row[7]) or date.min
            orphans.append(
                {
                    "id": pid,
                    "account_id": acc_id,
                    "stock_code": code,
                    "stock_name": str(row[3] or ""),
                    "qty": int(row[4] or 0),
                    "mv": round(mv, 2),
                }
            )
            phantom_mv[acc_id] = phantom_mv.get(acc_id, 0.0) + mv
            prev = opened_on.get(acc_id)
            opened_on[acc_id] = od if prev is None or od < prev else prev

        can_snap = await _has_table(session, "sayupapersnapshot") and await _has_column(
            session, "sayupapersnapshot", "position_value"
        )
        can_day = can_snap and await _has_column(session, "sayupapersnapshot", "day_pnl")
        can_broadcast = await _has_table(session, _BROADCAST_TABLE) and await _has_column(
            session, _BROADCAST_TABLE, "ws_bot_id"
        )
        can_cash = (
            await _has_table(session, "sayupapertrade")
            and await _has_column(session, "sayupapertrade", "amount")
            and await _has_column(session, "sayupapertrade", "fee")
            and await _has_column(session, "sayupapertrade", "realized_pnl")
            and await _has_column(session, "sayupapertrade", "executed_at")
        )

        cash_restated, cash_timeline, live_cash = ([], {}, {})
        if can_cash:
            cash_restated, cash_timeline, live_cash = await _plan_cash_restatement(session)

        if orphans:
            ids = ",".join(str(int(o["id"])) for o in orphans)
            await session.execute(text(f"DELETE FROM sayupaperposition WHERE id IN ({ids})"))
            out["orphans"] = orphans
        for fix in cash_restated:
            await session.execute(
                text(f"UPDATE {_ACCOUNT_TABLE} SET cash = :cash WHERE id = :id"),
                {"cash": fix["new_cash"], "id": int(fix["account_id"])},
            )
        out["cash_restated"] = cash_restated

        affected = set(phantom_mv) | {int(f["account_id"]) for f in cash_restated}
        if can_day and affected:
            out["snapshots_updated"] = await _rewrite_snapshots(
                session,
                phantom_mv=phantom_mv,
                opened_on=opened_on,
                cash_timeline=cash_timeline,
                live_cash=live_cash,
                has_quote=has_quote,
                acc_ids=affected,
            )

        if can_broadcast:
            out["broadcast_filled"] = await _fill_empty_broadcast_routing(session)
        await session.commit()
    if out["orphans"] or out["broadcast_filled"] or out["cash_restated"]:
        codes = [f"{o['stock_code']}×{o['qty']}" for o in out["orphans"]]
        logger.warning(
            f"{_HEAL_LOG} 幽灵仓 {len(out['orphans'])} 只 {codes}；"
            f"现金更正 {len(out['cash_restated'])} 盘；"
            f"快照改 {out['snapshots_updated']} 行；播报补 {out['broadcast_filled']} 条"
        )
    return out


async def _plan_cash_restatement(
    session: AsyncSession,
) -> tuple[List[Dict[str, Any]], Dict[int, List[Tuple[date, float]]], Dict[int, float]]:
    """若账户现金 − 按流水重算现金 ≈ Σrealized_pnl，判定为旧卖出公式把盈亏加进了现金。"""
    accs = (await session.execute(text(f"SELECT id, cash, initial_cash FROM {_ACCOUNT_TABLE}"))).all()
    trades = (
        await session.execute(
            text(
                "SELECT account_id, side, amount, fee, realized_pnl, executed_at "
                "FROM sayupapertrade WHERE account_id > 0 "
                "ORDER BY executed_at ASC, id ASC"
            )
        )
    ).all()
    by_acc: Dict[int, List[Any]] = {}
    for row in trades:
        by_acc.setdefault(int(row[0]), []).append(row)

    fixes: List[Dict[str, Any]] = []
    timeline: Dict[int, List[Tuple[date, float]]] = {}
    live_cash: Dict[int, float] = {}
    for acc in accs:
        aid = int(acc[0])
        cash = float(acc[1] or 0.0)
        initial = float(acc[2] or 0.0)
        expected = initial
        pnl_sum = 0.0
        steps: List[Tuple[date, float]] = []
        for row in by_acc.get(aid, []):
            expected += _trade_cash_delta(row[1], row[2], row[3])
            if str(row[1] or "").strip().lower() == "sell":
                pnl_sum += float(row[4] or 0.0)
            d = _row_date(row[5])
            if d is not None:
                steps.append((d, expected))
        gap = cash - expected
        if abs(gap) <= 0.05 or abs(gap - pnl_sum) > 1.0:
            continue
        new_cash = round(expected, 4)
        fixes.append(
            {
                "account_id": aid,
                "old_cash": round(cash, 4),
                "new_cash": new_cash,
                "extra": round(gap, 4),
            }
        )
        timeline[aid] = steps
        live_cash[aid] = new_cash
    return fixes, timeline, live_cash


async def _rewrite_snapshots(
    session: AsyncSession,
    *,
    phantom_mv: Dict[int, float],
    opened_on: Dict[int, date],
    cash_timeline: Dict[int, List[Tuple[date, float]]],
    live_cash: Dict[int, float],
    has_quote: bool,
    acc_ids: set[int],
) -> int:
    """按幽灵市值 + 正确现金重写快照；日盈亏按更正后的总资产链条重算。"""
    updated = 0
    for acc_id in acc_ids:
        initial = (
            await session.execute(
                text(f"SELECT initial_cash, cash FROM {_ACCOUNT_TABLE} WHERE id = :id"),
                {"id": acc_id},
            )
        ).first()
        if initial is None:
            continue
        initial_cash = float(initial[0] or 0.0)
        now_cash = float(live_cash.get(acc_id, initial[1] or 0.0))
        snaps = (
            await session.execute(
                text(
                    "SELECT id, trade_date, cash, position_value, total_equity "
                    "FROM sayupapersnapshot WHERE account_id = :id "
                    "ORDER BY trade_date ASC, id ASC"
                ),
                {"id": acc_id},
            )
        ).all()
        opened = opened_on.get(acc_id)
        mv = phantom_mv.get(acc_id, 0.0)
        steps = cash_timeline.get(acc_id, [])
        prev_equity: Optional[float] = None
        for snap in snaps:
            td = _row_date(snap[1])
            stored_cash = float(snap[2] or 0.0)
            pv = float(snap[3] or 0.0)
            if td is not None and opened is not None and td >= opened:
                pv = max(0.0, pv - mv)
            cash = _cash_on(steps, td, stored_cash) if td is not None else stored_cash
            equity = round(cash + pv, 2)
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
                    "UPDATE sayupapersnapshot SET cash = :cash, position_value = :pv, total_equity = :eq, "
                    "day_pnl = :dp, day_pnl_pct = :dpp, total_pnl = :tp, total_pnl_pct = :tpp "
                    "WHERE id = :sid"
                ),
                {
                    "cash": round(cash, 2),
                    "pv": round(pv, 2),
                    "eq": equity,
                    "dp": day_pnl,
                    "dpp": day_pct,
                    "tp": total_pnl,
                    "tpp": total_pct,
                    "sid": int(snap[0]),
                },
            )
            updated += 1
            prev_equity = equity

        quote_expr = "last_quote_price" if has_quote else "NULL"
        live_rows = (
            await session.execute(
                text(
                    f"SELECT qty, {quote_expr} AS last_quote_price, avg_cost "
                    "FROM sayupaperposition WHERE account_id = :id AND qty > 0"
                ),
                {"id": acc_id},
            )
        ).all()
        live_pv = round(sum(_pos_mv(r[0], r[1], r[2]) for r in live_rows), 2)
        live_eq = round(now_cash + live_pv, 2)
        latest = (
            await session.execute(
                text(
                    "SELECT id FROM sayupapersnapshot WHERE account_id = :id ORDER BY trade_date DESC, id DESC LIMIT 1"
                ),
                {"id": acc_id},
            )
        ).first()
        if latest is not None:
            total_pnl = round(live_eq - initial_cash, 2)
            total_pct = round(total_pnl / initial_cash * 100, 4) if initial_cash else 0.0
            await session.execute(
                text(
                    "UPDATE sayupapersnapshot SET cash = :cash, position_value = :pv, "
                    "total_equity = :eq, total_pnl = :tp, total_pnl_pct = :tpp WHERE id = :sid"
                ),
                {
                    "cash": round(now_cash, 2),
                    "pv": live_pv,
                    "eq": live_eq,
                    "tp": total_pnl,
                    "tpp": total_pct,
                    "sid": int(latest[0]),
                },
            )
    return updated


async def _fill_empty_broadcast_routing(session: AsyncSession) -> int:
    """同一群里若有完整 ws_bot_id / bot_self_id，抄给迁移种子留下的空目标。"""
    rows = (await session.execute(text(f"SELECT id, group_id, bot_self_id, ws_bot_id FROM {_BROADCAST_TABLE}"))).all()
    donors: Dict[str, Tuple[str, str]] = {}
    for _tid, gid, self_id, ws_id in rows:
        g = str(gid or "")
        self_s = str(self_id or "").strip()
        ws_s = str(ws_id or "").strip()
        if g and self_s and ws_s and g not in donors:
            donors[g] = (self_s, ws_s)
    filled = 0
    for tid, gid, self_id, ws_id in rows:
        self_s = str(self_id or "").strip()
        ws_s = str(ws_id or "").strip()
        if self_s and ws_s:
            continue
        donor = donors.get(str(gid or ""))
        if donor is None:
            continue
        await session.execute(
            text(f"UPDATE {_BROADCAST_TABLE} SET bot_self_id = :self_id, ws_bot_id = :ws_id WHERE id = :id"),
            {"self_id": donor[0], "ws_id": donor[1], "id": int(tid)},
        )
        filled += 1
    return filled


@on_core_start_before(priority=-70)
async def papertrade_migrate_v2() -> None:
    """启动钩子。任何异常都只记日志，绝不让迁移失败拖挂 core 启动。"""
    try:
        skipped = await run_papertrade_v2_migration()
        if skipped:
            logger.warning(f"{_LOG} 跳过：{skipped}")
        await heal_orphan_ledger()
    except Exception as e:
        logger.exception(f"{_LOG} 迁移异常，模拟盘将以现有 schema 继续运行: {e}")
        return
