"""模拟盘「假期脏成交自愈」单测。

只测不碰 DB 的纯函数：时间戳解析 + 流水回放 + 账户重算。够用且不引入
真实 session。时间戳那一组是重点——``text()`` 查询回来的 datetime 列是
**字符串**，早期版本解析失败后兜底成 ``now()``，会把每笔历史成交都标成
"今天"，进而在还原时把整个账本当脏数据删光。
"""

import sys
import importlib.util
from types import ModuleType
from pathlib import Path
from datetime import date, datetime

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_heal_test"


def _ensure_pkg() -> None:
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME, PKG_ROOT / "__init__.py", submodule_search_locations=[str(PKG_ROOT)]
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
    # 嵌套包壳：holiday_heal 用相对导入（.trading_calendar / ..utils.*），
    # 必须有 stock_papertrade 这一层才能解析，否则 collection 直接 ImportError。
    sub_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade",
        PKG_ROOT / "stock_papertrade" / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT / "stock_papertrade")],
    )
    assert sub_spec is not None
    sub = importlib.util.module_from_spec(sub_spec)
    sub.__path__ = [str(PKG_ROOT / "stock_papertrade")]
    sys.modules[f"{PKG_NAME}.stock_papertrade"] = sub


def _load(name: str, file_name: str) -> ModuleType:
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG_NAME}.stock_papertrade.{name}", PKG_ROOT / file_name)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


matcher = _load("matcher", "stock_papertrade/matcher.py")
heal = _load("holiday_heal", "stock_papertrade/holiday_heal.py")


def _trade(tid: int, side: str, price: float, qty: int, day: str, pnl: float = 0.0):
    fee = 5.0 if side == "buy" else 10.0
    return heal._Trade(
        id=tid,
        account_id=1,
        code="600036",
        name="招商银行",
        secid="1.600036",
        side=side,
        price=price,
        qty=qty,
        amount=price * qty,
        fee=fee,
        realized_pnl=pnl,
        decided_at=datetime.fromisoformat(f"{day} 10:00:00"),
        executed_on=date.fromisoformat(day),
    )


# ============================================================
# 时间戳解析（回归重点）
# ============================================================
def test_as_datetime_parses_sqlite_string():
    """裸 text() 查询回来的是字符串，必须能解析。"""
    assert heal._as_datetime("2026-10-01 10:00:00") == datetime(2026, 10, 1, 10, 0, 0)
    assert heal._as_datetime("2026-10-01 10:00:00.123456") == datetime(2026, 10, 1, 10, 0, 0, 123456)
    assert heal._as_datetime("2026-10-01T10:00:00") == datetime(2026, 10, 1, 10, 0, 0)
    assert heal._as_datetime("2026-10-01") == datetime(2026, 10, 1, 0, 0, 0)
    assert heal._as_datetime(datetime(2026, 10, 1, 10, 0)) == datetime(2026, 10, 1, 10, 0)
    assert heal._as_datetime(date(2026, 10, 1)) == datetime(2026, 10, 1, 0, 0, 0)
    print("[OK] 时间戳：str / 带微秒 / ISO T / 纯日期 / datetime 都能解析")


def test_as_datetime_never_falls_back_to_now():
    """解析不了必须返回 None——兜底成 now() 会把全账本标成今天的脏数据。"""
    for bad in (None, "", "   ", "not-a-date", 12345, True, []):
        assert heal._as_datetime(bad) is None, f"{bad!r} 应返回 None"
    print("[OK] 时间戳：无法解析一律 None，绝不兜底 now()")


# ============================================================
# 流水回放
# ============================================================
def test_replay_matches_matcher_cost_basis():
    """回放出的成本口径必须与撮合层 apply_fill_qty_cost 完全一致（含手续费）。"""
    trades = [_trade(1, "buy", 10.0, 1000, "2026-09-28"), _trade(2, "buy", 12.0, 1000, "2026-09-29")]
    states = heal._replay_final(trades)

    expect_qty, expect_avg = 0, 0.0
    for t in trades:
        expect_qty, expect_avg = matcher.apply_fill_qty_cost(expect_qty, expect_avg, t.side, t.qty, t.price, t.fee)
    assert states["600036"].qty == expect_qty == 2000
    assert abs(states["600036"].avg - expect_avg) < 1e-9
    assert abs(states["600036"].avg - 11.005) < 1e-9
    print(f"[OK] 回放成本 {states['600036'].avg} 与撮合层一致")


def test_replay_full_close_resets_opened_at():
    """清仓后开仓日要重置——线上是删行重建，下一笔买入重新计开仓日。"""
    trades = [
        _trade(1, "buy", 10.0, 1000, "2026-09-28"),
        _trade(2, "sell", 15.0, 1000, "2026-09-29", pnl=3985.0),
        _trade(3, "buy", 20.0, 500, "2026-09-30"),
    ]
    st = heal._replay_final(trades)["600036"]
    assert st.qty == 500
    assert st.opened_on == date(2026, 9, 30)
    assert abs(st.avg - 20.01) < 1e-9
    print("[OK] 清仓后重开仓，开仓日重置为最后一笔买入日")


def test_holdings_on_marks_snapshots_before_next_fill():
    """mark 日要记录"该日收盘时"的持仓，而不是当天最后一笔成交之后。"""
    trades = [
        _trade(1, "buy", 10.0, 1000, "2026-09-28"),
        _trade(2, "buy", 12.0, 1000, "2026-09-29"),
        _trade(3, "sell", 15.0, 1000, "2026-10-08"),
    ]
    marks = [date(2026, 10, 1), date(2026, 10, 8)]
    held = heal._holdings_on(trades, marks)
    # 10-01 收盘时仍持 2000 股（卖出发生在 10-08）
    assert held[date(2026, 10, 1)]["600036"].qty == 2000
    # 10-08 收盘时剩 1000 股
    assert held[date(2026, 10, 8)]["600036"].qty == 1000
    print("[OK] mark 日持仓取的是当日收盘时点，不是最后一笔之后")


# ============================================================
# 账户重算
# ============================================================
def test_plan_accounts_recomputes_cash_and_principal():
    """删掉假成交后，现金与本金都要按留存流水重算。"""
    accounts = {1: ("测试盘", 1_000_000.0, 964_985.0, 1_014_685.0)}
    keep = [
        _trade(1, "buy", 10.0, 1000, "2026-09-28"),
        _trade(2, "buy", 12.0, 1000, "2026-09-29"),
        _trade(4, "sell", 15.0, 1000, "2026-10-08", pnl=3985.0),
    ]
    old_pos = {(1, "600036"): (1000, 11.6717, 13.0)}
    cash_fixes, pos_fixes = heal._plan_accounts(accounts, keep, old_pos)

    assert len(cash_fixes) == 1
    fix = cash_fixes[0]
    # 1,000,000 - (10,000+5) - (12,000+5) + (15,000-10)
    assert abs(fix["new_cash"] - 992_980.0) < 0.01, fix
    # 本金 = 期初 + 已实现盈亏，realized_pnl 不进现金
    assert abs(fix["new_principal"] - 1_003_985.0) < 0.01, fix

    assert len(pos_fixes) == 1
    p = pos_fixes[0]
    assert p["old_qty"] == 1000 and p["new_qty"] == 1000
    assert abs(p["new_avg_cost"] - 11.005) < 1e-4
    assert p["removed"] is False
    print(f"[OK] 现金→{fix['new_cash']:,.2f} 本金→{fix['new_principal']:,.2f} 均价→{p['new_avg_cost']}")


def test_plan_accounts_noop_when_ledger_already_consistent():
    """账本本来就自洽时不该报更正（幂等，重复启动不会刷日志）。"""
    accounts = {1: ("测试盘", 1_000_000.0, 992_980.0, 1_003_985.0)}
    keep = [
        _trade(1, "buy", 10.0, 1000, "2026-09-28"),
        _trade(2, "buy", 12.0, 1000, "2026-09-29"),
        _trade(4, "sell", 15.0, 1000, "2026-10-08", pnl=3985.0),
    ]
    old_pos = {(1, "600036"): (1000, 11.005, 15.0)}
    cash_fixes, pos_fixes = heal._plan_accounts(accounts, keep, old_pos)
    assert cash_fixes == []
    assert pos_fixes == []
    print("[OK] 账本已自洽时无更正项（幂等）")


def test_plan_accounts_marks_position_removed():
    """还原后该票不再持有时，持仓行要标记删除。"""
    accounts = {1: ("测试盘", 1_000_000.0, 500_000.0, 1_000_000.0)}
    keep = [_trade(1, "buy", 10.0, 1000, "2026-09-28")]
    old_pos = {(1, "600036"): (1000, 10.005, 10.0), (1, "000333"): (500, 83.97, 87.0)}
    cash_fixes, pos_fixes = heal._plan_accounts(accounts, keep, old_pos)
    by_code = {p["stock_code"]: p for p in pos_fixes}
    assert by_code["000333"]["removed"] is True
    assert "000333" not in [p["stock_code"] for p in pos_fixes if not p["removed"]]
    print("[OK] 还原后不再持有的票标记 removed=True")


def test_close_for_uses_nearest_earlier_bar():
    """休市日没有 K 线，取该日之前最近一根收盘价。"""
    table = {"600036": {date(2026, 9, 29): 12.0, date(2026, 10, 8): 20.0}}
    assert heal._close_for(table, "600036", date(2026, 9, 29), 0.0) == 12.0
    # 10-01 无 bar → 退回 09-29
    assert heal._close_for(table, "600036", date(2026, 10, 1), 0.0) == 12.0
    # 该票完全没数据 → 退回成本价
    assert heal._close_for({}, "600036", date(2026, 10, 1), 11.005) == 11.005
    print("[OK] 收盘价回溯：当日 → 之前最近一根 → 成本价兜底")


def test_plan_accounts_keeps_full_code_for_new_position():
    """新建持仓（原本没有持仓行）时股票代码必须是完整 6 位。

    回归：曾经把 ``set(finals)``（键是裸 code 字符串）和 old_positions 的
    ``(account_id, code)`` 元组键直接取并集，键类型退化成联合，``key[1]``
    在裸字符串上取到的是**第二个字符**，代码会被写成 "0"。
    """
    # 现金按留存流水算是 1,000,000 - 10,000 - 5 = 989,995，填成自洽值，
    # 这样唯一的更正项就只剩"新建持仓行"
    accounts = {1: ("测试盘", 1_000_000.0, 989_995.0, 1_000_000.0)}
    keep = [_trade(1, "buy", 10.0, 1000, "2026-09-28")]
    cash_fixes, pos_fixes = heal._plan_accounts(accounts, keep, {})
    assert cash_fixes == []
    assert len(pos_fixes) == 1
    assert pos_fixes[0]["stock_code"] == "600036"
    assert pos_fixes[0]["new_qty"] == 1000
    assert pos_fixes[0]["stock_name"] == "招商银行"
    print("[OK] 新建持仓的股票代码保持完整 6 位")


def test_circuit_breaker_thresholds_are_conservative():
    """熔断阈值必须卡得住"判据坏了"这种灾难场景。"""
    assert 0.0 < heal._MAX_DIRTY_SHARE < 0.5
    assert 0 < heal._MAX_DIRTY_ROWS <= 1000
    print(f"[OK] 熔断阈值：脏数据占比 > {heal._MAX_DIRTY_SHARE:.0%} 或 笔数 > {heal._MAX_DIRTY_ROWS} 即放弃")


if __name__ == "__main__":
    test_as_datetime_parses_sqlite_string()
    test_as_datetime_never_falls_back_to_now()
    test_replay_matches_matcher_cost_basis()
    test_replay_full_close_resets_opened_at()
    test_holdings_on_marks_snapshots_before_next_fill()
    test_plan_accounts_recomputes_cash_and_principal()
    test_plan_accounts_noop_when_ledger_already_consistent()
    test_plan_accounts_marks_position_removed()
    test_close_for_uses_nearest_earlier_bar()
    test_plan_accounts_keeps_full_code_for_new_position()
    test_circuit_breaker_thresholds_are_conservative()
    print("\n[SUCCESS] holiday_heal 全部测试通过！")
