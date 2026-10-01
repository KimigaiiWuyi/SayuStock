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
FALLBACK_CLOSED_DAYS = cal.FALLBACK_CLOSED_DAYS
CalendarCache = cal.CalendarCache


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


# ============================================================
# 2026 假期表回归（这批 bug 的直接防线）
# ============================================================
def test_2026_national_day_is_holiday():
    """2026 国庆 10/1~10/7 休市——旧表只到 2026-02-27，这里全被当成交易日。"""
    for day in ("2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"):
        dt = datetime.fromisoformat(day + "T10:00:00")
        assert is_a_share_trading_day(dt) is False, f"{day} 应为休市"
    print("[OK] 2026 国庆 5 个工作日全部判为休市")


def test_2026_mid_autumn_is_holiday():
    """2026 中秋 9/25(周五) 休市，9/28(周一) 恢复开市。"""
    assert is_a_share_trading_day(datetime(2026, 9, 25, 10, 0, 0)) is False
    assert is_a_share_trading_day(datetime(2026, 9, 28, 10, 0, 0)) is True
    print("[OK] 2026 中秋 9/25 休市、9/28 开市")


def test_2026_other_holidays_covered():
    """清明 / 劳动节 / 端午 也都在表里。"""
    for day in ("2026-04-06", "2026-05-01", "2026-05-04", "2026-05-05", "2026-06-19"):
        assert is_a_share_trading_day(datetime.fromisoformat(day + "T10:00:00")) is False, day
    print("[OK] 2026 清明/劳动节/端午 全部判为休市")


def test_2026_post_spring_festival_is_trading():
    """2026 春节 2/24~2/27 照常开市——旧表误标成休市，会白白少跑 4 天。"""
    for day in ("2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27"):
        assert day not in FALLBACK_CLOSED_DAYS, f"{day} 是交易日，不该在假期表里"
        assert is_a_share_trading_day(datetime.fromisoformat(day + "T10:00:00")) is True, day
    print("[OK] 2026 春节后 2/24~2/27 判为交易日")


def test_session_grace_window():
    """开盘 3 分钟内不拿分时判休市，避免首根分时未落地就误杀。"""
    at_open = datetime(2026, 3, 18, 9, 31, 0)
    assert is_trading_time(at_open) is True
    assert cal._session_grace_passed(at_open) is False
    assert cal._session_grace_passed(datetime(2026, 3, 18, 9, 34, 0)) is True
    assert cal._session_grace_passed(datetime(2026, 3, 18, 13, 2, 0)) is False
    assert cal._session_grace_passed(datetime(2026, 3, 18, 13, 4, 0)) is True
    print("[OK] 开盘 3 分钟宽限窗口生效")


def test_cache_coercion_rejects_garbage():
    """缓存字段缺失/类型不对时整体作废，不做部分补全。"""
    assert cal._coerce_cache({}) is None
    assert cal._coerce_cache("nope") is None
    assert cal._coerce_cache({"intraday_last_date": "2026-09-30"}) is None
    good = CalendarCache(
        intraday_last_date="2026-09-30",
        intraday_checked_at=1.0,
        daily_last_date="2026-09-30",
        daily_checked_at=1.0,
        daily_window_start="2024-10-01",
        daily_window_end="2026-09-30",
        closed_days=["2026-10-01"],
    )
    assert cal._coerce_cache(good) == good
    bad = dict(good)
    bad["closed_days"] = ["2026-10-01", 7]
    assert cal._coerce_cache(bad) is None
    print("[OK] 缓存校验：残缺/污染输入一律作废")


def test_fallback_covers_more_than_static_table():
    """兜底表不能只等于手工静态表——holidays 库应带来额外年份覆盖。"""
    static = cal._STATIC_FALLBACK_DAYS
    assert static <= FALLBACK_CLOSED_DAYS
    assert len(FALLBACK_CLOSED_DAYS) > len(static), "holidays 库没有贡献任何额外休市日"
    print(f"[OK] 兜底表 {len(FALLBACK_CLOSED_DAYS)} 天（静态 {len(static)} + holidays 扩展）")


def test_holidays_backend_covers_2026_official_days():
    """holidays 库必须覆盖 2026 全部官方休市工作日，否则兜底不成立。"""
    official = {
        "2026-01-01",
        "2026-01-02",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-02-19",
        "2026-02-20",
        "2026-02-23",
        "2026-04-06",
        "2026-05-01",
        "2026-05-04",
        "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
    }
    from_holidays = cal._closed_from_holidays([2026])
    missing = official - from_holidays
    assert not missing, f"holidays 库缺少: {sorted(missing)}"
    print(f"[OK] holidays 库覆盖 2026 全部 {len(official)} 个官方休市工作日")


def test_density_guard_thresholds_are_sane():
    """密度阈值要卡得住"数据残缺"、又不误伤正常年份。"""
    assert 0.0 < cal._MIN_BAR_DENSITY < 1.0
    # 实测 A 股工作日里约 92%~93% 是交易日，阈值必须显著低于它
    assert cal._MIN_BAR_DENSITY < 0.85
    assert 0.0 < cal._MAX_CLOSED_RATIO < 1.0
    # 正常年份休市占比 ~7%~8%，阈值必须显著高于它
    assert cal._MAX_CLOSED_RATIO > 0.15
    print(f"[OK] 密度阈值 {cal._MIN_BAR_DENSITY:.0%} / 休市上限 {cal._MAX_CLOSED_RATIO:.0%}")


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
    test_2026_national_day_is_holiday()
    test_2026_mid_autumn_is_holiday()
    test_2026_other_holidays_covered()
    test_2026_post_spring_festival_is_trading()
    test_session_grace_window()
    test_cache_coercion_rejects_garbage()
    test_fallback_covers_more_than_static_table()
    test_holidays_backend_covers_2026_official_days()
    test_density_guard_thresholds_are_sane()
    print("\n[SUCCESS] calendar 全部测试通过！")
