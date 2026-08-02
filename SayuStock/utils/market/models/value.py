"""估值时间序列。"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass

from ..enums import ValueKind
from .symbol import SymbolRef


@dataclass(frozen=True, slots=True)
class ValuePoint:
    day: date
    value: float


@dataclass(frozen=True, slots=True)
class ValueSeries:
    symbol: SymbolRef
    kind: ValueKind
    points: tuple[ValuePoint, ...]
