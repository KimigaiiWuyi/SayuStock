"""默认策略：多因子（宏观 / 技术 / 基本面 / 舆情 / 波动调节）。

2026-09 起宏观是第一维：buy 必须先过「宏观三问」定档，快照里带 ``macro_regime`` +
``macro_note``，表内有高危 risk_off 事件时不得标进攻——这些由硬闸兜底，不只是提示词建议。
"""

from __future__ import annotations

from typing import Any, Mapping

from .base import Strategy, GateInput, ParamSpec, PoolPreference
from .tools import MULTIFACTOR_RESEARCH_TOOLS
from ..strategy import macro_gate_reason, indicators_have_entry_stop
from ...stock_macro.prompts import MACRO_POSITION_RULES

__all__ = ["MultiFactorStrategy"]


class MultiFactorStrategy(Strategy):
    id = "multi_factor"
    name = "多因子"
    description = (
        "均衡打法：先宏观定档（钱多钱少 / 成长价值相 / 重大事件），再看技术 + 基本面 + 事件舆情；"
        "AI 选股，买入必须带止损与宏观档位。"
    )
    agent_profile = "papertrade_decision_agent"
    extra_tools = MULTIFACTOR_RESEARCH_TOOLS
    match_keywords = ("模拟盘", "模拟盘买", "模拟盘卖", "看盘", "决策", "虚拟盘", "papertrade")

    @property
    def param_specs(self) -> tuple[ParamSpec, ...]:
        return (
            ParamSpec(
                key="min_buy_score",
                label="买入建议最低评分（提示词软约束，不进硬闸）",
                default=0.30,
                min_value=0.0,
                max_value=1.0,
            ),
        )

    def prompt_block(self, params: Mapping[str, Any]) -> str:
        min_score = float(params["min_buy_score"])
        return (
            "【策略：多因子】\n"
            "- 先宏观定档再看个股：每轮 Phase 3 必调 macro_event_list 并回答宏观三问，"
            "得出 进攻/中性/防御 之一；档位决定总仓与单票上限（见下）。\n"
            "- 五维评分：宏观/事件≈30% / 技术≈35% / 基本面≈25% / 波动率调节 ±10%。\n"
            f"- buy 建议 score ≥ {min_score:.2f}；不足则 hold 并在 reason 写清差在哪一维。\n"
            "- buy 的 snapshot/indicators JSON 顶层**必须**同时写：plan_stop_pct(<0) 或 "
            "plan_stop_price(>0)；macro_regime（进攻/中性/防御）；macro_note（≥8 字宏观依据）。"
            "缺任一项拒绝落库。请先调 stock_indicators 与 macro_event_list。\n"
            "- 表内有 severity≥4 的 risk_off 进行中事件时，buy 不得标 macro_regime=进攻。\n"
            "- reason 开头固定写「宏观:{档位}|{相}|{一句依据}」。\n"
            "- 成交只调 trade_insert（持仓随成交写入）；禁止与 position_upsert 并行改股数。\n"
            "- 禁止纯技术面 buy：宏观三问没答、事件/舆情未检索时最多给试探仓。\n"
            f"{MACRO_POSITION_RULES}\n"
        )

    def research_phases(self) -> str:
        return (
            "Phase 宏观（先做）：macro_event_list(status=open) → get_latest_news + "
            "get_market_overview / get_sector_heatmap → 回答宏观三问 → 定档；"
            "表里 stale=true 的事件可顺手 web_search 1 次并 macro_event_upsert 更新；"
            "快讯里出现表内没有的关税/地缘/央行/油价大事先登记再继续。榜单 get_market_ranking 只作线索。\n"
            "Phase 个股：stock_indicators 多周期；"
            "财报优先复用旧 decision，仅新票/拟买才 stock_financials；"
            "持仓与拟买卖须 web_search 至少 1 次。\n"
        )

    def pool_preference(self, params: Mapping[str, Any]) -> PoolPreference:
        return PoolPreference(
            source_weights={
                "sector": 1.0,
                "concept": 1.0,
                "hotmap": 1.0,
                "gainer": 1.0,
                "laggard": 1.0,
                "amount": 1.0,
                "quality": 1.0,
                "news": 1.0,
            },
            filter_overheated=True,
            seed_bluechip=True,
        )

    def gate_buy(self, params: Mapping[str, Any], gate: GateInput) -> str:
        ind = dict(gate.indicators)
        if not indicators_have_entry_stop(ind):
            return (
                "⚠️ buy 须在 indicators/snapshot JSON 写入 plan_stop_pct(<0) 或 "
                "plan_stop_price(>0)（可解析数值止损）；已拒绝落库，请补全后重试"
            )
        return macro_gate_reason(ind)
