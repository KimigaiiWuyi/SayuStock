"""标的标识。"""

from __future__ import annotations

from dataclasses import dataclass

from ..enums import AssetClass


@dataclass(frozen=True, slots=True)
class SymbolRef:
    """供应商无关的标的引用；provider_symbol 仅透传给对应 adapter。"""

    code: str
    name: str
    asset_class: AssetClass
    exchange: str
    provider_symbol: str
