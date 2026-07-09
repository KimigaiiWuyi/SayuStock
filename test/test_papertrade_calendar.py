"""AI 模拟盘交易日历单测。"""

import sys
import importlib.util
from types import ModuleType
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_cal_test"


def _ensure_pkg():
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        PKG_ROOT / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT)],
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
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
    spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade.{name}",
        PKG_ROOT / "stock_papertrade" / file_name,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cal = _load("trading_calendar", "trading_calendar.py")
is_a_share_trading_day = cal.is_a_share_trading_day
is_trading_time = cal.is_trading_time
should_run_papertrade = cal.should_run_papertrade
trading_day_summary = cal.trading_day_summary
next_decision_time = cal.next_decision_time


# ============================================================
# Tests
# ============================================================
def test_weekday_is_trading_day():
    """普通工作日 → 是交易日"""
    # 2025-03-19 是周三
    dt = datetime(2025, 3, 19, 10, 0, 0)
    assert is_a_share_trading_day(dt) is True
    print("[OK] 普通周三 → 交易日")


def test_weekend_is_not_trading_day():
    """周末 → 不是交易日"""
    # 2025-03-22 是周六
    dt = datetime(2025, 3, 22, 10, 0, 0)
    assert is_a_share_trading_day(dt) is False
    # 2025-03-23 是周日
    dt = datetime(2025, 3, 23, 10, 0, 0)
    assert is_a_share_trading_day(dt) is False
    print("[OK] 周末 → 非交易日")


def test_holiday_is_not_trading_day():
    """节假日 → 不是交易日"""
    # 2025-10-01 是国庆节
    dt = datetime(2025, 10, 1, 10, 0, 0)
    assert is_a_share_trading_day(dt) is False
    # 2025-05-01 是劳动节
    dt = datetime(2025, 5, 1, 10, 0, 0)
    assert is_a_share_trading_day(dt) is False
    # 2025-02-17 是春节（除夕后）
    # 实际春节是 1-28 到 2-4 (2025)
    dt = datetime(2025, 1, 28, 10, 0, 0)
    assert is_a_share_trading_day(dt) is False
    print("[OK] 国庆/劳动/春节 → 非交易日")


def test_trading_time_morning():
    """上午 9:30-11:30 → 交易时段"""
    assert is_trading_time(datetime(2025, 3, 19, 9, 30, 0)) is True
    assert is_trading_time(datetime(2025, 3, 19, 10, 0, 0)) is True
    assert is_trading_time(datetime(2025, 3, 19, 11, 30, 0)) is True
    print("[OK] 上午 9:30-11:30 → 交易时段")


def test_trading_time_lunch():
    """午休 11:30-13:00 → 不在交易时段"""
    assert is_trading_time(datetime(2025, 3, 19, 11, 31, 0)) is False
    assert is_trading_time(datetime(2025, 3, 19, 12, 0, 0)) is False
    assert is_trading_time(datetime(2025, 3, 19, 12, 59, 0)) is False
    print("[OK] 午休 11:30-13:00 → 非交易时段")


def test_trading_time_afternoon():
    """下午 13:00-15:00 → 交易时段"""
    assert is_trading_time(datetime(2025, 3, 19, 13, 0, 0)) is True
    assert is_trading_time(datetime(2025, 3, 19, 14, 30, 0)) is True
    assert is_trading_time(datetime(2025, 3, 19, 15, 0, 0)) is True
    print("[OK] 下午 13:00-15:00 → 交易时段")


def test_trading_time_after_close():
    """收盘后 15:00 之后 → 不在交易时段"""
    assert is_trading_time(datetime(2025, 3, 19, 15, 1, 0)) is False
    assert is_trading_time(datetime(2025, 3, 19, 18, 0, 0)) is False
    print("[OK] 收盘后 15:00+ → 非交易时段")


def test_trading_time_before_open():
    """开盘前 9:30 之前 → 不在交易时段"""
    assert is_trading_time(datetime(2025, 3, 19, 9, 0, 0)) is False
    assert is_trading_time(datetime(2025, 3, 19, 9, 29, 0)) is False
    print("[OK] 开盘前 9:30 前 → 非交易时段")


def test_should_run_combined():
    """should_run_papertrade 兼顾交易日 + 交易时段"""
    # 交易日 + 交易时段
    assert should_run_papertrade(datetime(2025, 3, 19, 10, 0, 0)) is True
    # 周末
    assert should_run_papertrade(datetime(2025, 3, 22, 10, 0, 0)) is False
    # 交易日 + 午休
    assert should_run_papertrade(datetime(2025, 3, 19, 12, 0, 0)) is False
    print("[OK] should_run 组合判断正确")


def test_summary_format():
    """trading_day_summary 返回 3-tuple"""
    td, tt, desc = trading_day_summary(datetime(2025, 3, 19, 10, 0, 0))
    assert td is True
    assert tt is True
    assert "交易时段" in desc
    print(f"[OK] 摘要: {desc}")


def test_next_decision_time_during_session():
    """交易时段内 → 返回当前时间（立即）"""
    now = datetime(2025, 3, 19, 10, 0, 0)
    nxt = next_decision_time(now)
    assert nxt == now
    print("[OK] 交易时段内 next = now")


def test_next_decision_time_lunch_break():
    """午休 → 13:00"""
    now = datetime(2025, 3, 19, 12, 0, 0)
    nxt = next_decision_time(now)
    assert nxt.hour == 13
    assert nxt.minute == 0
    assert nxt.date() == now.date()
    print("[OK] 午休 → 次决策 13:00")


def test_next_decision_time_after_close():
    """收盘后 → 次日 9:30"""
    now = datetime(2025, 3, 19, 16, 0, 0)  # 周三收盘后
    nxt = next_decision_time(now)
    assert nxt.hour == 9
    assert nxt.minute == 30
    # 次日 = 2025-03-20（周四）
    assert nxt.date().isoformat() == "2025-03-20"
    print("[OK] 收盘后 → 次日 9:30")


def test_next_decision_time_holiday_to_next_trading_day():
    """节假日 → 下一个交易日 9:30"""
    # 2025-10-01 是国庆，但 10-01 是周三
    now = datetime(2025, 10, 1, 10, 0, 0)
    nxt = next_decision_time(now)
    # 下一个交易日是 10-09
    assert nxt.day >= 9  # 10-09 或更晚
    print(f"[OK] 节假日 → 下一个交易日 {nxt.date().isoformat()}")


if __name__ == "__main__":
    test_weekday_is_trading_day()
    test_weekend_is_not_trading_day()
    test_holiday_is_not_trading_day()
    test_trading_time_morning()
    test_trading_time_lunch()
    test_trading_time_afternoon()
    test_trading_time_after_close()
    test_trading_time_before_open()
    test_should_run_combined()
    test_summary_format()
    test_next_decision_time_during_session()
    test_next_decision_time_lunch_break()
    test_next_decision_time_after_close()
    test_next_decision_time_holiday_to_next_trading_day()
    print("\n[SUCCESS] calendar 全部 13 个测试通过！")
