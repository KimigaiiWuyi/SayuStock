"""验证预披露分红事件被正确排除的单元测试。"""

from typing import Any, Dict, List

import pandas as pd


def test_exclude_planned_dividends():
    """模拟招商银行分红数据，验证 2025-12-31 与 2026-06-30 两条记录被正确区分。"""

    # 模拟原始接口返回（与问题描述一致的 4 条记录）
    dividend_resp: List[Dict[str, Any]] = [
        {
            "REPORT_DATE": "2026-06-30 00:00:00",
            "EX_DIVIDEND_DATE": None,
            "PLAN_NOTICE_DATE": "2026-03-28 00:00:00",
            "NOTICE_DATE": "2026-03-28 00:00:00",
            "PRETAX_BONUS_RMB": None,
            "ASSIGN_PROGRESS": "预披露",
        },
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "EX_DIVIDEND_DATE": None,
            "PLAN_NOTICE_DATE": "2026-03-28 00:00:00",
            "NOTICE_DATE": "2026-03-28 00:00:00",
            "PRETAX_BONUS_RMB": 10.03,
            "ASSIGN_PROGRESS": "董事会决议通过",
        },
        {
            "REPORT_DATE": "2025-06-30 00:00:00",
            "EX_DIVIDEND_DATE": "2026-01-16 00:00:00",
            "PLAN_NOTICE_DATE": "2025-12-30 00:00:00",
            "NOTICE_DATE": "2026-01-10 00:00:00",
            "PRETAX_BONUS_RMB": 10.13,
            "ASSIGN_PROGRESS": "实施分配",
        },
        {
            "REPORT_DATE": "2024-12-31 00:00:00",
            "EX_DIVIDEND_DATE": "2025-07-11 00:00:00",
            "PLAN_NOTICE_DATE": "2025-03-26 00:00:00",
            "NOTICE_DATE": "2025-07-04 00:00:00",
            "PRETAX_BONUS_RMB": 20.0,
            "ASSIGN_PROGRESS": "实施分配",
        },
    ]

    # === 1. 复现 get_dy_series 中 raw_events 的构建逻辑 ===
    raw_events: List[Dict[str, Any]] = []
    for row in dividend_resp:
        bonus = row.get("PRETAX_BONUS_RMB")
        report_date_str = row.get("REPORT_DATE")
        if bonus is None:
            continue
        ex_date_str = row.get("EX_DIVIDEND_DATE")
        fallback_date_str = row.get("PLAN_NOTICE_DATE") or row.get("NOTICE_DATE")
        date_str = ex_date_str or fallback_date_str
        if not date_str:
            continue
        ex_date_candidate = pd.Timestamp(str(date_str)[:10])
        bonus_per_share = float(bonus) / 10.0
        if bonus_per_share <= 0:
            continue
        report_date: pd.Timestamp | None = None
        if report_date_str:
            try:
                parsed = pd.Timestamp(str(report_date_str)[:10])
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, pd.Timestamp):
                report_date = parsed
        raw_events.append(
            {
                "ex_date": ex_date_candidate,
                "bonus_per_share": bonus_per_share,
                "report_date": report_date,
                "is_planned": not bool(ex_date_str),
            }
        )

    assert len(raw_events) == 3, f"预期3条有效记录(排除PRETAX_BONUS_RMB为None的), 实际{len(raw_events)}"
    print(f"[OK] 原始有效事件数: {len(raw_events)}")

    # === 2. 复现 period_groups 归并逻辑 ===
    period_groups: Dict[str, Dict[str, Any]] = {}
    for ev in raw_events:
        ex_date_val: pd.Timestamp = ev["ex_date"]
        if ev["report_date"] is not None:
            report_key_ts = ev["report_date"]
        else:
            report_key_ts = pd.Timestamp(f"{ex_date_val.year}-01-01")
        key_str: str = report_key_ts.strftime("%Y-%m-%d")
        bucket = period_groups.setdefault(
            key_str,
            {"report_date": report_key_ts, "ex_events": []},
        )
        bucket["ex_events"].append((ex_date_val, ev["bonus_per_share"], ev["is_planned"]))

    # === 3. 复现 period_records 构建逻辑（含 is_planned 标记） ===
    period_records: List[Dict[str, Any]] = []
    for key_str, bucket in period_groups.items():
        ex_events = sorted(bucket["ex_events"], key=lambda x: x[0])
        if not ex_events:
            continue
        total_bonus = float(sum(b for _, b, _ in ex_events))
        if total_bonus <= 0:
            continue
        is_all_planned = all(planned for _, _, planned in ex_events)
        period_records.append(
            {
                "report_date": bucket["report_date"],
                "ex_events": ex_events,
                "bonus_per_share": total_bonus,
                "effective_date": ex_events[-1][0],
                "is_planned": is_all_planned,
            }
        )
    period_records.sort(key=lambda r: r["effective_date"])

    print(f"[OK] 归并后报告期数: {len(period_records)}")
    for r in period_records:
        print(
            f"   报告期={r['report_date'].strftime('%Y-%m-%d')}, "
            f"生效日={r['effective_date'].strftime('%Y-%m-%d')}, "
            f"金额={r['bonus_per_share']:.2f}, is_planned={r['is_planned']}"
        )

    # === 4. 验证关键断言 ===
    # 4a) 2025-12-31 董事会决议（无除权日）应被标记为 is_planned=True
    planned_records = [r for r in period_records if r["is_planned"]]
    assert len(planned_records) == 1, f"预期1条预披露记录, 实际{len(planned_records)}"
    assert planned_records[0]["report_date"].strftime("%Y-%m-%d") == "2025-12-31"
    print("[OK] 2025-12-31 无除权日记录被正确标记为 is_planned=True")

    # 4b) 2026-06-30 预披露（PRETAX_BONUS_RMB 为空）根本没进入 raw_events
    report_dates = [r["report_date"].strftime("%Y-%m-%d") for r in period_records]
    assert "2026-06-30" not in report_dates
    print("[OK] 2026-06-30 预披露(PRETAX_BONUS_RMB为None)被排除在计算之外")

    # 4c) 模拟 2026-01-20 的 applicable — 计算层不过滤 is_planned，
    #     2025-12-31 董事会决议(effective_date=2026-03-28) > 2026-01-20，尚未生效
    trade_date = pd.Timestamp("2026-01-20")
    current_year = trade_date.year
    target_report_year = current_year - 1
    applicable = [
        r for r in period_records if r["effective_date"] <= trade_date and r["report_date"].year == target_report_year
    ]
    assert len(applicable) == 1, f"2026-01-20 应命中1条实施分配记录, 实际{len(applicable)}"
    assert applicable[0]["report_date"].strftime("%Y-%m-%d") == "2025-06-30"
    print("[OK] 2026-01-20 的股息率分子正确锁定 2025-06-30 实施分配记录")

    # 4d) 模拟 2026-03-29 — 2025-12-31 董事会决议(effective_date=2026-03-28) <= 2026-03-29 已生效
    trade_date2 = pd.Timestamp("2026-03-29")
    applicable2 = [
        r for r in period_records if r["effective_date"] <= trade_date2 and r["report_date"].year == target_report_year
    ]
    assert len(applicable2) == 2, f"2026-03-29 应命中2条记录, 实际{len(applicable2)}"
    report_dates2 = {r["report_date"].strftime("%Y-%m-%d") for r in applicable2}
    assert report_dates2 == {"2025-06-30", "2025-12-31"}
    print("[OK] 2026-03-29 时 2025-12-31 董事会决议已参与计算(effective_date生效)")

    # 4e) 模拟 2026-03-28 当天 — effective_date 是当天，应已生效（<=）
    trade_date3 = pd.Timestamp("2026-03-28")
    applicable3 = [
        r for r in period_records if r["effective_date"] <= trade_date3 and r["report_date"].year == target_report_year
    ]
    assert len(applicable3) == 2, f"2026-03-28 应命中2条记录, 实际{len(applicable3)}"
    print("[OK] 2026-03-28 当天 2025-12-31 董事会决议已生效")

    # 4f) 验证 is_planned 标记仍正确（供标注层过滤用）
    planned_applicable = [r for r in applicable2 if r["is_planned"]]
    assert len(planned_applicable) == 1
    assert planned_applicable[0]["report_date"].strftime("%Y-%m-%d") == "2025-12-31"
    print("[OK] is_planned 标记正确，标注层可据此跳过未除权事件")

    print("\n[SUCCESS] 全部测试通过！方案公布即参与计算，未除权事件仅不标注。")


if __name__ == "__main__":
    test_exclude_planned_dividends()
