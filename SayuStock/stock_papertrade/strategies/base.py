"""策略契约：工具清单 + 候选池 + 落库硬闸 + Kanban 流程。

LLM 只负责在工具结果上选标的；量价结构 / 止损等硬条件必须是函数，不能让模型报数。
每个策略还要声明自己的研究工具（``extra_tools``），决策代理按注册表组装，不要在
``stock_agent`` / ``commands`` 里再写一份工具列表。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Union, Mapping, Optional
from dataclasses import field, dataclass

from .tools import CORE_DECISION_TOOLS

__all__ = [
    "ParamSpec",
    "PoolPreference",
    "GateInput",
    "Strategy",
]

ParamValue = Union[int, float, str, bool]


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """单个策略参数的声明（``模拟盘策略参数`` 命令与 WebConsole 都读它）。

    ``min_value`` / ``max_value`` 只对数值型有意义；越界时 ``normalize_params``
    **夹紧而不是报错** —— 用户手滑填个 999 不该让整个盘的策略参数解析失败、
    静默回退默认值（那样更难排查）。
    """

    key: str
    label: str
    default: ParamValue
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class PoolPreference:
    """生效点 2：候选池轮换偏好。

    ``source_weights`` 是**相对权重**而不是绝对条数：候选池的各路来源（行业龙头 /
    概念 / 热股 / 涨幅榜 / 跌幅榜 / 成交额 / 高ROE / 新闻）拉到的数量本来就不稳定，
    写死条数会在某一路挂掉时让池子塌一半。权重为 0 表示**完全禁用**该来源。
    """

    source_weights: Dict[str, float] = field(default_factory=dict)
    target_size: int = 0  # 0 = 用全局默认
    rotate_out: int = 0  # 0 = 用全局默认
    filter_overheated: bool = True
    seed_bluechip: bool = True


@dataclass(frozen=True, slots=True)
class GateInput:
    """生效点 3 的入参。

    ``indicators`` 是 LLM 写进 ``decision_insert.indicators`` /
    ``trade_insert.snapshot`` 的那个 JSON（已解析成 dict）。策略要卡什么字段，
    就必须在 ``prompt_block`` 里明确要求 LLM 写什么字段 —— 两者是一对的，
    只加闸不改提示词的结果是 LLM 永远补不齐、心跳空转。
    """

    stock_code: str
    indicators: Mapping[str, Any]
    score: float = 0.0
    source: str = "decision"  # "decision" | "trade"
    side: str = "buy"


class Strategy(ABC):
    """策略基类。子类必须给出 ``id`` / ``name`` 并实现三个生效点。"""

    id: str = ""
    name: str = ""
    description: str = ""
    agent_profile: str = "papertrade_decision_agent"
    extra_tools: tuple[str, ...] = ()
    match_keywords: tuple[str, ...] = ()

    # ── 参数 ──
    @property
    def param_specs(self) -> tuple[ParamSpec, ...]:
        """本策略暴露的可调参数；默认无参数。"""
        return ()

    def normalize_params(self, raw: Optional[Mapping[str, Any]] = None) -> Dict[str, ParamValue]:
        """把用户/DB 里的原始参数补默认 + 夹紧到合法范围。

        未声明的键**直接丢弃**：``strategy_params`` 是自由 JSON 列，改过策略的盘会
        残留上一个策略的键，带着它们跑会让 ``gate_buy`` 读到语义完全不同的值。
        """
        out: Dict[str, ParamValue] = {}
        source: Mapping[str, Any] = raw or {}
        for spec in self.param_specs:
            value: Any = source.get(spec.key, spec.default)
            if isinstance(spec.default, bool):
                out[spec.key] = _to_bool(value, bool(spec.default))
                continue
            if isinstance(spec.default, (int, float)):
                num = _to_float(value)
                if num is None:
                    out[spec.key] = spec.default
                    continue
                if spec.min_value is not None:
                    num = max(spec.min_value, num)
                if spec.max_value is not None:
                    num = min(spec.max_value, num)
                out[spec.key] = int(num) if isinstance(spec.default, int) else float(num)
                continue
            out[spec.key] = str(value)
        return out

    def decision_tools(self) -> list[str]:
        """账本工具 + 本策略研究工具。决策代理 / Kanban 只认这份清单。"""
        seen: set[str] = set()
        out: list[str] = []
        for name in (*CORE_DECISION_TOOLS, *self.extra_tools):
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
        return out

    def agent_prompt_extra(self) -> str:
        """拼在通用决策 prompt 后面的策略补充。默认空。"""
        return ""

    def research_phases(self) -> str:
        """Kanban 决策任务里「研究」那一段。默认不额外研究。"""
        return ""

    def kanban_decision_task(self, account_name: str, params: Mapping[str, ParamValue]) -> str:
        """建心跳树时写入的决策子任务全文。"""
        research = self.research_phases()
        return (
            f"每 30 分钟决策心跳（模拟盘「{account_name}」）：\n"
            f"{self.prompt_block(params)}\n"
            "Phase 0: **每轮必调** papertrade_candidate_refresh() 轮换候选池，"
            "再 papertrade_agent_pool_list 看池子。\n"
            "Phase 1: papertrade_account_query + papertrade_position_list；"
            "有持仓时对每只回看 trade/decision 里的入场计划，禁止每轮重编止损。\n"
            f"{research}"
            "Phase 执行: buy/sell 走 match_order → trade_insert → position_upsert → "
            "decision_insert；hold 只写 decision_insert。\n"
            "最终消息只输出 <<NO_BROADCAST>>。\n"
        )

    def kick_task(self, account_name: str, params: Mapping[str, ParamValue]) -> str:
        """init / 启用时立刻踢一轮决策用的短任务。"""
        return f"为模拟盘「{account_name}」立即执行一次心跳决策。\n{self.kanban_decision_task(account_name, params)}"

    # ── 生效点 1：提示词注入 ──
    @abstractmethod
    def prompt_block(self, params: Mapping[str, ParamValue]) -> str:
        """写进 Kanban 决策子任务描述的操作纪律段。

        必须**显式列出 ``gate_buy`` 会卡的字段名**，否则 LLM 补不齐必填项，
        每轮买入都被拒、白烧 token。
        """
        ...

    # ── 生效点 2：候选池偏好 ──
    @abstractmethod
    def pool_preference(self, params: Mapping[str, ParamValue]) -> PoolPreference:
        """候选池轮换偏好。不想干预时返回 ``PoolPreference()``。"""
        ...

    # ── 生效点 3：数据库硬闸 ──
    @abstractmethod
    def gate_buy(self, params: Mapping[str, ParamValue], gate: GateInput) -> str:
        """买入落库前的硬校验；放行返回 ``""``，拒绝返回给 LLM 看的中文理由。

        理由必须**可执行**（说清缺什么字段、该调哪个工具补），否则 LLM 只会原样
        重试同一个被拒的请求。
        """
        ...

    def gate_sell(self, params: Mapping[str, ParamValue], gate: GateInput) -> str:
        """卖出结构硬闸；默认放行。止损单请走 ``validate_entry`` 的 stop 旁路。"""
        return ""

    def validate_entry(self, params: Mapping[str, ParamValue], gate: GateInput) -> str:
        """买卖落库前的统一硬闸。sell + stop_triggered 一律放行，避免锁死风控。"""
        side = str(gate.side or "buy").strip().lower()
        if side == "sell":
            if _is_stop_triggered(gate.indicators):
                return ""
            return self.gate_sell(params, gate)
        return self.gate_buy(params, gate)


def _is_stop_triggered(indicators: Mapping[str, Any]) -> bool:
    raw = indicators.get("stop_triggered")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on", "是")
    return False


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f or f in (float("inf"), float("-inf")) else f
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except (ValueError, AttributeError):
            return None
        return None if f != f or f in (float("inf"), float("-inf")) else f
    return None


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on", "是", "开"):
            return True
        if low in ("0", "false", "no", "off", "否", "关"):
            return False
    return default
