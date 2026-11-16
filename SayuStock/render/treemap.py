"""Market-cap-weighted treemap matching the original Finviz/Plotly dark style."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from . import render_html
from .charts import squarify
from .palette import mix
from .templates import render_template
from .chart_page import percent_text
from ..utils.render_data import CloudmapLeaf, build_cloudmap_render_data
from ..utils.market.models import BoardSnapshot
from ..utils.market.display import source_footer

_CLOUD_NEUTRAL = "#3d3d3b"
_CLOUD_UP = "#ff0000"
_CLOUD_DOWN = "#00ff00"


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
    show_text: bool
    show_change: bool


@dataclass(slots=True)
class Group:
    x: float
    y: float
    w: float
    h: float
    name: str
    header_h: float
    header_font: float
    show_title: bool


@dataclass(slots=True)
class TreemapPage:
    title: str
    subtitle: str
    footer: str
    full_bleed: bool
    width: int
    unit: float
    plot_width: int
    plot_height: int
    groups: list[Group]
    tiles: list[Tile]


def _heat(change: float) -> str:
    shade = _CLOUD_UP if change > 0 else _CLOUD_DOWN
    return mix(shade, _CLOUD_NEUTRAL, min(abs(change) / 10, 1))


def _label_units(text: str) -> float:
    return sum(1.0 if ord(ch) > 127 else 0.62 for ch in text)


def _fit_tile_font(w: float, h: float, name: str, change: str, unit: float) -> tuple[float, bool, bool]:
    # Floor in CSS px keeps dense high-res maps (e.g. 2500px) readable without forcing oversized text
    min_font = max(2.6 * unit, 4.8)
    if w < min_font * 2.1 or h < min_font * 1.25:
        return min_font, False, False

    name_u = max(_label_units(name), 2.0)
    can_two_lines = h >= min_font * 2.25 and w >= min_font * 3.0
    if can_two_lines:
        chg_u = len(change) * 0.56 * 0.76
        fit_two = min((w * 0.78) / max(name_u, chg_u), (h * 0.72) / 1.98)
        if fit_two >= min_font * 0.85:
            return min(28 * unit, fit_two), True, True
        fit_tight = min((w * 0.90) / max(name_u, chg_u), (h * 0.84) / 1.98)
        if fit_tight >= min_font * 0.85:
            return min(28 * unit, fit_tight), True, True

    fit_one = min((w * 0.82) / name_u, (h * 0.76) / 1.15)
    if fit_one >= min_font * 0.85:
        return min(28 * unit, fit_one), True, False
    fit_one_tight = min((w * 0.92) / name_u, (h * 0.86) / 1.15)
    return min(28 * unit, max(min_font * 0.85, fit_one_tight)), True, False


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
    full_bleed = market == "大盘云图"
    unit = width / 1300
    outer_pad = round(4 * unit)
    plot_width = width - 2 * outer_pad
    plot_height = width - (2 * outer_pad if full_bleed else round(74 * unit))

    all_groups = squarify([weight(leaves) for _, leaves in by_category], 0, 0, plot_width, plot_height)
    groups: list[Group] = []
    tiles: list[Tile] = []
    names = Counter(leaf.name for leaf in data.leaves)
    group_gap = 2.2 * unit
    tile_pad = 2.0 * unit
    tile_gap = 1.0 * unit
    min_header_font = max(5.2 * unit, 8.0)

    for (category, leaves), (gx, gy, gw, gh) in zip(by_category, all_groups, strict=True):
        bw = gw - group_gap
        bh = gh - group_gap
        if bw < 4 or bh < 4:
            continue
        show_title = bw >= 26 * unit and bh >= 22 * unit
        header_h = min(24 * unit, max(11 * unit, bh * 0.13)) if show_title else 0.0
        header_font = min(
            14.5 * unit, max(min_header_font, min(header_h * 0.65, (bw * 0.92) / max(_label_units(category), 2.0)))
        )
        groups.append(Group(gx, gy, bw, bh, category, header_h, header_font, show_title))

        inner_x = gx + tile_pad
        inner_y = gy + header_h + (1.0 * unit if show_title else tile_pad)
        inner_w = max(bw - 2 * tile_pad, 1.0)
        inner_h = max(bh - (header_h + 1.0 * unit + tile_pad if show_title else 2 * tile_pad), 1.0)
        leaf_boxes = squarify([leaf.value for leaf in leaves], inner_x, inner_y, inner_w, inner_h)
        for leaf, (lx, ly, lw, lh) in zip(leaves, leaf_boxes, strict=True):
            tw = lw - tile_gap
            th = lh - tile_gap
            if tw <= 1 or th <= 1:
                continue
            name = f"{leaf.name} · {leaf.code}" if names[leaf.name] > 1 and leaf.code else leaf.name
            change = percent_text(leaf.diff_val)
            fit, show_text, show_change = _fit_tile_font(tw, th, name, change, unit)
            tiles.append(Tile(lx, ly, tw, th, name, change, _heat(leaf.diff_val), fit, show_text, show_change))

    layers = "完整成分" if data.layer == 1 else "行业代表成分"
    subtitle = f"{len(data.leaves)} 只标的 · {len(grouped)} 个行业 · {layers}"
    return TreemapPage(
        data.title,
        subtitle,
        "" if full_bleed else source_footer(snap.provider),
        full_bleed,
        width,
        unit,
        plot_width,
        plot_height,
        groups,
        tiles,
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
    return await render_html(treemap_html(page), width=page.width, scale=scale, label=page.title)
