"""默认策略：多因子（技术 / 基本面 / 舆情 / 波动调节）。

硬闸只查入场止损，与升级前行为一致。四维评分写在 prompt 里由 LLM 执行。
"""

from __future__ import annotations

from typing import Any, Mapping

from .base import Strategy, GateInput, ParamSpec, PoolPreference
from .tools import MULTIFACTOR_RESEARCH_TOOLS
from ..strategy import indicators_have_entry_stop

__all__ = ["MultiFactorStrategy"]


class MultiFactorStrategy(Strategy):
    id = "multi_factor"
    name = "多因子"
    description = "原来的均衡打法：技术面 + 基本面 + 事件舆情 + 波动率一起看；AI 选股，买入必须带止损。"
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
            "- 四维评分：技术≈40% / 基本面≈30% / 事件舆情≈15% / 波动率调节。\n"
            f"- buy 建议 score ≥ {min_score:.2f}；不足则 hold 并在 reason 写清差在哪一维。\n"
            "- buy 的 snapshot/indicators **必须**写入 plan_stop_pct(<0) 或 plan_stop_price(>0)"
            "（也接受 stop_pct / stop_price）；否则拒绝落库。请先调 stock_indicators。\n"
            "- 成交只调 trade_insert（持仓随成交写入）；禁止与 position_upsert 并行改股数。\n"
            "- 禁止纯技术面强 buy：事件/舆情未检索时最多给试探仓。\n"
        )

    def research_phases(self) -> str:
        return (
            "Phase 宏观：get_latest_news + get_market_overview / get_sector_heatmap；"
            "榜单 get_market_ranking 只作线索。\n"
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
        if not indicators_have_entry_stop(dict(gate.indicators)):
            return (
                "⚠️ buy 须在 indicators/snapshot JSON 写入 plan_stop_pct(<0) 或 "
                "plan_stop_price(>0)（可解析数值止损）；已拒绝落库，请补全后重试"
            )
        return ""
