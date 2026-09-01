"""个股命令周期前缀：K 线 vs 多日分时。"""

from __future__ import annotations

KLINE_PREFIX_TO_CODE: dict[str, str] = {
    "5k": "5",
    "15k": "15",
    "30k": "30",
    "60k": "60",
    "k线": "100",
    "日线": "101",
    "日k": "101",
    "周k": "102",
    "周线": "102",
    "月k": "103",
    "月线": "103",
    "季k": "104",
    "季线": "104",
    "半年k": "105",
    "半年线": "105",
    "年k": "106",
    "年线": "106",
}

# 长前缀必须排在短前缀前面，避免「五日分时」被「五日」截断
INTRADAY_DAYS_PREFIX_TO_NDAYS: dict[str, int] = {
    "五日分时": 5,
    "5日分时": 5,
    "五日": 5,
    "5日": 5,
    "五天": 5,
    "5天": 5,
}

_INTRADAY_NDAYS_SECTOR = "single-stock-ndays-"


def parse_stock_img_request(raw: str) -> tuple[str, str]:
    """解析「个股」命令正文 → (标的文本, sector)。"""
    text = raw.strip()
    lowered = text.lower()
    for prefix, ndays in INTRADAY_DAYS_PREFIX_TO_NDAYS.items():
        if lowered.startswith(prefix.lower()):
            content = text[len(prefix) :].strip().replace("分时", "").strip()
            return content, f"{_INTRADAY_NDAYS_SECTOR}{ndays}"
    kline_prefixes = sorted(KLINE_PREFIX_TO_CODE.keys(), key=len, reverse=True)
    for prefix in kline_prefixes:
        if lowered.startswith(prefix):
            content = text[len(prefix) :].strip()
            return content, f"single-stock-kline-{KLINE_PREFIX_TO_CODE[prefix]}"
    return text.replace("分时", "").strip(), "single-stock"


def intraday_ndays_from_sector(sector: str | None) -> int | None:
    if sector == "single-stock":
        return 1
    if sector is None:
        return None
    if not sector.startswith(_INTRADAY_NDAYS_SECTOR):
        return None
    rest = sector[len(_INTRADAY_NDAYS_SECTOR) :]
    if not rest.isdigit():
        return None
    value = int(rest)
    if value < 1:
        return None
    return value


def is_intraday_sector(sector: str | None) -> bool:
    return intraday_ndays_from_sector(sector) is not None
