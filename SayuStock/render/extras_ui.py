"""Shared display values for the simulation and analysis pages."""

from __future__ import annotations

from math import isfinite
from typing import Literal
from dataclasses import dataclass

Tone = Literal["up", "down", "flat", "neutral", "warn", "info"]


@dataclass(frozen=True, slots=True)
class Metric:
    label: str
    value: str
    tone: Tone = "neutral"
    note: str = ""


def direction(value: float | None) -> Tone:
    if value is None or not isfinite(value):
        return "neutral"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def fmt_number(value: object, digits: int = 2, *, signed: bool = False, suffix: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return "—"
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}{suffix}"
