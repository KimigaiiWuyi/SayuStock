"""量能极值策略：门槛、工具清单、Kanban 流程。区位算法见 ``volume_structure``。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import Strategy, GateInput, ParamSpec, PoolPreference
from .tools import VOLUME_RESEARCH_TOOLS
from ..strategy import indicators_have_entry_stop
from .volume_structure import (
    VolumeStructure,
    load_structure,
    evaluate_location,
    measure_from_ohlcv,
    structure_from_indicators,
)

__all__ = [
    "VolumeExtremumStrategy",
    "VolumeStructure",
    "measure_from_ohlcv",
    "evaluate_location",
    "load_structure",
]


class VolumeExtremumStrategy(Strategy):
    id = "volume_extremum"
    name = "底部放量 / 顶部放量"
    description = (
        "先用近 5 年月 K 粗筛是否在历史底部/顶部，过了再用日 K 确认局部区位和放量。顶底由函数算，不是 AI 主观判断。"
    )
    agent_profile = "papertrade_decision_volume"
    extra_tools = VOLUME_RESEARCH_TOOLS
    match_keywords = ("量能极值", "放量盘", "volume_extremum", "底部放量")

    @property
    def param_specs(self) -> tuple[ParamSpec, ...]:
        return (
            ParamSpec("month_lookback_years", "月K粗筛回看年数", 5, min_value=2, max_value=10),
            ParamSpec("month_bottom_pct", "月K底部分位上限（粗筛）", 0.25, min_value=0.05, max_value=0.45),
            ParamSpec("month_top_pct", "月K顶部分位下限（粗筛）", 0.75, min_value=0.55, max_value=0.98),
            ParamSpec("lookback_m", "日K确认窗口（交易日）", 60, min_value=20, max_value=250),
            ParamSpec("bottom_pct", "日K底部分位上限", 0.15, min_value=0.05, max_value=0.40),
            ParamSpec("top_pct", "日K顶部分位下限", 0.85, min_value=0.60, max_value=0.98),
            ParamSpec("vol_ma_n", "日K量能均线窗口", 20, min_value=5, max_value=60),
            ParamSpec("vol_ratio_min", "相对放量阈值（当日量/均量）", 2.0, min_value=1.2, max_value=10.0),
            ParamSpec("require_bullish_close", "底放量买要求收阳", True),
        )

    def prompt_block(self, params: Mapping[str, Any]) -> str:
        years = int(params["month_lookback_years"])
        m_bot = float(params["month_bottom_pct"])
        m_top = float(params["month_top_pct"])
        days = int(params["lookback_m"])
        d_bot = float(params["bottom_pct"])
        d_top = float(params["top_pct"])
        vr = float(params["vol_ratio_min"])
        return (
            "【策略：底部放量 / 顶部放量】\n"
            f"- 顶底由**系统函数**判定：先近 {years} 年月 K 粗筛"
            f"（底≤{m_bot:.2f} / 顶≥{m_top:.2f}），过了才拉近 {days} 日 K 确认"
            f"（底≤{d_bot:.2f} / 顶≥{d_top:.2f}）+ 放量≥{vr:.1f}。\n"
            "- 不要自己编 month_percentile / close_percentile / rel_volume。\n"
            "- 月 K 不在五年极值区会直接拒单。止损卖写 stop_triggered=true。\n"
            "- buy 须带 plan_stop。可用 papertrade_volume_scan 查看函数结果。\n"
        )

    def agent_prompt_extra(self) -> str:
        return (
            "【量能极值额外纪律】顶底/放量由系统用月K+日K函数判定，"
            "不要自己编分位和量比；可用 papertrade_volume_scan 查看结果；"
            "不要为交差去扫财报/榜单。止损卖必须 snapshot.stop_triggered=true。"
        )

    def research_phases(self) -> str:
        return (
            "Phase 研究：对持仓和 1~3 只候选调 papertrade_volume_scan"
            "（系统会先月K粗筛，不过关不拉日K）。"
            "结构不满足就 hold，不要用基本面故事硬买。"
            "写止损时可调 stock_indicators 看 ATR/支撑。\n"
        )

    def pool_preference(self, params: Mapping[str, Any]) -> PoolPreference:
        return PoolPreference(
            source_weights={
                "sector": 0.0,
                "concept": 0.0,
                "hotmap": 1.0,
                "gainer": 0.6,
                "laggard": 2.0,
                "amount": 2.0,
                "quality": 0.0,
                "news": 0.0,
            },
            target_size=12,
            rotate_out=5,
            filter_overheated=True,
            seed_bluechip=False,
        )

    def gate_buy(self, params: Mapping[str, Any], gate: GateInput) -> str:
        ind = dict(gate.indicators)
        if not indicators_have_entry_stop(ind):
            return (
                "⚠️ buy 须在 snapshot/indicators 写入 plan_stop_pct(<0) 或 plan_stop_price(>0)；已拒绝落库，请补全后重试"
            )
        struct = structure_from_indicators(ind)
        if struct is None:
            return "⚠️ 未能算出顶底/放量（K 线不足或拉行情失败），本单已拒绝。"
        years = int(params["month_lookback_years"])
        if not struct.month_passed:
            mp = struct.month_percentile
            cap = float(params["month_bottom_pct"])
            shown = "—" if mp is None else f"{mp:.2f}"
            return f"⚠️ 月K粗筛未过：近 {years} 年分位 {shown} > {cap:.2f}，不在历史底部，未拉日K。请改 hold。"
        if struct.rel_volume is None or struct.close_percentile is None:
            return "⚠️ 月K已过关但日K确认失败，本单已拒绝。"
        vr_min = float(params["vol_ratio_min"])
        bottom = float(params["bottom_pct"])
        if struct.rel_volume < vr_min:
            return f"⚠️ 日K未放量：rel_volume={struct.rel_volume:.2f} < {vr_min:.1f}。请改 hold。"
        if struct.close_percentile > bottom:
            return (
                f"⚠️ 日K不在近 {int(params['lookback_m'])} 日底部："
                f"close_percentile={struct.close_percentile:.2f} > {bottom:.2f}。请改 hold。"
            )
        if bool(params["require_bullish_close"]) and not struct.bullish_close:
            return "⚠️ 底放量买要求收阳（日K开盘 vs 收盘），请改 hold。"
        return ""

    def gate_sell(self, params: Mapping[str, Any], gate: GateInput) -> str:
        struct = structure_from_indicators(gate.indicators)
        if struct is None:
            return "⚠️ 未能算出顶底/放量（K 线不足或拉行情失败），本单已拒绝。"
        years = int(params["month_lookback_years"])
        if not struct.month_passed:
            mp = struct.month_percentile
            floor = float(params["month_top_pct"])
            shown = "—" if mp is None else f"{mp:.2f}"
            return (
                f"⚠️ 月K粗筛未过：近 {years} 年分位 {shown} < {floor:.2f}，"
                "不在历史顶部，未拉日K。止损请写 stop_triggered=true。"
            )
        if struct.rel_volume is None or struct.close_percentile is None:
            return "⚠️ 月K已过关但日K确认失败，本单已拒绝。"
        vr_min = float(params["vol_ratio_min"])
        top = float(params["top_pct"])
        if struct.rel_volume < vr_min:
            return f"⚠️ 日K顶部未放量：rel_volume={struct.rel_volume:.2f} < {vr_min:.1f}。止损请写 stop_triggered=true。"
        if struct.close_percentile < top:
            return (
                f"⚠️ 日K不在近 {int(params['lookback_m'])} 日顶部："
                f"close_percentile={struct.close_percentile:.2f} < {top:.2f}。"
                "止损请写 stop_triggered=true。"
            )
        return ""
