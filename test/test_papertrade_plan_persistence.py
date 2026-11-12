"""模拟盘入场计划持久化与长决策理由不截断单测。

覆盖：
1. 决策理由不截断：大于 200 字的完整 reasoning 完整落库，避免上下文自我污染。
2. 流水 snapshot 参数持久化：传入 indicators 时，snapshot 正确序列化落库而不为空。
3. 持仓表持久化入场计划：成交买入时随持仓原子写入 plan_stop_price / plan_stop_pct / plan_take_price / plan_thesis。
4. 持仓列表接口：papertrade_position_list 正常返回入场计划字段。
"""

import sys
import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from SayuStock.stock_papertrade import db, ai_tools, trade_executor  # noqa: E402
from SayuStock.utils.database.papertrade_models import SayuPaperAccount, SayuPaperPosition  # noqa: E402


def test_position_model_has_plan_fields():
    """核对 SayuPaperPosition 模型包含结构化止损计划字段。"""
    fields = SayuPaperPosition.model_fields
    assert "plan_stop_price" in fields
    assert "plan_stop_pct" in fields
    assert "plan_take_price" in fields
    assert "plan_thesis" in fields


def test_decision_reason_not_truncated_over_200_chars():
    """测试长决策理由落库时不再被 200 字符截断。"""
    long_reason = "这是一个超过两百字的完整决策逻辑。" + "详细量化推理与宏观背景分析，保持持有观察白电走势。" * 15
    assert len(long_reason) > 300

    fake_acc = SayuPaperAccount(id=1, name="默认模拟盘", cash=1000000.0)
    saved_records: list[dict[str, object]] = []

    async def fake_append(account_id, **kwargs):
        saved_records.append(kwargs)
        return AsyncMock(id=101)

    ctx = AsyncMock()

    with (
        patch.object(ai_tools, "_write_account", AsyncMock(return_value=(fake_acc, ""))),
        patch.object(ai_tools, "_deny_write", AsyncMock(return_value="")),
        patch.object(db.PaperDecisionRepo, "append", fake_append),
    ):
        asyncio.run(
            ai_tools.papertrade_decision_insert(
                ctx=ctx,
                action="hold",
                stock_code="000333",
                stock_name="美的集团",
                reason=long_reason,
                indicators="{}",
            )
        )

    assert len(saved_records) == 1
    stored_reason = str(saved_records[0]["reason"])
    # 必须保留完整文本，不以 ... 截断
    assert stored_reason == long_reason
    assert len(stored_reason) > 300
    assert not stored_reason.endswith("...")


def test_trade_insert_persists_snapshot_from_indicators():
    """测试 trade_insert 时若将止损计划传入 indicators，snapshot 字段能正确序列化并落库。"""
    fake_acc = SayuPaperAccount(id=1, name="默认模拟盘", cash=1000000.0)
    record_calls: list[dict[str, object]] = []

    class FakeExecutor:
        async def record_trade(self, **kwargs):
            record_calls.append(kwargs)
            res = AsyncMock()
            res.ok = True
            res.message = "ok"
            return res

    ctx = AsyncMock()
    plan_json = '{"plan_stop_price": 83.36, "plan_stop_pct": -0.055, "plan_thesis": "白电龙头底仓"}'

    with (
        patch.object(ai_tools, "_write_account", AsyncMock(return_value=(fake_acc, ""))),
        patch.object(ai_tools, "_deny_write", AsyncMock(return_value="")),
        patch.object(ai_tools, "_gate_entry", AsyncMock(return_value="")),
        patch.object(trade_executor, "get_executor", return_value=FakeExecutor()),
        patch.object(ai_tools.broadcast, "broadcast_fill", AsyncMock()),
    ):
        # 传入 indicators，snapshot 留空
        asyncio.run(
            ai_tools.papertrade_trade_insert(
                ctx=ctx,
                stock_code="000333",
                stock_name="美的集团",
                secid="0.000333",
                side="buy",
                price=88.21,
                qty=500,
                amount=44105.0,
                fee=5.0,
                indicators=plan_json,
                snapshot="",
            )
        )

    assert len(record_calls) == 1
    call = record_calls[0]
    # snapshot 参数必须被正确赋值，而不是空字符串
    assert call["snapshot"] != ""
    parsed = json.loads(str(call["snapshot"]))
    assert abs(float(parsed["plan_stop_price"]) - 83.36) < 0.001
    assert abs(float(parsed["plan_stop_pct"]) - (-0.055)) < 0.001
    assert parsed["plan_thesis"] == "白电龙头底仓"


def test_position_list_returns_plan_fields():
    """测试 papertrade_position_list 返回的字典中包含持仓入场计划。"""
    fake_acc = SayuPaperAccount(id=1, name="默认模拟盘", cash=1000000.0)
    pos = SayuPaperPosition(
        id=1,
        account_id=1,
        stock_code="000333",
        stock_name="美的集团",
        secid="0.000333",
        qty=500,
        avg_cost=88.21,
        plan_stop_price=83.36,
        plan_stop_pct=-0.055,
        plan_take_price=98.0,
        plan_thesis="白电龙头底仓",
    )
    enriched_entry = {
        "current_price": 88.56,
        "market_value": 44280.0,
        "unrealized_pnl": 175.0,
        "unrealized_pnl_pct": 0.40,
        "quote_age_seconds": 10,
        "quote_source": "live",
    }

    ctx = AsyncMock()

    with (
        patch.object(ai_tools, "_read_account", AsyncMock(return_value=fake_acc)),
        patch.object(ai_tools, "_get_enriched_positions", AsyncMock(return_value=[(pos, enriched_entry)])),
    ):
        res_str = asyncio.run(ai_tools.papertrade_position_list(ctx=ctx))

    items = json.loads(res_str)
    assert len(items) == 1
    item = items[0]
    assert item["stock_code"] == "000333"
    assert abs(item["plan_stop_price"] - 83.36) < 0.001
    assert abs(item["plan_stop_pct"] - (-0.055)) < 0.001
    assert abs(item["plan_take_price"] - 98.0) < 0.001
    assert item["plan_thesis"] == "白电龙头底仓"


def test_append_with_cash_update_writes_entry_plan(tmp_path):
    """真库跑 ``PaperTradeRepo.append_with_cash_update``：buy 的 snapshot 计划随持仓原子写入。"""
    from sqlalchemy import text
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import gsuid_core.utils.database.base_models as base_models

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/pt.db")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _main():
        # 合成包测试会二次加载 models，同名 Index 重复注册使 create_all 必炸；
        # 只编 CREATE TABLE（含表级约束），本用例不需要二级索引。
        ddl = str(CreateTable(SayuPaperAccount.__table__).compile(dialect=sqlite.dialect()))
        for t in ("sayupaperposition", "sayupapertrade"):
            ddl += ";" + str(CreateTable(SayuPaperAccount.metadata.tables[t]).compile(dialect=sqlite.dialect()))
        async with engine.begin() as conn:
            for stmt in ddl.split(";"):
                await conn.execute(text(stmt))
        original = base_models.async_maker
        base_models.async_maker = maker
        try:
            async with maker() as s:
                acc = SayuPaperAccount(
                    name="测试盘",
                    group_id="111",
                    bot_id="onebot",
                    cash=1000000.0,
                    initial_cash=1000000.0,
                    principal=1000000.0,
                )
                s.add(acc)
                await s.commit()
                acc_id = int(acc.id or 0)
            snap = json.dumps(
                {
                    "plan_stop_price": 83.36,
                    "plan_stop_pct": -0.055,
                    "plan_take_price": 98.0,
                    "plan_thesis": "白电龙头底仓",
                },
                ensure_ascii=False,
            )
            trade, pos_qty = await db.PaperTradeRepo.append_with_cash_update(
                acc_id,
                stock_code="000333",
                stock_name="美的集团",
                secid="0.000333",
                side="buy",
                price=88.21,
                qty=500,
                amount=44105.0,
                fee=5.0,
                snapshot=snap,
            )
            assert pos_qty == 500 and trade.id is not None
            async with maker() as s:
                return (
                    await s.execute(
                        text(
                            "SELECT plan_stop_price, plan_stop_pct, plan_take_price, plan_thesis "
                            "FROM sayupaperposition WHERE account_id = :a"
                        ),
                        {"a": acc_id},
                    )
                ).first()
        finally:
            base_models.async_maker = original
            await engine.dispose()

    pos = asyncio.run(_main())
    assert pos is not None
    assert abs(float(pos[0]) - 83.36) < 0.001
    assert abs(float(pos[1]) - (-0.055)) < 0.001
    assert abs(float(pos[2]) - 98.0) < 0.001
    assert pos[3] == "白电龙头底仓"
