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
    # 东财 SecurityTypeName / 版块标签，如 沪A、创业板、美股、韩股
    sec_type: str = ""

    @property
    def display_name(self) -> str:
        """带市场/版块后缀的展示名，例如「三星电子 (韩股)」「宁德时代 (创业板)」。"""
        base = (self.name or "").strip()
        st = (self.sec_type or "").strip()
        if base and st:
            if f"({st})" in base or f"（{st}）" in base:
                return base
            return f"{base} ({st})"
        return base or st
