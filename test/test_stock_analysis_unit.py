"""stock_analysis 纯逻辑单测（不依赖网络、不触发整包插件注册）。"""

from __future__ import annotations

import sys
import importlib.util
from types import ModuleType
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_stock_analysis_test"


def _ensure_pkg() -> None:
    """搭出最小包骨架，避免 import SayuStock 触发整条插件注册链。"""
    if PKG_NAME in sys.modules:
        return
    pkg = ModuleType(PKG_NAME)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg

    utils = ModuleType(f"{PKG_NAME}.utils")
    utils.__path__ = [str(PKG_ROOT / "utils")]
    sys.modules[f"{PKG_NAME}.utils"] = utils

    sa = ModuleType(f"{PKG_NAME}.stock_analysis")
    sa.__path__ = [str(PKG_ROOT / "stock_analysis")]
    sys.modules[f"{PKG_NAME}.stock_analysis"] = sa


def _load(dotted: str, rel_path: str) -> ModuleType:
    _ensure_pkg()
    # 先确保依赖子模块可被相对 import 解析
    name = f"{PKG_NAME}.{dotted}"
    if name in sys.modules:
        return sys.modules[name]
    # 预加载 technical 的依赖
    if dotted == "stock_analysis.technical":
        _load("utils.kline", "utils/kline.py")
        _load("utils.indicators", "utils/indicators.py")
    if dotted == "stock_analysis.screener":
        # screener 只测纯函数，打桩 universe 避免拉网络
        uni = ModuleType(f"{PKG_NAME}.stock_analysis.universe")

        async def _noop(*_a, **_k):  # pragma: no cover
            raise RuntimeError("network should not be called in unit tests")

        setattr(uni, "fetch_a_share_universe", _noop)
        setattr(uni, "fetch_board_members", _noop)
        setattr(uni, "resolve_concept_fs", _noop)
        setattr(uni, "resolve_industry_fs", _noop)
        sys.modules[f"{PKG_NAME}.stock_analysis.universe"] = uni

    spec = importlib.util.spec_from_file_location(name, PKG_ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    # 让相对 import 在父包下解析
    parent_name = name.rsplit(".", 1)[0]
    if parent_name not in sys.modules:
        parent = ModuleType(parent_name)
        parent.__path__ = [str((PKG_ROOT / rel_path).parent)]
        sys.modules[parent_name] = parent
    spec.loader.exec_module(mod)
    return mod


technical = _load("stock_analysis.technical", "stock_analysis/technical.py")
screener = _load("stock_analysis.screener", "stock_analysis/screener.py")


def test_parse_period():
    code, q = technical.parse_period_and_query("日k 茅台")
    assert code == "101" and q == "茅台"
    code, q = technical.parse_period_and_query("日K 茅台")
    assert code == "101" and q == "茅台"
    code, q = technical.parse_period_and_query("周k 600519")
    assert code == "102" and q == "600519"
    code, q = technical.parse_period_and_query("贵州茅台")
    assert code == "101" and "茅台" in q


def test_parse_period_no_single_char_swallow():
    """单字 日/周/月 不得截断股票名。"""
    assert technical.parse_period_and_query("日经225") == ("101", "日经225")
    assert technical.parse_period_and_query("周大福") == ("101", "周大福")
    assert technical.parse_period_and_query("月城") == ("101", "月城")
    # 多字符别名可无空格
    code, q = technical.parse_period_and_query("日k茅台")
    assert code == "101" and q == "茅台"
    code, q = technical.parse_period_and_query("日k 茅台")
    assert code == "101" and q == "茅台"


def test_build_technical_from_synthetic_klines():
    klines: list[str] = []
    price = 100.0
    # 合法日期：用 2024-01-01 起连续序号由 klines_to_df 当字符串解析
    for i in range(80):
        day = (i % 28) + 1
        o = price
        c = price * (1.01 if i % 3 else 0.995)
        h = max(o, c) * 1.01
        lo = min(o, c) * 0.99
        klines.append(f"2024-01-{day:02d},{o:.2f},{c:.2f},{h:.2f},{lo:.2f},1000,1e7,1,0.5,0.5,1.0")
        price = c
    rep = technical.build_technical_report(name="测试", code="000001", period_code="101", klines=klines)
    assert not isinstance(rep, str)
    assert 0 <= rep.score <= 100
    assert rep.trend in ("偏多", "偏空", "震荡")
    assert rep.summary
    # 无信号双重计分膨胀：满分维度 35+30+20+15
    assert rep.score <= 100


def test_screener_parse_and_filter():
    leftover, ind, con, filters = screener.parse_screener_query("行业 半导体 PE<30 涨跌幅>2 市值50-200")
    assert ind == "半导体"
    assert con is None
    assert leftover == ""
    assert any(f[0] == "pe" and f[1] == "<" for f in filters)
    assert any(f[0] == "pct" and f[1] == ">" for f in filters)
    assert any(f[0] == "mv_yi" and f[1] == "between" for f in filters)

    df = pd.DataFrame(
        [
            {"code": "1", "name": "A", "pe": 20, "pct": 3, "mv_yi": 100, "turnover": 1, "vol_ratio": 1},
            {"code": "2", "name": "B", "pe": 40, "pct": 5, "mv_yi": 100, "turnover": 1, "vol_ratio": 1},
            {"code": "3", "name": "C", "pe": 15, "pct": 1, "mv_yi": 100, "turnover": 1, "vol_ratio": 1},
        ]
    )
    out = screener.apply_filters(df, filters)
    assert list(out["code"]) == ["1"]


def test_screener_no_die_fu_alias():
    """跌幅不再映射到 pct，避免 跌幅>2 语义反转。"""
    leftover, ind, con, filters = screener.parse_screener_query("跌幅>2")
    assert not any(f[0] == "pct" for f in filters)
    assert "跌幅" in leftover or leftover  # 未解析残留


def test_screener_industry_and_concept_both():
    leftover, ind, con, filters = screener.parse_screener_query("行业 半导体 概念 人工智能 PE<30")
    assert ind == "半导体"
    assert con == "人工智能"
