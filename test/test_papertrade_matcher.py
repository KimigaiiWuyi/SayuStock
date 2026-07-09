"""AI 模拟盘撮合单测。

覆盖：
- 100 股整手化
- buy 现金不足降档
- sell 持仓不足截断
- 费率计算（佣金 + 印花税）
- 已实现盈亏
- 加权平均成本
"""

import sys
import importlib.util
from types import ModuleType
from pathlib import Path

# 把仓库根目录加入 sys.path，以便能 import gsuid_core
# 文件: E:/MyPyProject/gsuid_core/gsuid_core/plugins/SayuStock/test/test_xxx.py
# 5 个 .parent 回到 E:/MyPyProject/gsuid_core/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 直接加载 stock_papertrade 包下的 matcher.py，绕过 SayuStock/__init__.py
# （后者会触发 from gsuid_core.sv import Plugins，未配置环境会报错）
PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_test_pkg"


def _load_submodule(name: str, file_name: str) -> ModuleType:
    """手动加载 stock_papertrade.<file_name> 作为一个 standalone module"""
    spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade.{name}",
        PKG_ROOT / "stock_papertrade" / file_name,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# 准备包对象
if PKG_NAME not in sys.modules:
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        PKG_ROOT / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT)],
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
    sub_pkg = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade",
        PKG_ROOT / "stock_papertrade" / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT / "stock_papertrade")],
    )
    assert sub_pkg is not None
    sub = importlib.util.module_from_spec(sub_pkg)
    sub.__path__ = [str(PKG_ROOT / "stock_papertrade")]
    sys.modules[f"{PKG_NAME}.stock_papertrade"] = sub

matcher = _load_submodule("matcher", "matcher.py")

COMMISSION_MIN = matcher.COMMISSION_MIN
COMMISSION_RATE = matcher.COMMISSION_RATE
LOT_SIZE = matcher.LOT_SIZE
STAMP_TAX_RATE = matcher.STAMP_TAX_RATE
MatchResult = matcher.MatchResult
calc_fee = matcher.calc_fee
calc_new_avg_cost = matcher.calc_new_avg_cost
calc_realized_pnl = matcher.calc_realized_pnl
match_order = matcher.match_order
round_lot = matcher.round_lot


def test_round_lot():
    """100 股整手化"""
    assert round_lot(0) == 0
    assert round_lot(50) == 0
    assert round_lot(99) == 0
    assert round_lot(100) == 100
    assert round_lot(150) == 100
    assert round_lot(199) == 100
    assert round_lot(200) == 200
    assert round_lot(250) == 200
    assert round_lot(1234) == 1200
    print("[OK] round_lot 整手化正确")


def test_calc_fee_buy():
    """buy 费率 = max(amount*0.00025, 5)，无印花税"""
    commission, stamp_tax, total = calc_fee("buy", 100_000)
    expected_commission = max(100_000 * COMMISSION_RATE, COMMISSION_MIN)
    assert commission == expected_commission
    assert stamp_tax == 0.0
    assert total == expected_commission
    print("[OK] buy 费率计算正确")


def test_calc_fee_sell():
    """sell 费率 = 佣金 + 印花税（万 5 = 0.0005）"""
    commission, stamp_tax, total = calc_fee("sell", 100_000)
    expected_commission = max(100_000 * COMMISSION_RATE, COMMISSION_MIN)
    expected_stamp = 100_000 * STAMP_TAX_RATE  # 50
    assert commission == expected_commission
    assert abs(stamp_tax - expected_stamp) < 1e-6
    assert abs(total - (expected_commission + expected_stamp)) < 1e-6
    print("[OK] sell 费率计算正确（含印花税）")


def test_calc_fee_commission_min():
    """小额交易时佣金 = 5 元保底"""
    commission, _, _ = calc_fee("buy", 100)  # 100*0.00025 = 0.025 < 5
    assert commission == 5.0
    print("[OK] 小额交易佣金保底 5 元")


def test_match_order_buy_normal():
    """正常 buy：100 股 @ 100 元 = 10000，扣 5 佣金后仍能买"""
    res = match_order("buy", "600519", 100, 100.0, cash_available=200_000, position_qty=0)
    assert res.ok is True
    assert res.actual_qty == 100
    assert res.amount == 10_000
    assert res.commission == 5.0  # max(10000*0.00025, 5) = max(2.5, 5) = 5
    assert res.stamp_tax == 0.0
    assert res.fee_total == 5.0
    print("[OK] 正常 buy 撮合成功")


def test_match_order_buy_large():
    """大额 buy：1000 股 @ 100 元 = 100000"""
    res = match_order("buy", "600519", 1000, 100.0, cash_available=200_000, position_qty=0)
    assert res.ok is True
    assert res.actual_qty == 1000
    assert res.amount == 100_000
    assert res.commission == 25.0  # 100000*0.00025 = 25
    assert res.fee_total == 25.0
    print("[OK] 大额 buy 撮合成功（佣金按比例）")


def test_match_order_buy_insufficient_cash():
    """现金不足时降档：500 股要 50000，但只有 30000，自动降到 200 股"""
    res = match_order("buy", "600519", 500, 100.0, cash_available=30_000, position_qty=0)
    # available after 5 块佣金保底 = (30000 - 5) / 1.00025 = 29987.5
    # qty = 29987.5 / 100 = 299.87 → round to 200
    assert res.ok is True
    assert res.actual_qty == 200
    assert res.amount == 20_000
    print("[OK] 现金不足时自动降档到 200 股")


def test_match_order_buy_cant_afford_even_one_lot():
    """现金不够买 100 股时被拒"""
    res = match_order("buy", "600519", 100, 1000.0, cash_available=5_000, position_qty=0)
    # amount = 100000 > 5000，无法买 100 股
    assert res.ok is False
    assert "现金不足" in res.reason
    print("[OK] 现金不足时正确拒绝")


def test_match_order_sell_normal():
    """正常 sell"""
    res = match_order("sell", "600519", 100, 105.0, cash_available=0, position_qty=500)
    assert res.ok is True
    assert res.actual_qty == 100
    assert res.amount == 10_500
    assert res.commission == 5.0  # 10500*0.00025 = 2.625 → 5
    assert abs(res.stamp_tax - 5.25) < 1e-6  # 10500*0.0005
    assert abs(res.fee_total - 10.25) < 1e-6  # 5 + 5.25
    print("[OK] 正常 sell 撮合成功（含印花税）")


def test_match_order_sell_truncate_to_position():
    """卖出量超过持仓时截断"""
    res = match_order("sell", "600519", 500, 100.0, cash_available=0, position_qty=300)
    assert res.ok is True
    assert res.actual_qty == 300
    assert res.amount == 30_000
    print("[OK] 卖出量超持仓自动截断")


def test_match_order_sell_no_position():
    """无持仓时拒绝 sell"""
    res = match_order("sell", "600519", 100, 100.0, cash_available=0, position_qty=0)
    assert res.ok is False
    assert "持仓不足" in res.reason
    print("[OK] 无持仓 sell 被拒绝")


def test_match_order_less_than_one_lot():
    """不足 100 股被拒绝"""
    res = match_order("buy", "600519", 50, 100.0, cash_available=100_000, position_qty=0)
    assert res.ok is False
    assert "不足一整手" in res.reason
    print("[OK] 不足整手被拒绝")


def test_match_order_invalid_side():
    """非法 side 被拒"""
    res = match_order("invalid", "600519", 100, 100.0, cash_available=100_000, position_qty=0)
    assert res.ok is False
    assert "非法方向" in res.reason
    print("[OK] 非法 side 被拒绝")


def test_match_order_invalid_price():
    """非法价格被拒"""
    res = match_order("buy", "600519", 100, 0.0, cash_available=100_000, position_qty=0)
    assert res.ok is False
    assert "价格异常" in res.reason
    print("[OK] 非法价格被拒绝")


def test_calc_realized_pnl_profit():
    """实现盈亏：盈利"""
    pnl = calc_realized_pnl(avg_cost=100.0, sell_qty=100, sell_price=110.0, fee=10.0)
    # (110-100)*100 - 10 = 990
    assert abs(pnl - 990) < 1e-6
    print("[OK] 盈利 realized_pnl 计算正确")


def test_calc_realized_pnl_loss():
    """实现盈亏：亏损"""
    pnl = calc_realized_pnl(avg_cost=100.0, sell_qty=100, sell_price=90.0, fee=10.0)
    # (90-100)*100 - 10 = -1010
    assert abs(pnl - (-1010)) < 1e-6
    print("[OK] 亏损 realized_pnl 计算正确")


def test_calc_new_avg_cost():
    """加权平均成本"""
    # 原 100 股 @ 10 元 = 1000
    # 新买 200 股 @ 12 元 = 2400 + 5 费
    new_cost = calc_new_avg_cost(
        old_qty=100,
        old_avg_cost=10.0,
        buy_qty=200,
        buy_price=12.0,
        buy_fee=5.0,
    )
    # (1000 + 2400 + 5) / 300 = 3405 / 300 = 11.35
    assert abs(new_cost - 11.35) < 1e-6
    print("[OK] 加仓后平均成本计算正确")


def test_calc_new_avg_cost_empty():
    """空仓时返回 0"""
    assert calc_new_avg_cost(0, 0, 0, 0, 0) == 0.0
    print("[OK] 空仓平均成本为 0")


if __name__ == "__main__":
    test_round_lot()
    test_calc_fee_buy()
    test_calc_fee_sell()
    test_calc_fee_commission_min()
    test_match_order_buy_normal()
    test_match_order_buy_large()
    test_match_order_buy_insufficient_cash()
    test_match_order_buy_cant_afford_even_one_lot()
    test_match_order_sell_normal()
    test_match_order_sell_truncate_to_position()
    test_match_order_sell_no_position()
    test_match_order_less_than_one_lot()
    test_match_order_invalid_side()
    test_match_order_invalid_price()
    test_calc_realized_pnl_profit()
    test_calc_realized_pnl_loss()
    test_calc_new_avg_cost()
    test_calc_new_avg_cost_empty()
    print("\n[SUCCESS] matcher 全部 18 个测试通过！")
