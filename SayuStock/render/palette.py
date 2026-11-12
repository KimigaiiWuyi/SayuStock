"""SayuStock's colours: one dark theme for every page.

The theme is GitHub's dark palette (Primer): near-black surfaces, grey text and
a fixed set of accents. Direction is red up, green down (the A-share
convention; the US heatmap swaps them, as US sites do) and grey flat. Every
other red or green is derived from those two, lighter for text on a
direction-coloured card, darker for the cards themselves and for heat scales,
so no page brings its own.

Python pages import these names; templates read them as Jinja globals
(render/templates.py), theme.css included.
"""

from __future__ import annotations

from typing import Tuple


def rgb(color: str) -> Tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix(color: str, other: str, share: float) -> str:
    """``share`` of ``color`` over ``other``, e.g. ``mix(UP, PAGE, 0.45)`` for a dark red."""
    blended = (round(a * share + b * (1 - share)) for a, b in zip(rgb(color), rgb(other)))
    return "#{:02x}{:02x}{:02x}".format(*blended)


def rgba(color: str, alpha: float) -> str:
    """``#rrggbb`` at ``alpha``, e.g. ``rgba(UP, 0.2)`` for an area under a rising line."""
    red, green, blue = rgb(color)
    return f"rgba({red},{green},{blue},{alpha:.2f})"


# Surfaces, darkest last: the page gradient, then panels and cells.
PAGE_TOP, PAGE, PAGE_BOTTOM = "#10151d", "#0b0e13", "#090c10"
SURFACE = "#161b22"
# Lines: faint grid, grid, borders and neutral bars, dividers.
GRID_SOFT, GRID, BORDER, DIVIDER = "#1c2128", "#21262d", "#30363d", "#484f58"
# Text: headings and values, secondary values, labels, notes.
TEXT, TEXT_SOFT, MUTED, DIM = "#e6edf3", "#c9d1d9", "#8b949e", "#6e7681"
WHITE = "#ffffff"

# Direction.
UP, DOWN, FLAT = "#f6465d", "#2ebd85", "#aeb8c6"
# On a direction-coloured card: the same hues, lighter.
UP_TEXT, DOWN_TEXT = mix(UP, WHITE, 0.75), mix(DOWN, WHITE, 0.75)
# Direction-coloured cards and pills: the same hues, darker.
UP_CARD, DOWN_CARD, FLAT_CARD = mix(UP, PAGE, 0.45), mix(DOWN, PAGE, 0.45), mix(FLAT, PAGE, 0.3)
UP_CARD_END, DOWN_CARD_END, FLAT_CARD_END = mix(UP, PAGE, 0.15), mix(DOWN, PAGE, 0.15), mix(FLAT, PAGE, 0.1)
UP_PILL, DOWN_PILL, FLAT_PILL = mix(UP, PAGE, 0.8), mix(DOWN, PAGE, 0.72), mix(FLAT, PAGE, 0.35)

# Accents.
BLUE = "#58a6ff"  # the neutral highlight: indices, averages, finished phases
AMBER = "#d29922"  # reference lines: previous close, previous day
ORANGE = "#f0883e"  # what is ahead or happening now: forecasts, the current phase
YELLOW = "#e3b341"
VIOLET = "#a371f7"
TEAL = "#56d4dd"
PINK = "#db61a2"
GREEN = "#3fb950"
RED = "#ff7b72"
SKY = "#79c0ff"
LILAC = "#d2a8ff"
# Lines that only need telling apart (compare, multi-stock, valuation), in order.
SERIES = (BLUE, ORANGE, VIOLET, GREEN, RED, YELLOW, SKY, PINK, TEAL, "#ffa657", "#7ee787", LILAC, MUTED)

# Heat scales (cloud maps, heatmaps, flow rankings) run from the grey of a
# border to the direction colours.
HEAT_NEUTRAL = BORDER
HEAT_UP, HEAT_DOWN = mix(UP, PAGE, 0.9), mix(DOWN, PAGE, 0.8)
HEAT_UP_MID, HEAT_DOWN_MID = mix(UP, HEAT_NEUTRAL, 0.4), mix(DOWN, HEAT_NEUTRAL, 0.4)


def ramp(color: str, steps: int, fade: float = 0.7) -> Tuple[str, ...]:
    """``steps`` shades from ``color`` towards ``TEXT_SOFT``, for ranked lines."""
    if steps <= 1:
        return (color,)
    return tuple(mix(color, TEXT_SOFT, 1 - fade * index / (steps - 1)) for index in range(steps))


TOKENS = {name: value for name, value in globals().items() if name.isupper() and isinstance(value, (str, tuple))}
