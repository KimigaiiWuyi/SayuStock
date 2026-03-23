#!/usr/bin/env python3
"""
update_stocks.py — 更新中国 A 股代码-名称及申万行业映射文件

修复说明：
通过构建 申万二级->一级 和 申万三级->二级的 映射树，解决成分股接口返回 "-" 的问题。
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime

import pandas as pd
import akshare as ak

# ──────────────────────────────────────────────
# 重试装饰器
# ──────────────────────────────────────────────


def retry(max_attempts=3, delay=3):
    """简单重试装饰器"""

    def decorator(func):
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt < max_attempts:
                        print(f"\n       ⚠️ 第 {attempt} 次失败: {e.__class__.__name__}: {e}")
                        print(f"       等待 {delay}s 后重试...")
                        time.sleep(delay)
            raise last_err

        return wrapper

    return decorator


# ──────────────────────────────────────────────
# 数据抓取（基础股票列表）
# ──────────────────────────────────────────────


@retry(max_attempts=3, delay=5)
def fetch_sse(symbol: str = "主板A股") -> pd.DataFrame:
    df = ak.stock_info_sh_name_code(symbol=symbol)
    code_col, name_col = "证券代码", "证券简称"
    board = "科创板" if symbol == "科创板" else "主板"
    rows = [
        {
            "code": str(r.get(code_col, "")).strip(),
            "name": str(r.get(name_col, "")).strip(),
            "exchange": "SSE",
            "board": board,
        }
        for _, r in df.iterrows()
        if str(r.get(code_col, "")).strip() and str(r.get(code_col, "")).strip() != "nan"
    ]
    return pd.DataFrame(rows)


@retry(max_attempts=3, delay=5)
def fetch_szse() -> pd.DataFrame:
    df = ak.stock_info_sz_name_code(symbol="A股列表")
    rows = []
    for _, row in df.iterrows():
        code_raw = str(row.get("A股代码", "")).strip()
        code = code_raw.split(".")[0].zfill(6)
        name = str(row.get("A股简称", "")).strip()
        if not code or code == "000nan" or not name:
            continue

        board = "创业板" if code.startswith(("300", "301")) else ("中小板" if code.startswith("002") else "主板")
        rows.append({"code": code, "name": name, "exchange": "SZSE", "board": board})
    return pd.DataFrame(rows)


@retry(max_attempts=3, delay=5)
def fetch_bse() -> pd.DataFrame:
    df = ak.stock_info_bj_name_code()
    rows = [
        {
            "code": str(r.get("证券代码", "")).strip(),
            "name": str(r.get("证券简称", "")).strip(),
            "exchange": "BSE",
            "board": "北交所",
        }
        for _, r in df.iterrows()
        if str(r.get("证券代码", "")).strip() and str(r.get("证券代码", "")).strip() != "nan"
    ]
    return pd.DataFrame(rows)


def fetch_all_base_stocks() -> tuple:
    """抓取全部 A 股基础信息"""
    parts, errors = [], []
    fetchers = [
        ("[1/4] 上交所主板", lambda: fetch_sse("主板A股")),
        ("[2/4] 上交所科创板", lambda: fetch_sse("科创板")),
        ("[3/4] 深交所", fetch_szse),
        ("[4/4] 北交所", fetch_bse),
    ]

    for label, fn in fetchers:
        print(f"{label}...")
        try:
            df = fn()
            parts.append(df)
            print(f"       成功获取 {len(df)} 只")
        except Exception as e:
            errors.append(f"{label}: {e}")
            print(f"       ❌ 失败: {e}")

    if not parts:
        print("\n❌ 所有交易所基础信息均抓取失败，无法继续")
        sys.exit(1)

    all_df = pd.concat(parts, ignore_index=True).drop_duplicates(subset="code", keep="first")
    return dict(zip(all_df["code"], all_df["name"])), all_df


# ──────────────────────────────────────────────
# 数据抓取（构建申万行业分类树）
# ──────────────────────────────────────────────


def fetch_sw_industries() -> dict:
    print("\n[5/5] 开始获取申万行业分类(构建层级树)...")
    try:
        # 1. 获取二级行业信息，构建: [二级行业名称 -> 一级行业名称]
        l2_df = ak.sw_index_second_info()
        l2_to_l1 = {str(row["行业名称"]).strip(): str(row["上级行业"]).strip() for _, row in l2_df.iterrows()}

        # 2. 获取三级行业信息，构建: [三级代码 -> {"l1": 一级名称, "l2": 二级名称}]
        l3_df = ak.sw_index_third_info()
        l3_info_map = {}
        for _, row in l3_df.iterrows():
            l3_code = str(row["行业代码"]).strip()
            l2_name = str(row["上级行业"]).strip()
            # 查表得出它的一级行业
            l1_name = l2_to_l1.get(l2_name, "未知")
            l3_info_map[l3_code] = {"l1": l1_name, "l2": l2_name}

        l3_codes = list(l3_info_map.keys())
    except Exception as e:
        print(f"❌ 获取申万层级目录失败: {e}")
        return {}

    industry_map = {}
    total = len(l3_codes)
    print(f"       成功构建行业树。共 {total} 个申万三级行业，开始拉取成分股...")

    @retry(max_attempts=3, delay=2)
    def _get_cons(symbol):
        return ak.sw_index_third_cons(symbol=symbol)

    for i, code in enumerate(l3_codes):
        print(f"\r       正在处理行业 [{i + 1:03d}/{total}] - {code} ", end="", flush=True)
        try:
            cons_df = _get_cons(code)
            if cons_df is None or cons_df.empty:
                continue

            # 核心修复点：不使用接口返回的可能为 "-" 的列，而是使用我们自己构建的层级树映射
            mapped_l1 = l3_info_map[code]["l1"]
            mapped_l2 = l3_info_map[code]["l2"]

            for _, row in cons_df.iterrows():
                raw_code = str(row.get("股票代码", ""))
                if not raw_code:
                    continue
                # 兼容 600519.SH 这种带后缀的格式
                stock_code = raw_code.split(".")[0] if "." in raw_code else raw_code

                industry_map[stock_code] = {"industry_l1": mapped_l1, "industry_l2": mapped_l2}
            # 适度休眠防屏蔽
            time.sleep(0.2)
        except Exception:
            pass  # 发生极端异常跳过该行业

    print("\n       ✅ 申万行业数据拉取完成。")
    return industry_map


# ──────────────────────────────────────────────
# 文件读写与对比
# ──────────────────────────────────────────────


def save_json(mapping: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存 JSON → {path}  ({len(mapping)} 只)")


def save_csv(df: pd.DataFrame, path: str):
    df_sorted = df.sort_values("code").reset_index(drop=True)
    df_sorted.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"✅ 已保存 CSV  → {path}  ({len(df_sorted)} 只)")


def show_diff(new_mapping: dict, old_path: str):
    if not os.path.exists(old_path):
        print("⚠️  未找到旧版本文件，跳过对比")
        return

    with open(old_path, "r", encoding="utf-8") as f:
        old_mapping = json.load(f)

    old_codes = set(old_mapping.keys())
    new_codes = set(new_mapping.keys())

    added = sorted(new_codes - old_codes)
    removed = sorted(old_codes - new_codes)
    common = old_codes & new_codes

    changed = []
    for c in common:
        old_val = old_mapping[c]
        new_val = new_mapping[c]

        if isinstance(old_val, str):
            if old_val != new_val["name"]:
                changed.append(f"{c}: {old_val} -> {new_val['name']}")
        else:
            changes = []
            if old_val.get("name") != new_val.get("name"):
                changes.append(f"名称({old_val.get('name')}->{new_val.get('name')})")
            if old_val.get("industry_l1") != new_val.get("industry_l1"):
                changes.append(f"一级({old_val.get('industry_l1')}->{new_val.get('industry_l1')})")
            if old_val.get("industry_l2") != new_val.get("industry_l2"):
                changes.append(f"二级({old_val.get('industry_l2')}->{new_val.get('industry_l2')})")
            if changes:
                changed.append(f"{c} " + ", ".join(changes))

    print("\n📊 版本对比:")
    print(f"   旧版: {len(old_mapping)} 只")
    print(f"   新版: {len(new_mapping)} 只")
    print(f"   新增: {len(added)} 只")
    print(f"   删除: {len(removed)} 只")

    print(f"   改名/跨行: {len(changed)} 只", end="")
    if changed:
        sample = changed[:10]
        print("\n      " + "\n      ".join(sample) + ("\n      ..." if len(changed) > 10 else ""))
    else:
        print()


def main():
    parser = argparse.ArgumentParser(description="更新中国 A 股及行业映射文件")
    parser.add_argument("-o", "--output", default="chinese_stocks.json", help="输出文件名 (默认: chinese_stocks.json)")
    parser.add_argument("--format", choices=["json", "csv", "both"], default="json", help="输出格式")
    parser.add_argument("--diff", action="store_true", help="与已有文件对比差异")
    args = parser.parse_args()

    # 强制让输出目录与当前 Python 脚本在同一个目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, args.output)

    start = time.time()
    print(f"🚀 开始抓取 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. 抓取基础股票数据 (约 5400+ 只)
    base_mapping, detail_df = fetch_all_base_stocks()

    # 2. 抓取申万行业映射树
    industry_map = fetch_sw_industries()

    # 3. 数据融合
    final_mapping = {}
    for code, name in base_mapping.items():
        ind = industry_map.get(code, {})
        # 如果能在行业表找到，就用映射的行业；找不到（如北交所新股），填"未知"
        final_mapping[code] = {
            "name": name,
            "industry_l1": ind.get("industry_l1", "未知"),
            "industry_l2": ind.get("industry_l2", "未知"),
        }

    # 如果需要导出 CSV，更新 Dataframe
    detail_df["industry_l1"] = detail_df["code"].map(lambda x: final_mapping[x]["industry_l1"])
    detail_df["industry_l2"] = detail_df["code"].map(lambda x: final_mapping[x]["industry_l2"])

    # 4. 差异对比
    if args.diff:
        show_diff(final_mapping, output_path)

    # 5. 保存文件
    print()
    if args.format in ("json", "both"):
        save_json(final_mapping, output_path)
    if args.format in ("csv", "both"):
        csv_path = os.path.splitext(output_path)[0] + ".csv"
        save_csv(detail_df, csv_path)

    print(f"\n⏱  耗时: {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
