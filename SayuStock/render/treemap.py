"""Market-cap-weighted treemap for board snapshots and the main market map."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from . import render_html
from .charts import squarify
from .palette import HEAT_UP, HEAT_DOWN, HEAT_NEUTRAL, mix
from .templates import render_template
from .chart_page import percent_text
from ..utils.render_data import CloudmapLeaf, build_cloudmap_render_data
from ..utils.market.models import BoardSnapshot
from ..utils.market.display import source_footer


@dataclass(slots=True)
class Tile:
    x: float
    y: float
    w: float
    h: float
    name: str
    change: str
    color: str
    font_size: float


@dataclass(slots=True)
class Group:
    x: float
    y: float
    w: float
    h: float
    name: str
    show_title: bool


@dataclass(slots=True)
class TreemapPage:
    title: str
    subtitle: str
    footer: str
    width: int
    unit: float
    plot_width: int
    plot_height: int
    groups: list[Group]
    tiles: list[Tile]
    heat_key: list[tuple[str, str]]


def _heat(change: float) -> str:
    shade = HEAT_UP if change > 0 else HEAT_DOWN
    return mix(shade, HEAT_NEUTRAL, min(abs(change) / 10, 1))


def prepare_treemap(
    snap: BoardSnapshot, market: str, sector: str | None = None, layer: int = 2, *, width: int = 1200
) -> TreemapPage | str:
    data = build_cloudmap_render_data(snap, market, sector, layer)
    if isinstance(data, str):
        return data
    grouped: dict[str, list[CloudmapLeaf]] = defaultdict(list)
    for leaf in data.leaves:
        grouped[leaf.category].append(leaf)

    def weight(leaves: list[CloudmapLeaf]) -> float:
        return sum(leaf.value for leaf in leaves)

    by_category = sorted(grouped.items(), key=lambda item: weight(item[1]), reverse=True)
    unit = width / 1300
    plot_width = width - round(56 * unit)
    plot_height = width - round(182 * unit)
    all_groups = squarify([weight(leaves) for _, leaves in by_category], 0, 0, plot_width, plot_height)
    groups: list[Group] = []
    tiles: list[Tile] = []
    names = Counter(leaf.name for leaf in data.leaves)
    for (category, leaves), (x, y, w, h) in zip(by_category, all_groups, strict=True):
        if w < 4 or h < 4:
            continue
        title_size = 26 * unit if w >= 74 * unit and h >= 64 * unit else 1
        groups.append(Group(x, y, w - 2, h - 2, category, title_size > 1))
        leaf_boxes = squarify([leaf.value for leaf in leaves], x + 2, y + title_size, w - 6, h - title_size - 4)
        for leaf, (lx, ly, lw, lh) in zip(leaves, leaf_boxes, strict=True):
            if lw <= 1 or lh <= 1:
                continue
            name = f"{leaf.name} · {leaf.code}" if names[leaf.name] > 1 and leaf.code else leaf.name
            fit = min(38 * unit, max(11 * unit, min(lw * 0.13, lh * 0.21)))
            tiles.append(Tile(lx, ly, lw - 1, lh - 1, name, percent_text(leaf.diff_val), _heat(leaf.diff_val), fit))
    layers = "完整成分" if data.layer == 1 else "行业代表成分"
    subtitle = f"{len(data.leaves)} 只标的 · {len(grouped)} 个行业 · {layers}"
    return TreemapPage(
        data.title,
        subtitle,
        source_footer(snap.provider),
        width,
        unit,
        plot_width,
        plot_height,
        groups,
        tiles,
        [(percent_text(change), _heat(change)) for change in (-10, -5, 0, 5, 10)],
    )


def treemap_html(page: TreemapPage) -> str:
    return render_template("treemap.html", page=page)


async def render_treemap(
    snap: BoardSnapshot,
    market: str,
    sector: str | None = None,
    layer: int = 2,
    *,
    width: int = 1200,
    scale: float = 1.5,
) -> str | bytes:
    page = prepare_treemap(snap, market, sector, layer, width=width)
    if isinstance(page, str):
        return page
    return await render_html(treemap_html(page), width=width, scale=scale, label=market)
