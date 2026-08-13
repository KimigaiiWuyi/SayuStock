"""策略注册表。

新增策略只需两步：在本包里写一个 ``Strategy`` 子类，然后加进 ``_REGISTRY``。
盘通过 ``SayuPaperAccount.strategy_id`` 引用策略、``strategy_params`` 存调参。

**未知 strategy_id 一律回落默认策略而不是抛异常**：``strategy_id`` 是 DB 里的自由
字符串，插件降级 / 回滚版本后完全可能读到本版不存在的策略。让心跳因为这个崩掉，
代价远大于用默认策略先跑着 —— 但 ``resolve`` 会把回落写进日志，不静默。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Mapping, Optional

from gsuid_core.logger import logger

from .base import Strategy, GateInput, ParamSpec, PoolPreference
from .tools import CORE_DECISION_TOOLS
from .multi_factor import MultiFactorStrategy
from .volume_extremum import VolumeExtremumStrategy

__all__ = [
    "Strategy",
    "GateInput",
    "ParamSpec",
    "PoolPreference",
    "DEFAULT_STRATEGY_ID",
    "get_strategy",
    "resolve",
    "list_strategies",
    "strategy_ids",
    "describe_all",
    "CORE_DECISION_TOOLS",
    "decision_profiles",
]

DEFAULT_STRATEGY_ID: str = MultiFactorStrategy.id

_REGISTRY: Dict[str, Strategy] = {
    MultiFactorStrategy.id: MultiFactorStrategy(),
    VolumeExtremumStrategy.id: VolumeExtremumStrategy(),
}


def get_strategy(strategy_id: str) -> Optional[Strategy]:
    """精确查一个策略；不存在返回 None（调用方自己决定怎么处理）。"""
    return _REGISTRY.get((strategy_id or "").strip())


def resolve(strategy_id: str) -> Strategy:
    """查策略，查不到回落默认策略并记一条 warning。"""
    key = (strategy_id or "").strip()
    found = _REGISTRY.get(key)
    if found is not None:
        return found
    if key:
        logger.warning(f"[SayuStock][PaperTrade] 未知策略 {key!r}，已回落 {DEFAULT_STRATEGY_ID}")
    return _REGISTRY[DEFAULT_STRATEGY_ID]


def resolve_with_params(
    strategy_id: str,
    raw_params: Optional[Mapping[str, Any]] = None,
) -> Tuple[Strategy, Dict[str, Any]]:
    """一次拿到 ``(策略对象, 归一化后的参数)`` —— 调用方几乎总是同时需要两者。"""
    strategy = resolve(strategy_id)
    return strategy, dict(strategy.normalize_params(raw_params))


def list_strategies() -> List[Strategy]:
    return list(_REGISTRY.values())


def decision_profiles() -> List[Strategy]:
    """每个 agent_profile 只留注册表里第一个策略（给 gate / 代理注册用）。"""
    seen: set[str] = set()
    out: List[Strategy] = []
    for s in _REGISTRY.values():
        if s.agent_profile in seen:
            continue
        seen.add(s.agent_profile)
        out.append(s)
    return out


def strategy_ids() -> List[str]:
    return list(_REGISTRY.keys())


def describe_all() -> str:
    """``模拟盘策略列表`` / 建盘失败时用的清单。只能从注册表里选。"""
    lines: List[str] = [
        "【模拟盘 · 可选策略】",
        "建盘 / 换策略只能从下面选，不能自造策略名。",
        "",
    ]
    for s in _REGISTRY.values():
        mark = "（默认）" if s.id == DEFAULT_STRATEGY_ID else ""
        lines.append(f"· {s.id}{mark} — {s.name}")
        lines.append(f"    {s.description}")
        for spec in s.param_specs:
            lines.append(f"    - {spec.key}={spec.default}  {spec.label}")
    lines.append("")
    lines.append("例：模拟盘创建 放量盘 volume_extremum")
    return "\n".join(lines)
