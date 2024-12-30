from typing import Optional

from .resource_path import DATA_PATH


def get_file(
    market: str,
    suffix: str,
    sector: Optional[str] = None,
    sp: Optional[str] = None,
):
    a = f'{market}_{sector}_{sp}_data'
    return DATA_PATH / f"{a}.{suffix}"
