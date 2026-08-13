"""模拟盘 v2「去群主键」迁移单测。

迁移只在**升级那一刻**跑一次，出错的现场极难复现，所以这里在临时 sqlite 上手工
搭出 v1 表结构（外加 ``exec_list`` 已经 ALTER 出来的空白新列），把真实迁移函数跑
一遍，逐条核对：

  - 谁拿到「默认模拟盘」这个名字（决定了升级后老用户的盘还在不在）
  - 子表 ``account_id`` 回填 + 孤儿行归零（决定了历史流水会不会凭空消失）
  - 旧的 ``(group_id, bot_id)`` 唯一索引必须被删（不删就开不出第二个盘）
  - 幂等：用户改过的盘名，二次启动绝不能被改回去

另外覆盖两个**静默失败**场景：新列没 ALTER 上（``trans_adapter`` 吞异常）和盘名
撞车。两者都必须"放弃这一步但不炸启动"。
"""

import sys
import asyncio
from typing import List, Tuple, Optional
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from SayuStock.utils.database import papertrade_migration as mig  # noqa: E402

DEFAULT = mig.DEFAULT_ACCOUNT_NAME

# v1 账户表 + exec_list 补出来的空白新列。刻意不写 NOT NULL：ALTER ADD COLUMN
# 加出来的列在老行上就是 NULL / 默认值，迁移必须能吃下这种半成品状态。
# ⚠️ 唯一约束写成**表级 CONSTRAINT** 而不是独立索引——线上库就是这么建的
# （SQLModel 的 UniqueConstraint 会落进 CREATE TABLE）。写成 CREATE UNIQUE INDEX
# 的话 DROP INDEX 就能拆掉，测试会绿、线上却拆不动：SQLite 把表级约束实现成
# sqlite_autoindex_*，只能整表重建。这个差异真的放跑过一次 bug。
_ACCOUNT_DDL = """
CREATE TABLE sayupaperaccount (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    cash REAL NOT NULL DEFAULT 0,
    initial_cash REAL NOT NULL DEFAULT 0,
    principal REAL NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'balanced',
    enabled INTEGER NOT NULL DEFAULT 1,
    kanban_init_root_id TEXT,
    kanban_period_root_id TEXT,
    created_at TIMESTAMP,
    name TEXT DEFAULT '',
    strategy_id TEXT DEFAULT '',
    strategy_params TEXT DEFAULT '',
    schema_migrated_v2 INTEGER DEFAULT 0,
    CONSTRAINT ux_sayupaperaccount_gid_bid UNIQUE (group_id, bot_id)
)
"""

# 约束是最后一项时，摘掉它必须同时收拾尾逗号，否则重建的建表语句语法就是错的
_ACCOUNT_DDL_TRAILING_UNIQUE = _ACCOUNT_DDL.replace(
    "    schema_migrated_v2 INTEGER DEFAULT 0,\n    CONSTRAINT ux_sayupaperaccount_gid_bid UNIQUE (group_id, bot_id)\n",
    "    schema_migrated_v2 INTEGER DEFAULT 0,\n    UNIQUE (group_id, bot_id)\n",
)

# 没有任何旧约束的干净库（全新安装 / 已经迁过一次）
_ACCOUNT_DDL_NO_UNIQUE = _ACCOUNT_DDL.replace(
    ",\n    CONSTRAINT ux_sayupaperaccount_gid_bid UNIQUE (group_id, bot_id)", ""
)

_ACCOUNT_DDL_NO_NAME = _ACCOUNT_DDL.replace("    name TEXT DEFAULT '',\n", "")

_LEGACY_UNIQUE_SQL = "CREATE UNIQUE INDEX ux_sayupaperaccount_gid_bid ON sayupaperaccount (group_id, bot_id)"

_CHILD_DDL = """
CREATE TABLE {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    stock_code TEXT NOT NULL DEFAULT '',
    account_id INTEGER
)
"""

_BROADCAST_DDL = """
CREATE TABLE sayupaperbroadcasttarget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL DEFAULT 0,
    bot_id TEXT NOT NULL DEFAULT '',
    bot_self_id TEXT NOT NULL DEFAULT '',
    ws_bot_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP
)
"""


class _Fixture:
    """一个临时 sqlite 库 + 被替换掉的 ``async_maker``。"""

    def __init__(self, tmp_path: Path, *, account_ddl: str = _ACCOUNT_DDL) -> None:
        self.url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/pt.db"
        self.engine = create_async_engine(self.url)
        self.maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.account_ddl = account_ddl

    async def setup(self, *, legacy_index: bool = False) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text(self.account_ddl))
            if legacy_index:
                # 另一种形态的旧约束：独立唯一索引（DROP INDEX 就能拆）
                await conn.execute(text(_LEGACY_UNIQUE_SQL))
            for table in mig._CHILD_TABLES:
                await conn.execute(text(_CHILD_DDL.format(table=table)))
            await conn.execute(text(_BROADCAST_DDL))

    async def add_account(self, group_id: str, bot_id: str, created_at: str, **extra: object) -> int:
        cols = ["group_id", "bot_id", "created_at"]
        vals = [f"'{group_id}'", f"'{bot_id}'", f"'{created_at}'"]
        for k, v in extra.items():
            cols.append(k)
            vals.append(f"'{v}'" if isinstance(v, str) else str(v))
        async with self.maker() as s:
            inserted = await s.execute(
                text(f"INSERT INTO sayupaperaccount ({', '.join(cols)}) VALUES ({', '.join(vals)}) RETURNING id")
            )
            row = inserted.scalar()
            await s.commit()
        return int(row or 0)

    async def add_child(self, table: str, group_id: str, bot_id: str, code: str) -> None:
        async with self.maker() as s:
            await s.execute(
                text(f"INSERT INTO {table} (group_id, bot_id, stock_code) VALUES ('{group_id}', '{bot_id}', '{code}')")
            )
            await s.commit()

    async def rows(self, sql: str) -> List[Tuple]:
        async with self.maker() as s:
            return [tuple(r) for r in (await s.execute(text(sql))).all()]

    async def scalar(self, sql: str) -> Optional[object]:
        async with self.maker() as s:
            return (await s.execute(text(sql))).scalar()

    async def dispose(self) -> None:
        await self.engine.dispose()


def _run(tmp_path: Path, body, *, account_ddl: str = _ACCOUNT_DDL, legacy_index: bool = False):
    """搭库 → 替换 async_maker → 跑 body(fx) → 还原。"""

    async def _main():
        fx = _Fixture(tmp_path, account_ddl=account_ddl)
        await fx.setup(legacy_index=legacy_index)
        original = mig.async_maker
        mig.async_maker = fx.maker
        try:
            return await body(fx)
        finally:
            mig.async_maker = original
            await fx.dispose()

    return asyncio.run(_main())


# ============================================================
# 1) 正常升级路径
# ============================================================
def test_earliest_account_becomes_the_default(tmp_path):
    """最早开盘的那个必须拿到「默认模拟盘」——老用户升级后发命令还要能查到它。"""

    async def body(fx: _Fixture):
        old = await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        new = await fx.add_account("222", "onebot", "2026-01-01 09:00:00")
        assert await mig.run_papertrade_v2_migration() is None
        return old, new, await fx.rows("SELECT id, name, strategy_id, schema_migrated_v2 FROM sayupaperaccount")

    old, new, rows = _run(tmp_path, body)
    by_id = {r[0]: r for r in rows}
    assert by_id[old][1] == DEFAULT
    assert by_id[new][1] == f"历史盘-{new}"
    # 策略必须落地，否则 resolve 时会拿到空字符串 → 回落默认策略但库里是脏的
    assert all(r[2] == mig.DEFAULT_STRATEGY_ID for r in rows)
    assert all(r[3] == 1 for r in rows)


def test_child_tables_get_account_id(tmp_path):
    """历史流水靠 (group_id, bot_id) join 回账户；回填不上就等于数据消失。"""

    async def body(fx: _Fixture):
        acc = await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await fx.add_child("sayupaperposition", "111", "onebot", "600519")
        await fx.add_child("sayupapertrade", "111", "onebot", "600519")
        # 孤儿行：账户早就被清盘删了，流水还在
        await fx.add_child("sayupapertrade", "999", "onebot", "000001")
        assert await mig.run_papertrade_v2_migration() is None
        return acc, await fx.rows("SELECT stock_code, account_id FROM sayupapertrade ORDER BY id")

    acc, trades = _run(tmp_path, body)
    assert trades == [("600519", acc), ("000001", 0)]


def test_orphan_rows_are_zeroed_not_deleted(tmp_path):
    """孤儿行归零而不是删除：读路径按 account_id > 0 过滤，数据留着能人工救。"""

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await fx.add_child("sayupaperdecision", "999", "onebot", "000001")
        await mig.run_papertrade_v2_migration()
        return await fx.scalar("SELECT COUNT(*) FROM sayupaperdecision WHERE account_id = 0")

    assert _run(tmp_path, body) == 1


def test_broadcast_seeded_from_origin_group(tmp_path):
    """升级后播报不能断：默认盘要自动订阅它原来的群。"""

    async def body(fx: _Fixture):
        acc = await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        return acc, await fx.rows("SELECT account_id, group_id, bot_id, created_by FROM sayupaperbroadcasttarget")

    acc, targets = _run(tmp_path, body)
    assert targets == [(acc, "111", "onebot", "migration")]


async def _legacy_unique_count(fx: "_Fixture") -> int:
    """数一下 (group_id, bot_id) 上还剩几个唯一约束（含 autoindex）。"""
    rows = await fx.rows("PRAGMA index_list(sayupaperaccount)")
    hits = 0
    for row in rows:
        if not row[2]:
            continue
        cols = {r[2] for r in await fx.rows(f"PRAGMA index_info('{row[1]}')")}
        if cols == {"group_id", "bot_id"}:
            hits += 1
    return hits


def test_table_level_unique_constraint_is_removed(tmp_path):
    """线上库把旧约束写在 CREATE TABLE 里，SQLite 落成 sqlite_autoindex_*。

    ``DROP INDEX`` 对它无效，必须整表重建。早期实现只发 DROP INDEX 就宣告成功，
    实测线上约束原封不动——这条用例就是那次事故的回归。
    """

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        return await _legacy_unique_count(fx)

    assert _run(tmp_path, body) == 0


def test_standalone_unique_index_is_also_removed(tmp_path):
    """另一种形态：独立唯一索引（部分库可能是这样建的）。"""

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        return await _legacy_unique_count(fx)

    assert _run(tmp_path, body, account_ddl=_ACCOUNT_DDL_NO_UNIQUE, legacy_index=True) == 0


def test_trailing_unique_clause_rebuild_produces_valid_ddl(tmp_path):
    """约束是列表最后一项时，摘掉它会留下尾逗号——不收拾就是语法错、重建失败。"""

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        return await _legacy_unique_count(fx)

    assert _run(tmp_path, body, account_ddl=_ACCOUNT_DDL_TRAILING_UNIQUE) == 0


def test_rebuild_preserves_rows_and_indexes(tmp_path):
    """重建表 = DROP + RENAME，数据和其它索引都必须原样活下来。"""

    async def body(fx: _Fixture):
        async with fx.engine.begin() as conn:
            await conn.execute(text("CREATE INDEX ix_sayupaperaccount_enabled ON sayupaperaccount (enabled)"))
        acc = await fx.add_account("111", "onebot", "2025-01-01 09:00:00", cash=12345.5)
        await mig.run_papertrade_v2_migration()
        return (
            acc,
            await fx.rows("SELECT id, name, cash, group_id FROM sayupaperaccount"),
            await fx.scalar(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ix_sayupaperaccount_enabled'"
            ),
            await fx.scalar(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ux_sayupaperaccount_name'"
            ),
        )

    acc, rows, enabled_idx, name_idx = _run(tmp_path, body)
    assert rows == [(acc, DEFAULT, 12345.5, "111")]
    assert enabled_idx == 1
    # name 唯一索引必须在重建之后建，否则会被 DROP TABLE 一起带走
    assert name_idx == 1


def test_second_account_in_same_group_is_possible_after_migration(tmp_path):
    """行为验证：约束真的没了，同群第二个盘插得进去。这才是整个改造的前提。"""

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        await fx.add_account("111", "onebot", "2026-01-01 09:00:00", name="放量盘")
        return await fx.scalar("SELECT COUNT(*) FROM sayupaperaccount WHERE group_id = '111'")

    assert _run(tmp_path, body) == 2


def test_duplicate_names_still_rejected_after_rebuild(tmp_path):
    """拆旧约束不能顺手把新约束也拆没了：盘名唯一是新模型的主键语义。"""

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        try:
            await fx.add_account("222", "onebot", "2026-01-01 09:00:00", name=DEFAULT)
        except Exception as e:
            return type(e).__name__
        return "NO_ERROR"

    assert _run(tmp_path, body) != "NO_ERROR"


def test_strip_helper_handles_both_clause_shapes():
    """重建的正确性全押在这个正则上，单独钉一下。"""
    named = "CREATE TABLE t (\n id INTEGER,\n PRIMARY KEY (id),\n"
    named += " CONSTRAINT ux_sayupaperaccount_gid_bid UNIQUE (group_id, bot_id)\n)"
    bare = 'CREATE TABLE t (\n id INTEGER,\n UNIQUE ("group_id", "bot_id"),\n other TEXT\n)'
    for ddl in (named, bare):
        out = mig._strip_legacy_unique_clause(ddl)
        assert "group_id, bot_id" not in out.replace('"', "")
        assert ",\n)" not in out and ", )" not in out
    # 没有该约束时原样返回（调用方靠"没变化"判断解析失败）
    clean = "CREATE TABLE t (id INTEGER)"
    assert mig._strip_legacy_unique_clause(clean) == clean


# ============================================================
# 2) 幂等
# ============================================================
def test_migration_is_idempotent_and_respects_renames(tmp_path):
    """二次启动不能把用户改过的盘名改回「默认模拟盘」。"""

    async def body(fx: _Fixture):
        acc = await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        async with fx.maker() as s:
            await s.execute(text(f"UPDATE sayupaperaccount SET name = '我的盘' WHERE id = {acc}"))
            await s.commit()
        await mig.run_papertrade_v2_migration()
        return await fx.scalar(f"SELECT name FROM sayupaperaccount WHERE id = {acc}")

    assert _run(tmp_path, body) == "我的盘"


def test_second_run_does_not_duplicate_broadcast_targets(tmp_path):
    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await mig.run_papertrade_v2_migration()
        await mig.run_papertrade_v2_migration()
        return await fx.scalar("SELECT COUNT(*) FROM sayupaperbroadcasttarget")

    assert _run(tmp_path, body) == 1


# ============================================================
# 3) 静默失败的两个来源
# ============================================================
def test_missing_column_aborts_without_raising(tmp_path):
    """``trans_adapter`` 对 ALTER 失败是 ``except: pass``，迁移必须自己探测。

    探测不到就整体放弃并回报原因——继续跑会在每一步都撞 "no such column"。
    """

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        return await mig.run_papertrade_v2_migration()

    reason = _run(tmp_path, body, account_ddl=_ACCOUNT_DDL_NO_NAME)
    assert reason is not None and "name" in reason


def test_duplicate_names_skip_unique_index_without_crashing(tmp_path):
    """两个盘重名时索引建不上，但迁移必须跑完（否则启动阶段抛异常）。"""

    async def body(fx: _Fixture):
        await fx.add_account("111", "onebot", "2025-01-01 09:00:00", name="重名盘", schema_migrated_v2=1)
        await fx.add_account("222", "onebot", "2026-01-01 09:00:00", name="重名盘", schema_migrated_v2=1)
        assert await mig.run_papertrade_v2_migration() is None
        return await fx.scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ux_sayupaperaccount_name'"
        )

    assert _run(tmp_path, body) == 0


def test_empty_account_table_still_finalizes_index(tmp_path):
    """全新库没有账户，但唯一索引得先建好，否则第一次建盘就能建出重名。"""

    async def body(fx: _Fixture):
        assert await mig.run_papertrade_v2_migration() is None
        return await fx.scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ux_sayupaperaccount_name'"
        )

    assert _run(tmp_path, body) == 1


def test_name_collision_with_existing_default_gets_suffixed(tmp_path):
    """老库里已经有人手工叫「默认模拟盘」时，最早的那个不能硬抢名字建不上索引。"""

    async def body(fx: _Fixture):
        first = await fx.add_account("111", "onebot", "2025-01-01 09:00:00")
        await fx.add_account("222", "onebot", "2026-01-01 09:00:00", name=DEFAULT, schema_migrated_v2=1)
        assert await mig.run_papertrade_v2_migration() is None
        return first, await fx.rows("SELECT id, name FROM sayupaperaccount ORDER BY id")

    first, rows = _run(tmp_path, body)
    names = [r[1] for r in rows]
    assert len(set(names)) == len(names)  # 无重名
    assert dict(rows)[first] != DEFAULT or names.count(DEFAULT) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
