"""云图（大盘 / 行业 / 概念）矩形树图。"""

from .chart_base import (
    BG_COLOR,
    FG_COLOR,
    JsonDict,
    Sequence,
    Rectangle,
    DrawResult,
    np,
    plt,
    _as_float,
    _setup_mpl,
    _fig_to_image,
    _draw_in_thread,
)
from .render_data import build_cloudmap_render_data


async def to_fig(raw_data: JsonDict, market: str, sector: str | None = None, layer: int = 2) -> DrawResult:
    return await _draw_in_thread(draw_cloudmap_chart, raw_data, market, sector, layer)


def _color_for_diff(diff: float) -> tuple[float, float, float]:
    clipped = max(-10.0, min(10.0, diff))
    base = np.array([61, 61, 59], dtype=float)
    target = np.array([255, 0, 0], dtype=float) if clipped >= 0 else np.array([0, 210, 80], dtype=float)
    ratio = abs(clipped) / 10.0
    rgb = (base * (1 - ratio) + target * ratio) / 255.0
    return float(rgb[0]), float(rgb[1]), float(rgb[2])


def _split_rect(
    items: Sequence[JsonDict], x: float, y: float, w: float, h: float
) -> list[tuple[JsonDict, float, float, float, float]]:
    if not items:
        return []
    if len(items) == 1:
        return [(items[0], x, y, w, h)]

    total = sum(max(_as_float(item["value"]), 0.0) for item in items)
    if total <= 0:
        total = float(len(items))
        items = [dict(item, value=1.0) for item in items]

    half = total / 2.0
    acc = 0.0
    split_index = 0
    for index, item in enumerate(items):
        value = max(_as_float(item["value"]), 0.0)
        if index > 0 and acc + value > half:
            break
        acc += value
        split_index = index + 1
    split_index = max(1, min(split_index, len(items) - 1))
    first = items[:split_index]
    second = items[split_index:]
    first_sum = sum(max(_as_float(item["value"]), 0.0) for item in first)
    ratio = first_sum / total if total else 0.5
    if w >= h:
        first_w = w * ratio
        return _split_rect(first, x, y, first_w, h) + _split_rect(second, x + first_w, y, w - first_w, h)
    first_h = h * ratio
    return _split_rect(first, x, y, w, first_h) + _split_rect(second, x, y + first_h, w, h - first_h)


def draw_cloudmap_chart(raw_data: JsonDict, market: str, sector: str | None = None, layer: int = 2) -> DrawResult:
    _setup_mpl()
    data = build_cloudmap_render_data(raw_data, market, sector, layer)
    if isinstance(data, str):
        return data
    cloudmap = data

    fig = plt.figure(figsize=(18, 18))
    ax = fig.add_subplot(1, 1, 1)
    fig.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    items: list[JsonDict] = cloudmap.df.to_dict("records")
    for item, x, y, w, h in _split_rect(items, 0.0, 0.0, 1.0, 1.0):
        pad = 0.0025
        rx = x + pad
        ry = y + pad
        rw = max(w - pad * 2, 0)
        rh = max(h - pad * 2, 0)
        diff_val = _as_float(item["diff_val"])
        ax.add_patch(
            Rectangle((rx, ry), rw, rh, facecolor=_color_for_diff(diff_val), edgecolor=BG_COLOR, linewidth=1.0)
        )
        area = rw * rh
        if area <= 0.002:
            continue
        fontsize = max(7, min(24, int(7 + area * 120)))
        custom_info = f"+{diff_val}%" if diff_val >= 0 else f"{diff_val}%"
        label = f"{item['name']}\n{custom_info}"
        if layer != 1 and area > 0.012:
            label = f"{item['category']}\n{label}"
        ax.text(
            rx + rw / 2,
            ry + rh / 2,
            label,
            ha="center",
            va="center",
            color="white",
            fontsize=fontsize,
            fontweight="bold",
            clip_on=True,
        )

    ax.set_title(cloudmap.title, color=FG_COLOR, fontsize=28, fontweight="bold", pad=18)
    fig.text(0.01, 0.01, "数据来源：东方财富 | SayuStock", color=FG_COLOR, fontsize=9, alpha=0.65)
    return _fig_to_image(fig, dpi=220)
