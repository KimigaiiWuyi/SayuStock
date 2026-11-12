"""兼容个股入口对板块快照的树图分支。"""

from ..render.treemap import render_treemap
from ..utils.market.models import BoardSnapshot


async def to_fig(snap: BoardSnapshot, market: str, sector: str | None = None, layer: int = 2) -> str | bytes:
    return await render_treemap(snap, market, sector, layer)
