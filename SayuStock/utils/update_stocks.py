#!/usr/bin/env python3
"""update_stocks.py — 用东财 API 更新中国 A 股代码-名称及行业映射。

数据源（全部走内置 ``EASTMONEY_REQUESTER``，不依赖 akshare）：

1. ``clist``：``沪深京A`` 全市场分页，拿代码 / 名称 / f100 所属行业
2. 行业板块菜单（``get_menu(mode=2)``）+ 各板块成分 ``clist``：
   用板块名覆盖 industry_l1（更贴近云图分类口径）

输出字段与 ``constant.StockInfo`` 对齐::

    {code: {"name": str, "industry_l1": str, "industry_l2": str}}

说明：东财行业板块是**一层**分类，不再构造申万二级树；
``industry_l2`` 优先写 clist 的 f100（个股所属行业），没有则与 l1 相同。
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

# 作为脚本直接运行时补齐 import 路径
# .../plugins/SayuStock/SayuStock/utils/update_stocks.py
_SCRIPT_DIR = Path(__file__).resolve().parent
_PLUGIN_ROOT = _SCRIPT_DIR.parents[2]  # .../plugins/SayuStock （import SayuStock）
_REPO_ROOT = _SCRIPT_DIR.parents[5]  # .../gsuid_core 仓库根（import gsuid_core）
for _p in (_PLUGIN_ROOT, _REPO_ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from gsuid_core.logger import logger  # noqa: E402
from SayuStock.utils.constant import market_dict  # noqa: E402
from SayuStock.utils.eastmoney import EASTMONEY_REQUESTER  # noqa: E402

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
# 行业板块 → 成分股（industry_l1）
# ──────────────────────────────────────────────


async def fetch_industry_map() -> dict[str, str]:
    """行业板块名作 industry_l1：遍历东财行业菜单 + 成分 clist。"""
    print("\n[2/2] 东财行业板块菜单 + 成分...")
    try:
        menu = await EASTMONEY_REQUESTER.get_menu(2)
    except Exception as e:
        print(f"❌ 获取行业菜单失败: {e}")
        return {}

    if not menu:
        print("❌ 行业菜单为空")
        return {}

    items = list(menu.items())
    total = len(items)
    print(f"       共 {total} 个行业板块，开始拉成分股...")

    industry_map: dict[str, str] = {}
    sem = asyncio.Semaphore(INDUSTRY_CONCURRENCY)
    done = 0
    lock = asyncio.Lock()

    async def _one(name: str, code: str) -> None:
        nonlocal done
        fs = code if str(code).startswith("b:") else f"b:{code}"
        async with sem:
            try:
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
                    # 先到先得；同股多板块时保留第一次（菜单顺序）
                    if stock_code not in industry_map:
                        industry_map[stock_code] = name
            except Exception as e:
                logger.warning(f"[update_stocks] 行业 {name}({code}) 失败: {e}")
            finally:
                await asyncio.sleep(INDUSTRY_PAUSE_S)
                async with lock:
                    done += 1
                    print(f"\r       行业进度 [{done:03d}/{total}] {name[:12]:<12}", end="", flush=True)

    await asyncio.gather(*[_one(n, c) for n, c in items])
    print(f"\n       ✅ 行业映射完成，覆盖 {len(industry_map)} 只股票")
    return industry_map


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
    board_map: dict[str, str] = {}
    if not args.skip_industry_boards:
        board_map = await fetch_industry_map()

    # 3. 融合：l1 优先板块名，l2 优先 f100，缺省「未知」
    final_mapping: dict[str, dict[str, str]] = {}
    for code, name in base_mapping.items():
        l1 = board_map.get(code) or f100_map.get(code) or "未知"
        l2 = f100_map.get(code) or l1
        final_mapping[code] = {
            "name": name,
            "industry_l1": l1,
            "industry_l2": l2,
        }

    detail_df["industry_l1"] = detail_df["code"].map(lambda x: final_mapping[x]["industry_l1"])
    detail_df["industry_l2"] = detail_df["code"].map(lambda x: final_mapping[x]["industry_l2"])

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
