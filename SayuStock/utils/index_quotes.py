"""自选和持仓顶部的指数条。按代码报价，不走涨跌幅榜。"""

from __future__ import annotations

from .market import DisplayItem, get_market, is_market_error

# 名称与东财 secid。中证2000 备用源没有盘口，报价失败时这张卡片缺席。
_INDEXES: tuple[tuple[str, str], ...] = (
    ("上证指数", "1.000001"),
    ("深证成指", "0.399001"),
    ("创业板指", "0.399006"),
    ("上证50", "1.000016"),
    ("沪深300", "1.000300"),
    ("中证A500", "1.000510"),
    ("中证2000", "2.932000"),
    ("国债指数", "1.000012"),
)


async def quote_index_items(names: list[str]) -> list[DisplayItem]:
    """按给定顺序取指数。某一只失败就跳过，不让整页空白。"""
    by_name = {name: secid for name, secid in _INDEXES}
    ordered = [(name, by_name[name]) for name in names if name in by_name]
    if not ordered:
        return []
    results = await get_market().quotes([secid for _, secid in ordered])
    items: list[DisplayItem] = []
    for (name, _), res in zip(ordered, results, strict=True):
        if is_market_error(res):
            continue
        change = float(res.change_pct) if res.change_pct is not None else 0.0
        items.append(
            DisplayItem(
                name=name,
                price=float(res.price),
                change_pct=change,
                amount=res.amount,
                code=res.symbol.code,
                provider=res.provider,
            )
        )
    return items
