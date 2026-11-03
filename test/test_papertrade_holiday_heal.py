"""模拟盘「假期脏成交自愈」单测。

只测不碰 DB 的纯函数：时间戳解析 + 流水回放 + 账户重算 + 持仓双向对账。
够用且不引入真实 session。时间戳那一组是重点——``text()`` 查询回来的
datetime 列是**字符串**，早期版本解析失败后兜底成 ``now()``，会把每笔历史
成交都标成"今天"，进而在还原时把整个账本当脏数据删光。

持仓对账那一组针对 2026-10-01 美的集团：买入流水还在、持仓行没了，
自愈当时挂在 ``if not dirty`` 早退后面，于是永远修不了这种形状。
"""

import ast
import sys
import asyncio
import inspect
import textwrap
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
    """裸 text() 查询回来的 datetime 列是 ISO 字符串，必须能解析。"""
    got = heal._as_datetime("2026-07-22 09:30:59.871749")
    assert got == datetime(2026, 7, 22, 9, 30, 59, 871749), got
    # 带 T 的 ISO 也要认
    assert heal._as_datetime("2026-07-22T09:30:59") == datetime(2026, 7, 22, 9, 30, 59)
    # 已经是 datetime 的直接过
    assert heal._as_datetime(datetime(2026, 1, 2, 3, 4)) == datetime(2026, 1, 2, 3, 4)
    print("[OK] 时间戳解析：ISO 空格 / ISO T / 原生 datetime")


def test_as_datetime_never_falls_back_to_now():
    """解析不了必须返回 None，**绝不能**兜底 now()。

    兜底 now() 会把每笔历史成交都标成"今天"——今天恰好是休市日时，
    整本账都会被当成脏数据删光。
    """
    for junk in ("", "  ", "not-a-date", "0000-00-00", None, 12345):
        assert heal._as_datetime(junk) is None, junk
    print("[OK] 时间戳解析失败一律 None，不兜底 now()")


# ============================================================
# 流水回放
# ============================================================


def test_replay_matches_matcher_cost_basis():
    """回放口径必须与线上 ``apply_fill_qty_cost`` 逐步一致。"""
    t = [_trade(1, "buy", 10.0, 1000, "2026-09-28"), _trade(2, "buy", 12.0, 1000, "2026-09-29")]
    finals = heal._replay_final(t)
    st = finals["600036"]
    # 逐步推进：每一步都拿撮合层的输出当下一步输入，口径错一步就会分叉
    qty1, avg1 = matcher.apply_fill_qty_cost(0, 0.0, "buy", 1000, 10.0, 5.0)
    qty2, avg2 = matcher.apply_fill_qty_cost(qty1, avg1, "buy", 1000, 12.0, 5.0)
    assert st.qty == qty2 == 2000
    assert abs(st.avg - avg2) < 1e-9, (st.avg, avg2)
    # (0 + 1000×10 + 5)/1000 = 10.005 → (1000×10.005 + 1000×12 + 5)/2000 = 11.005
    assert abs(st.avg - 11.005) < 1e-9, st.avg
    print(f"[OK] 回放均价 {st.avg:.6f} 与撮合层逐步一致")


def test_replay_full_close_resets_opened_at():
    """清仓后开仓日要重置，下次再买才算新开仓。"""
    t = [
        _trade(1, "buy", 10.0, 1000, "2026-09-28"),
        _trade(2, "sell", 12.0, 1000, "2026-09-29"),
        _trade(3, "buy", 11.0, 500, "2026-09-30"),
    ]
    st = heal._replay_final(t)["600036"]
    assert st.qty == 500
    assert st.opened_on == date(2026, 9, 30), st.opened_on
    print("[OK] 清仓后 opened_at 重置为再次建仓日")


def test_holdings_on_marks_snapshots_before_next_fill():
    """净值快照记的是**当日收盘**的持仓：下一笔成交之前先定格。"""
    t = [_trade(1, "buy", 10.0, 1000, "2026-09-28"), _trade(2, "buy", 12.0, 1000, "2026-09-30")]
    marks = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]
    snap = heal._holdings_on(t, marks)
    assert snap[date(2026, 9, 28)]["600036"].qty == 1000
    assert snap[date(2026, 9, 29)]["600036"].qty == 1000
    assert snap[date(2026, 9, 30)]["600036"].qty == 2000
    print("[OK] 快照按日定格：9-28/9-29 各 1000 股，9-30 才 2000 股")


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


# ============================================================
# 持仓 ↔ 流水 双向对账（2026-10-01 美的缺口）
# ============================================================


def _midea(tid: int, code: str, name: str, side: str, price: float, qty: int, day: str):
    fee = 5.0 if side == "buy" else 10.0
    return heal._Trade(
        id=tid,
        account_id=1,
        code=code,
        name=name,
        secid="0." + code,
        side=side,
        price=price,
        qty=qty,
        amount=price * qty,
        fee=fee,
        realized_pnl=0.0,
        decided_at=datetime.fromisoformat(f"{day} 10:00:00"),
        executed_on=date.fromisoformat(day),
    )


def test_plan_positions_flags_missing_position_from_ledger():
    """**回归**：买入流水还在、持仓行没了 → 必须报"新建"。

    这就是美的 10-01 的形状：LLM 绕开 trade_insert 直接把 qty 置 0，
    持仓行被删、卖出现金与流水都没写，群里却播报了卖出。自愈以前挂在
    脏成交早退后面，没有脏流水就返回，这种形状永远修不了。
    """
    ledger = [_midea(12, "000333", "美的集团", "buy", 83.97, 500, "2026-07-22")]
    fixes = heal._plan_positions(ledger, {}, frozenset({1}))

    assert len(fixes) == 1, fixes
    f = fixes[0]
    assert f["created"] is True, "持仓缺失时必须标记为新建，而不是被跳过"
    assert f["removed"] is False
    assert f["stock_code"] == "000333"
    assert f["stock_name"] == "美的集团"
    assert f["new_qty"] == 500
    # 期望值由撮合层算，不手填常数：买费用计入成本，均价 = (500×83.97 + 5)/500。
    _, want_avg = matcher.apply_fill_qty_cost(0, 0.0, "buy", 500, 83.97, 5.0)
    assert abs(f["new_avg_cost"] - want_avg) < 1e-3, (f["new_avg_cost"], want_avg)
    assert f["old_qty"] == 0
    print(f"[OK] 流水有/持仓缺 → 新建 {f['stock_code']} {f['new_qty']}股@{f['new_avg_cost']:.3f}")


def test_plan_positions_still_deletes_ghost_holding():
    """反向：持仓有、流水净头寸 ≤ 0 → 仍要报删除（幽灵仓）。"""
    ledger = [_midea(12, "000333", "美的集团", "buy", 83.97, 500, "2026-07-22")]
    old = {(1, "000333"): (500, 83.98, 80.1)}
    assert heal._plan_positions(ledger, old, frozenset({1})) == [], "一致时必须空操作（幂等）"

    # 把买卖都从流水里拿掉，持仓行就成了没有流水支撑的幽灵
    fixes = heal._plan_positions([], old, frozenset({1}))
    assert len(fixes) == 1
    assert fixes[0]["removed"] is True
    assert fixes[0]["created"] is False
    print("[OK] 持仓有/流水净零 → 标记删除（幽灵仓）")


def test_plan_positions_ignores_orphan_account_trades():
    """流水 account_id 对不上真实账户时不得建仓。

    盘被删过会留下孤儿流水；按它建出来的持仓行永远没人读，还会污染统计。
    """
    orphan = _midea(1, "000333", "美的集团", "buy", 83.97, 500, "2026-07-22")
    orphan.account_id = 99
    assert heal._plan_positions([orphan], {}, frozenset({1})) == []
    print("[OK] 孤儿 account_id 的流水不参与建仓")


def test_plan_positions_detects_partial_qty_drift():
    """股数被改小（既不是 0 也不是原值）也要能纠回来。"""
    ledger = [_midea(12, "000333", "美的集团", "buy", 83.97, 500, "2026-07-22")]
    old = {(1, "000333"): (200, 83.98, 80.1)}
    fixes = heal._plan_positions(ledger, old, frozenset({1}))
    assert len(fixes) == 1
    f = fixes[0]
    assert f["created"] is False and f["removed"] is False
    assert (f["old_qty"], f["new_qty"]) == (200, 500)
    print(f"[OK] 股数漂移 {f['old_qty']} → {f['new_qty']} 被纠正")


def test_plan_positions_leaves_cost_basis_drift_alone():
    """股数一致、只有成本价口径不同时不得改写（口径演进 ≠ 损坏）。

    早期 ``calc_new_avg_cost`` 不把买费用计入成本，老账里那批持仓按新口径
    回放必然差几分钱。自动改写会让用户看到持仓成本凭空跳一下，所以只报告。
    """
    ledger = [_midea(10, "600309", "万华化学", "buy", 73.58, 500, "2026-07-22")]
    old = {(1, "600309"): (500, 73.58, 73.0)}  # 老口径：不含费
    fixes = heal._plan_positions(ledger, old, frozenset({1}))
    assert len(fixes) == 1
    f = fixes[0]
    assert f["cost_only"] is True, "股数一致、仅成本漂移应标为 cost_only"
    assert f["created"] is False and f["removed"] is False
    assert f["new_qty"] == 500
    # 含费口径：(500×73.58 + 5)/500 = 73.59
    assert abs(f["new_avg_cost"] - 73.59) < 1e-3, f["new_avg_cost"]
    print(f"[OK] 成本口径漂移 {f['old_avg_cost']} → {f['new_avg_cost']} 仅报告不落库")


def test_no_dirty_path_commits_position_reconcile():
    """**回归**：无脏成交那条路径必须 commit，否则补回的持仓等于没写。

    ``async_maker()`` 是普通 AsyncSession，退出 ``with`` **不会**自动提交
    （已实测：不 commit 时 INSERT 离开会话后查不到）。所以把持仓对账放在
    ``if not dirty: return`` 之前还不够——那条 return 之前也必须 commit，
    否则"重启就自动补回持仓"只是看起来成立：日志说对账了，库里什么都没有。
    """
    import ast
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(heal.heal_holiday_trades)))

    # 找出所有 return 语句，看哪些在"有写入"之后还没 commit
    returns: list[int] = []
    commits: list[int] = []
    writes: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return):
            returns.append(node.lineno)
        elif isinstance(node, ast.Call):
            seg = ast.dump(node)
            if "commit" in seg and getattr(node.func, "attr", "") == "commit":
                commits.append(node.lineno)
            if "apply_positions" in seg:
                writes.append(node.lineno)
    assert writes, "源码里找不到持仓写入调用"
    assert commits, "源码里找不到任何 commit"

    # 写入点之后必须有 commit 覆盖：最晚的写入行 < 某次 commit 行
    last_write = max(writes)
    assert any(c > last_write for c in commits), (
        f"最晚的持仓写入在第 {last_write} 行，之后没有任何 commit：写了也不会落库"
    )
    print(f"[OK] 持仓写入（第 {last_write} 行）之后有 commit（共 {len(commits)} 处）")


def test_async_session_does_not_autocommit():
    """底层前提：``async_maker()`` 不自动提交。

    这条是上面那条的意义所在。若哪天框架改成自动提交，上面的断言会变成
    保守冗余（仍成立）；但若反之（今天有人误以为会自动提交而省掉 commit），
    就会静默丢数据。
    """
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # 必须用文件库：``:memory:`` 下 SQLite 每次连接都是一个全新的空库，
    # 第二次 session 拿到的是另一张表，查不到任何行——那证明的是别的事情。
    tmp = Path(__file__).resolve().parent / "_probe_autocommit.db"
    if tmp.exists():
        tmp.unlink()

    async def probe() -> int:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.as_posix()}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.execute(sa_text("CREATE TABLE probe (v TEXT)"))
        async with maker() as s:
            await s.execute(sa_text("INSERT INTO probe VALUES ('x')"))
            # 故意不 commit
        async with maker() as s:
            res = (await s.execute(sa_text("SELECT count(*) FROM probe"))).scalar()
            assert res is not None
            count = int(res)
        # Windows 上连接池还攥着文件句柄，必须在删文件前 dispose
        await engine.dispose()
        return count

    rows = asyncio.run(probe())
    tmp.unlink(missing_ok=True)
    assert rows == 0, "session 退出时若自动提交了，本前提就不成立"
    print("[OK] async session 不自动提交：不 commit 等于没写")


def test_reconcile_is_not_gated_on_dirty_trades():
    """对账绝不能挂在"有没有脏成交"这个条件后面。

    回归：原实现在没有脏成交时直接 return，持仓重建根本走不到。
    现金/本金对账同理——误删的流水被补回后，账也必须无条件对平。
    用 AST 判定语句顺序——不能靠 ``str.index``：注释里同样会出现
    ``if not dirty`` 字面量，先命中的会是注释而不是真代码。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(heal.heal_holiday_trades)))
    plan_line = None
    cash_line = None
    guard_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            ids = [n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            if plan_line is None and "pos_fixes" in ids:
                plan_line = node.lineno
            if cash_line is None and "cash_fixes" in ids:
                cash_line = node.lineno
        elif isinstance(node, ast.If) and guard_line is None:
            # ``if not dirty`` 可能裹在 ``and``/``or`` 里（如 and not restored_applied），
            # 在 test 子树里找 not dirty 而不是只看顶层节点
            if any(
                isinstance(sub, ast.UnaryOp)
                and isinstance(sub.op, ast.Not)
                and isinstance(sub.operand, ast.Name)
                and sub.operand.id == "dirty"
                for sub in ast.walk(node.test)
            ):
                guard_line = node.lineno
    assert plan_line is not None, "源码里找不到 pos_fixes 的结果赋值"
    assert cash_line is not None, "源码里找不到 cash_fixes 的结果赋值"
    assert guard_line is not None, "源码里找不到 not dirty 早退分支"
    assert plan_line < guard_line, (
        f"持仓对账在第 {plan_line} 行、早退在第 {guard_line} 行：没有脏流水时永远不会重建缺失的持仓"
    )
    assert cash_line < guard_line, (
        f"现金对账在第 {cash_line} 行、早退在第 {guard_line} 行：没有脏流水时永远不会对平现金/本金"
    )
    print(f"[OK] 持仓/现金对账（第 {plan_line}/{cash_line} 行）都排在 not dirty 早退（第 {guard_line} 行）之前")


def test_restore_archived_revives_only_proven_trading_days():
    """**回归 2026-11-02**：归档行成交日有上证 bar → 自动复活，幂等。

    归档不是只进不出的垃圾桶：被误删的流水（10-30 卖 1000、11-02 卖 500）
    靠「day ∈ 上证日 K 交易日集合」这条硬证据复活。真休市日与库里现存的
    行都不能复活——前者删除本来就对，后者复活就是双记。
    """
    import json as _json
    import asyncio

    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tmp_dir = Path(__file__).resolve().parent / "_restore_probe"
    tmp_dir.mkdir(exist_ok=True)
    db_file = tmp_dir / "probe.db"
    archive = tmp_dir / "papertrade_holiday_archive_20261102-165755.json"
    if db_file.exists():
        db_file.unlink()

    def _archived(tid: int, side: str, price: float, qty: int, day: str, pnl: float = 0.0) -> dict[str, object]:
        return {
            "id": tid,
            "account_id": 1,
            "stock_code": "600028",
            "stock_name": "中国石化",
            "secid": "1.600028",
            "side": side,
            "price": price,
            "qty": qty,
            "amount": price * qty,
            "fee": 10.0,
            "realized_pnl": pnl,
            "decided_at": f"{day}T10:00:00.123456",
        }

    archive.write_text(
        _json.dumps(
            {
                "archived_at": "2026-11-02T16:57:55",
                "dirty_days": ["2026-11-02"],
                "count": 3,
                "trades": [
                    _archived(49, "sell", 5.87, 500, "2026-11-02", pnl=178.0),
                    _archived(40, "buy", 5.8, 100, "2026-10-01"),
                    _archived(41, "buy", 5.5, 9000, "2026-10-16"),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def _fake_trading():
        return frozenset({"2026-10-16", "2026-10-30", "2026-11-02"}), "test"

    async def run() -> tuple[int, list[str], list[tuple], int]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.execute(
                sa_text("CREATE TABLE sayupaperaccount (id INTEGER PRIMARY KEY, group_id TEXT, bot_id TEXT, mode TEXT)")
            )
            await conn.execute(
                sa_text(
                    "CREATE TABLE sayupapertrade (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "account_id INTEGER, group_id TEXT, bot_id TEXT, stock_code TEXT, "
                    "stock_name TEXT, secid TEXT, side TEXT, price REAL, qty INTEGER, "
                    "amount REAL, fee REAL, realized_pnl REAL, reason TEXT, snapshot TEXT, "
                    "decided_at DATETIME, executed_at DATETIME, mode TEXT)"
                )
            )
            await conn.execute(sa_text("INSERT INTO sayupaperaccount VALUES (1, 'g1', 'b1', 'balanced')"))
            # 库里已有一笔 10-16 买入：与归档里那笔同键，必须靠去重跳过而不是双记
            await conn.execute(
                sa_text(
                    "INSERT INTO sayupapertrade (account_id, stock_code, stock_name, secid, side, "
                    "price, qty, amount, fee, realized_pnl, reason, snapshot, decided_at, executed_at, mode) "
                    "VALUES (1, '600028', '中国石化', '1.600028', 'buy', 5.5, 9000, 49500.0, 25.0, 0.0, "
                    "'', '', '2026-10-16 10:00:00.123456', '2026-10-16 10:00:00.123456', 'balanced')"
                )
            )
        async with maker() as s:
            existing, _ = await heal._load_trades(s)
            n, days = await heal._restore_archived(s, existing, dry_run=False)
            await s.commit()
        # 独立连接复查：同一个 session 会命中未提交事务，骗过自己
        async with maker() as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT account_id, stock_code, side, qty, price, reason, group_id, bot_id, mode "
                        "FROM sayupapertrade ORDER BY id"
                    )
                )
            ).all()
            # 幂等：复跑时复活行已在库里，靠 _load_trades 的键去重
            existing2, _ = await heal._load_trades(s)
            n2, _ = await heal._restore_archived(s, existing2, dry_run=False)
        await engine.dispose()
        return n, days, [tuple(r) for r in rows], n2

    old_rtd, old_dp = heal.resolve_trading_days, heal.DATA_PATH
    setattr(heal, "resolve_trading_days", _fake_trading)
    setattr(heal, "DATA_PATH", tmp_dir)
    try:
        n, days, rows, n2 = asyncio.run(run())
    finally:
        heal.resolve_trading_days, heal.DATA_PATH = old_rtd, old_dp
        archive.unlink(missing_ok=True)
        db_file.unlink(missing_ok=True)
        tmp_dir.rmdir()

    assert n == 1, n
    assert days == ["2026-11-02"], days
    assert len(rows) == 2, rows
    aid, code, side, qty, price, reason, gid, bid, mode = rows[1]
    assert (aid, code, side, qty, price) == (1, "600028", "sell", 500, 5.87), rows[1]
    assert reason == "归档还原" and gid == "g1" and bid == "b1" and mode == "balanced", rows[1]
    assert n2 == 0, "复活行已入库，复跑必须幂等"
    print(f"[OK] 归档还原：交易日行复活 {n} 笔（{days}），休市日/已存在行跳过，复跑 {n2} 笔")


def test_same_day_trades_are_never_dirty():
    """**回归 2026-11-02**：当天成交永不判脏。

    收盘后自愈跑起来时，当日日 K 往往还没入库——早盘生成的 24h K 线缓存
    （``get_stock_kline`` TTL=1440min）就没有当天 bar，当天被判休市后
    09:35 卖 500 股 / 10-30 卖 1000 股两次真实成交被误删。当天能落库
    就说明过了盘中实时闸，宁可等次日证实再清。
    """
    today = date(2026, 11, 2)
    closed = frozenset({"2026-11-02", "2026-10-30", "2026-10-01"})
    same_day = _trade(90, "sell", 5.87, 500, "2026-11-02")
    past_closed = _trade(89, "sell", 5.8, 1000, "2026-10-30")
    open_day = _trade(88, "buy", 5.5, 1000, "2026-10-16")
    dirty = heal._dirty_trades([same_day, past_closed, open_day], closed, today)
    assert [t.id for t in dirty] == [89], [t.id for t in dirty]
    print("[OK] 当天命中休市表也不删；已证实的历史休市日成交仍清")


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
    test_plan_positions_flags_missing_position_from_ledger()
    test_plan_positions_still_deletes_ghost_holding()
    test_plan_positions_ignores_orphan_account_trades()
    test_plan_positions_detects_partial_qty_drift()
    test_plan_positions_leaves_cost_basis_drift_alone()
    test_no_dirty_path_commits_position_reconcile()
    test_async_session_does_not_autocommit()
    test_reconcile_is_not_gated_on_dirty_trades()
    test_restore_archived_revives_only_proven_trading_days()
    test_same_day_trades_are_never_dirty()
    print("\n[SUCCESS] holiday_heal 全部测试通过！")
