"""东财 JSON 边界解析工具：显式键检查，禁止静默默认值掩盖缺字段。"""

from __future__ import annotations

from typing import Mapping


def as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return value
    return None


def require_mapping(root: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    if key not in root:
        return None
    return as_mapping(root[key])


def opt_float(row: Mapping[str, object], key: str) -> float | None:
    if key not in row:
        return None
    raw = row[key]
    if raw is None or raw == "" or raw == "-":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def opt_str(row: Mapping[str, object], key: str) -> str | None:
    if key not in row:
        return None
    raw = row[key]
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text and text != "-" else None


def opt_int(row: Mapping[str, object], key: str) -> int | None:
    value = opt_float(row, key)
    if value is None:
        return None
    return int(value)
