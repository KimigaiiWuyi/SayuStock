#!/usr/bin/env python3
"""update_stocks.py — 用东财 API 更新中国 A 股代码-名称及行业映射。

数据源（全部走内置 ``EASTMONEY_REQUESTER``，不依赖 akshare）：

1. ``clist``：``沪深京A`` 全市场分页，拿代码 / 名称 / f100 所属行业
2. ``sidemenu_new.json`` 行业板块（``type=2``）按 ``flag`` 分三级：
   ``1`` 一级、``2`` 二级、``3`` 三级；再分别拉成分 ``clist``

输出字段与 ``constant.StockInfo`` 对齐::

    {code: {"name": str, "industry_l1": str, "industry_l2": str, "industry_l3": str}}
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import argparse
from typing import Any
from pathlib import Path
from datetime import datetime

import pandas as pd

# 直跑脚本时补路径并以包身份重入，保证下面只能走相对导入。
# 本文件：.../plugins/SayuStock/SayuStock/utils/update_stocks.py
if not __package__:
    _script_dir = Path(__file__).resolve().parent
    _plugin_root = _script_dir.parents[1]  # .../plugins/SayuStock（内层 SayuStock 包的父目录）
    _core_root = next(
        (p for p in _script_dir.parents if (p / "gsuid_core" / "__init__.py").is_file()),
        None,
    )
    for _p in (_plugin_root, _core_root):
        if _p is not None:
            _s = str(_p)
            if _s not in sys.path:
                sys.path.insert(0, _s)
    import runpy

    runpy.run_module("SayuStock.utils.update_stocks", run_name="__main__")
    raise SystemExit(0)

from gsuid_core.logger import logger  # noqa: E402

from .constant import market_dict
from .eastmoney import EASTMONEY_REQUESTER

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# 代码 / 名称 / 市场标记 / 所属行业
CLIST_FIELDS = "f12,f14,f13,f100"
PAGE_SIZE = 100
# 行业板块成分拉取时的并发与节流
INDUSTRY_CONCURRENCY = 6
INDUSTRY_PAUSE_S = 0.15


# ──────────────────────────────────────────────
# 东财 clist 分页
# ──────────────────────────────────────────────


def _as_diff_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    diff = data["diff"] if "diff" in data else []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not isinstance(diff, list):
        return []
    return [x for x in diff if isinstance(x, dict)]


def _total_of(data: dict[str, Any]) -> int:
    raw = data["total"] if "total" in data else 0
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


async def fetch_clist_pages(
    fs: str,
    *,
    fields: str = CLIST_FIELDS,
    pz: int = PAGE_SIZE,
    max_pages: int = 200,
    fid: str = "f12",
    label: str = "",
    quiet: bool = False,
) -> list[dict[str, Any]]:
    """按 fs 表达式分页拉取 clist 全量行。"""
    all_rows: list[dict[str, Any]] = []
    for pn in range(1, max_pages + 1):
        params: list[tuple[str, str]] = [
            ("pz", str(pz)),
            ("po", "1"),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", fid),
            ("pn", str(pn)),
            ("fs", fs),
            ("fields", fields),
        ]
        resp = await EASTMONEY_REQUESTER.stock_request(CLIST_URL, "GET", params=params)
        if isinstance(resp, int) or not isinstance(resp, dict):
            logger.warning(f"[update_stocks] clist 失败 {label} pn={pn} resp={resp}")
            break
        data = resp["data"] if "data" in resp and isinstance(resp["data"], dict) else {}
        page = _as_diff_list(data)
        if not page:
            break
        all_rows.extend(page)
        total = _total_of(data)
        if not quiet:
            tag = f" {label}" if label else ""
            print(
                f"\r       clist{tag}: 已拉 {len(all_rows)}" + (f"/{total}" if total else ""),
                end="",
                flush=True,
            )
        if (total > 0 and len(all_rows) >= total) or len(page) < pz:
            break
        await asyncio.sleep(0.05)
    if not quiet and (label or all_rows):
        print()
    return all_rows


def _board_of_code(code: str) -> str:
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("8", "4")) and len(code) == 6:
        return "北交所"
    if code.startswith("002"):
        return "中小板"
    return "主板"


def _exchange_of_code(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "SSE"
    if code.startswith(("8", "4")) and len(code) == 6:
        return "BSE"
    return "SZSE"


def _norm_code(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or text in {"nan", "None", "-"}:
        return ""
    # 兼容 600519.SH / 1.600519
    if "." in text:
        left, right = text.split(".", 1)
        if left.isdigit() and len(left) <= 2 and right.isdigit():
            text = right  # 1.600519
        elif left.isdigit():
            text = left  # 600519.SH
        elif right.isdigit():
            text = right
        else:
            text = left
    text = text.strip()
    if text.isdigit():
        return text.zfill(6)
    return text


def _norm_name(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or text in {"nan", "None", "-"}:
        return ""
    return text


def _norm_industry(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or text in {"nan", "None", "-", "null"}:
        return ""
    return text


# ──────────────────────────────────────────────
# 基础股票列表（沪深京 A）
# ──────────────────────────────────────────────


async def fetch_all_base_stocks() -> tuple[dict[str, str], pd.DataFrame, dict[str, str]]:
    """抓取全部 A 股：返回 (code→name, 明细 DataFrame, code→f100 行业)。"""
    fs = market_dict["沪深京A"] if "沪深京A" in market_dict else ("m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048")
    print("[1/2] 东财 clist 拉取沪深京 A...")
    rows = await fetch_clist_pages(fs, label="沪深京A", fid="f12")
    if not rows:
        print("❌ clist 未返回任何股票，无法继续")
        sys.exit(1)

    name_map: dict[str, str] = {}
    f100_map: dict[str, str] = {}
    detail_rows: list[dict[str, str]] = []
    for item in rows:
        code = _norm_code(item["f12"] if "f12" in item else "")
        name = _norm_name(item["f14"] if "f14" in item else "")
        if not code or not name:
            continue
        ind = _norm_industry(item["f100"] if "f100" in item else "")
        name_map[code] = name
        if ind:
            f100_map[code] = ind
        detail_rows.append(
            {
                "code": code,
                "name": name,
                "exchange": _exchange_of_code(code),
                "board": _board_of_code(code),
            }
        )

    detail_df = pd.DataFrame(detail_rows).drop_duplicates(subset="code", keep="first")
    print(f"       成功获取 {len(name_map)} 只")
    return name_map, detail_df, f100_map


# ──────────────────────────────────────────────
# 行业板块 → 成分股（按 sidemenu flag 分三级）
# ──────────────────────────────────────────────

SIDEMENU_URL = "https://quote.eastmoney.com/center/api/sidemenu_new.json"


async def fetch_industry_boards_by_level() -> dict[int, list[tuple[str, str]]]:
    """sidemenu type=2：flag 1/2/3 → [(板块名, BKxxxx), ...]。"""
    empty: dict[int, list[tuple[str, str]]] = {1: [], 2: [], 3: []}
    resp = await EASTMONEY_REQUESTER.stock_request(SIDEMENU_URL)
    if isinstance(resp, int) or not isinstance(resp, dict):
        print(f"❌ sidemenu 失败: {resp}")
        return empty
    if "bklist" not in resp or not isinstance(resp["bklist"], list):
        print("❌ sidemenu 无 bklist")
        return empty
    by_flag: dict[int, list[tuple[str, str]]] = {1: [], 2: [], 3: []}
    for item in resp["bklist"]:
        if not isinstance(item, dict):
            continue
        if "type" not in item or item["type"] != 2:
            continue
        if "name" not in item or "code" not in item:
            continue
        flag_raw = item["flag"] if "flag" in item else 1
        flag = int(flag_raw) if isinstance(flag_raw, (int, float)) else 1
        if flag not in by_flag:
            continue
        by_flag[flag].append((str(item["name"]), str(item["code"])))
    return by_flag


async def _fill_board_members(boards: list[tuple[str, str]], label: str) -> dict[str, str]:
    """拉一批板块的成分代码 → 板块名；同股只保留第一次。"""
    if not boards:
        return {}
    total = len(boards)
    print(f"       {label} {total} 个板块，开始拉成分...")
    industry_map: dict[str, str] = {}
    sem = asyncio.Semaphore(INDUSTRY_CONCURRENCY)
    done = 0
    lock = asyncio.Lock()

    async def _one(name: str, code: str) -> None:
        nonlocal done
        fs = code if str(code).startswith("b:") else f"b:{code}"
        async with sem:
            rows = await fetch_clist_pages(
                fs,
                fields="f12,f14",
                max_pages=50,
                fid="f3",
                quiet=True,
            )
            for item in rows:
                stock_code = _norm_code(item["f12"] if "f12" in item else "")
                if not stock_code:
                    continue
                if stock_code not in industry_map:
                    industry_map[stock_code] = name
            await asyncio.sleep(INDUSTRY_PAUSE_S)
            async with lock:
                done += 1
                print(f"\r       {label}进度 [{done:03d}/{total}] {name[:12]:<12}", end="", flush=True)

    await asyncio.gather(*[_one(n, c) for n, c in boards])
    print(f"\n       ✅ {label}完成，覆盖 {len(industry_map)} 只")
    return industry_map


async def fetch_industry_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """分别返回一级 / 二级 / 三级 代码→板块名。"""
    print("\n[2/2] 东财行业板块菜单（flag=1/2/3）+ 成分...")
    by_flag = await fetch_industry_boards_by_level()
    print(f"       一级 {len(by_flag[1])} / 二级 {len(by_flag[2])} / 三级 {len(by_flag[3])}")
    l1_map = await _fill_board_members(by_flag[1], "一级")
    l2_map = await _fill_board_members(by_flag[2], "二级")
    l3_map = await _fill_board_members(by_flag[3], "三级")
    return l1_map, l2_map, l3_map


# ──────────────────────────────────────────────
# 文件读写与对比
# ──────────────────────────────────────────────


def save_json(mapping: dict[str, dict[str, str]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存 JSON → {path}  ({len(mapping)} 只)")


def save_csv(df: pd.DataFrame, path: str) -> None:
    df_sorted = df.sort_values("code").reset_index(drop=True)
    df_sorted.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"✅ 已保存 CSV  → {path}  ({len(df_sorted)} 只)")


def show_diff(new_mapping: dict[str, dict[str, str]], old_path: str) -> None:
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

    changed: list[str] = []
    for c in common:
        old_val = old_mapping[c]
        new_val = new_mapping[c]

        if isinstance(old_val, str):
            if old_val != new_val["name"]:
                changed.append(f"{c}: {old_val} -> {new_val['name']}")
            continue

        changes: list[str] = []
        if old_val.get("name") != new_val.get("name"):
            changes.append(f"名称({old_val.get('name')}->{new_val.get('name')})")
        if old_val.get("industry_l1") != new_val.get("industry_l1"):
            changes.append(f"一级({old_val.get('industry_l1')}->{new_val.get('industry_l1')})")
        if old_val.get("industry_l2") != new_val.get("industry_l2"):
            changes.append(f"二级({old_val.get('industry_l2')}->{new_val.get('industry_l2')})")
        old_l3 = old_val["industry_l3"] if "industry_l3" in old_val else ""
        new_l3 = new_val["industry_l3"] if "industry_l3" in new_val else ""
        if old_l3 != new_l3:
            changes.append(f"三级({old_l3}->{new_l3})")
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


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="用东财 API 更新中国 A 股及行业映射文件")
    parser.add_argument(
        "-o",
        "--output",
        default="chinese_stocks.json",
        help="输出文件名 (默认: chinese_stocks.json)",
    )
    parser.add_argument("--format", choices=["json", "csv", "both"], default="json", help="输出格式")
    parser.add_argument("--diff", action="store_true", help="与已有文件对比差异")
    parser.add_argument(
        "--skip-industry-boards",
        action="store_true",
        help="跳过行业板块成分遍历，仅用 clist f100 填行业",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, args.output)

    start = time.time()
    print(f"🚀 开始抓取（东财 API）— {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. 基础列表
    base_mapping, detail_df, f100_map = await fetch_all_base_stocks()

    # 2. 行业板块映射（可选）
    l1_map: dict[str, str] = {}
    l2_map: dict[str, str] = {}
    l3_map: dict[str, str] = {}
    if not args.skip_industry_boards:
        l1_map, l2_map, l3_map = await fetch_industry_maps()

    # 3. 融合：flag 分级优先，缺省退回 clist f100 / 「未知」
    final_mapping: dict[str, dict[str, str]] = {}
    for code, name in base_mapping.items():
        if code in l1_map:
            l1 = l1_map[code]
        elif code in f100_map:
            l1 = f100_map[code]
        else:
            l1 = "未知"
        if code in l2_map:
            l2 = l2_map[code]
        elif code in f100_map:
            l2 = f100_map[code]
        else:
            l2 = l1
        if code in l3_map:
            l3 = l3_map[code]
        else:
            l3 = l2
        final_mapping[code] = {
            "name": name,
            "industry_l1": l1,
            "industry_l2": l2,
            "industry_l3": l3,
        }

    def _industry(code: object, field: str) -> str:
        if not isinstance(code, str) or not code:
            return "未知"
        info = final_mapping.get(code)
        if info is None:
            return "未知"
        return str(info[field])

    detail_df["industry_l1"] = detail_df["code"].map(lambda x: _industry(x, "industry_l1"))
    detail_df["industry_l2"] = detail_df["code"].map(lambda x: _industry(x, "industry_l2"))
    detail_df["industry_l3"] = detail_df["code"].map(lambda x: _industry(x, "industry_l3"))

    if args.diff:
        show_diff(final_mapping, output_path)

    print()
    if args.format in ("json", "both"):
        save_json(final_mapping, output_path)
    if args.format in ("csv", "both"):
        csv_path = os.path.splitext(output_path)[0] + ".csv"
        save_csv(detail_df, csv_path)

    known = sum(1 for v in final_mapping.values() if v["industry_l1"] != "未知")
    print(f"\n📈 行业覆盖: {known}/{len(final_mapping)}")
    print(f"⏱  耗时: {time.time() - start:.1f}s")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
