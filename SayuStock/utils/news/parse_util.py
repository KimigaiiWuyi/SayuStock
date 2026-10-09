"""不可信新闻 JSON 的显式取值。缺字段返回 None，不填默认正文。"""

from __future__ import annotations

from typing import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

_BJT = ZoneInfo("Asia/Shanghai")


def as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return value
    return None


def as_list(value: object) -> list[object] | None:
    if isinstance(value, list):
        return value
    return None


def opt_str(row: Mapping[str, object], key: str) -> str | None:
    if key not in row:
        return None
    raw = row[key]
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text else None


def opt_int(row: Mapping[str, object], key: str) -> int | None:
    if key not in row:
        return None
    raw = row[key]
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def parse_bjt_ms(text: str) -> int | None:
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_BJT)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)
