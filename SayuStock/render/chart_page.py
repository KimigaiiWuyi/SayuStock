"""Presentation geometry for the plugin's Cartesian charts."""

from math import isfinite
from datetime import datetime
from dataclasses import field, dataclass
from collections.abc import Callable, Sequence

import pandas as pd

from . import render_html
from .charts import Scale, ticks, line_path
from .palette import AMBER
from .templates import render_template

WIDTH = 1240
PLOT_WIDTH = WIDTH - 56
LEFT = 77
RIGHT = PLOT_WIDTH - 77
TOP = 18


@dataclass(slots=True)
class Tick:
    pos: float
    label: str
    note: str = ""


@dataclass(slots=True)
class Stroke:
    path: str
    color: str
    width: float = 1.65
    dash: str = ""
    opacity: float = 1.0


@dataclass(slots=True)
class Fill:
    path: str
    color: str
    opacity: float


@dataclass(slots=True)
class Rectangle:
    x: float
    y: float
    width: float
    height: float
    color: str
    opacity: float = 1.0


@dataclass(slots=True)
class Rule:
    x1: float
    y1: float
    x2: float
    y2: float
    color: str = AMBER
    dash: str = "5 4"
    opacity: float = 0.75


@dataclass(slots=True)
class Dot:
    x: float
    y: float
    color: str
    radius: float = 3.0


@dataclass(slots=True)
class Mark:
    x: float
    y: float
    label: str
    color: str
    anchor: str = "start"


@dataclass(slots=True)
class Legend:
    label: str
    color: str
    value: str = ""
    detail: str = ""


@dataclass(slots=True)
class Panel:
    label: str
    height: int
    axis: list[Tick]
    x_ticks: list[Tick] = field(default_factory=list)
    right_axis: list[Tick] = field(default_factory=list)
    series: list[Stroke] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    rects: list[Rectangle] = field(default_factory=list)
    backdrop: list[Rectangle] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    dots: list[Dot] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    legend: list[Legend] = field(default_factory=list)
    empty: str = ""

    @property
    def bottom(self) -> int:
        return self.height - (50 if self.x_ticks else 17)


@dataclass(slots=True)
class ChartPage:
    title: str
    eyebrow: str
    subtitle: str
    panels: list[Panel]
    footer: str
    legend: list[Legend] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def page_html(page: ChartPage) -> str:
    return render_template(
        "chart_page.html", page=page, width=WIDTH, plot_width=PLOT_WIDTH, left=LEFT, right=RIGHT, top=TOP
    )


async def render_chart(page: ChartPage | str) -> bytes | str:
    if isinstance(page, str):
        return page
    return await render_html(page_html(page), width=WIDTH, label=page.eyebrow)


def frame_column(frame: pd.DataFrame, name: str) -> pd.Series:
    column = frame[name]
    assert isinstance(column, pd.Series)
    return column


def finite(value: object) -> float | None:
    if isinstance(value, (int, float)) and isfinite(value):
        return float(value)
    return None


def numbers(column: pd.Series) -> list[float | None]:
    return [finite(value) for value in column.tolist()]


def timestamps(column: pd.Series) -> list[datetime]:
    return [value.to_pydatetime() for value in column if isinstance(value, pd.Timestamp)]


def bounds(values: Sequence[float | None], *, minimum: float = 0.01) -> tuple[float, float]:
    actual = [value for value in values if value is not None]
    if not actual:
        return -minimum, minimum
    lo, hi = min(actual), max(actual)
    pad = max((hi - lo) * 0.09, minimum)
    return lo - pad, hi + pad


def panel_scale(
    panel: Panel, lo: float, hi: float, fmt: Callable[[float], str], *, count: int = 5, right: bool = False
) -> Scale:
    scale = Scale(lo, hi, panel.bottom, TOP)
    labels = [Tick(scale(value), fmt(value)) for value in ticks(lo, hi, count)]
    if right:
        panel.right_axis = labels
    else:
        panel.axis = labels
    return scale


def plot_x(count: int, *, bars: bool = False, right: int = RIGHT) -> list[float]:
    width = right - LEFT
    if bars:
        return [LEFT + width * (i + 0.5) / count for i in range(count)]
    if count == 1:
        return [LEFT]
    return [LEFT + width * i / max(count - 1, 1) for i in range(count)]


def sample_indices(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    return [round(i * (count - 1) / (maximum - 1)) for i in range(maximum)]


def date_ticks(dates: Sequence[datetime], xs: Sequence[float], *, intraday: bool = False) -> list[Tick]:
    n = len(dates)
    if not n:
        return []
    same_day = dates[0].date() == dates[-1].date()
    same_year = dates[0].year == dates[-1].year
    fmt = ("%H:%M" if same_day else "%m-%d %H:%M") if intraday else ("%m-%d" if same_year else "%Y-%m-%d")
    if intraday and not same_year:
        fmt = "%Y-%m-%d %H:%M"
    return [Tick(xs[i], dates[i].strftime(fmt)) for i in sample_indices(n, 7 if intraday else 8)]


def number_text(value: float | None, precision: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def percent_text(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%" if value else "0.00%"


def compact_number(value: float) -> str:
    if abs(value) >= 1e8:
        return f"{value / 1e8:g}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:g}万"
    return f"{value:g}"


def last_value(values: Sequence[float | None]) -> float | None:
    return next((value for value in reversed(values) if value is not None), None)


def add_series(
    panel: Panel,
    xs: Sequence[float],
    values: Sequence[float | None],
    scale: Scale,
    color: str,
    *,
    width: float = 1.65,
    dash: str = "",
    end_dot: bool = False,
) -> None:
    points = [scale(value) if value is not None else None for value in values]
    path = line_path(xs, points)
    if path:
        panel.series.append(Stroke(path, color, width, dash))
    if end_dot:
        for x, y in reversed(list(zip(xs, points))):
            if y is not None:
                panel.dots.append(Dot(x, y, color))
                break


def add_end_labels(panel: Panel, entries: list[Mark], *, gap: float = 20) -> None:
    """Distribute labels along the right rail without moving the data points."""
    if not entries:
        return
    entries.sort(key=lambda entry: entry.y)
    available = panel.bottom - TOP
    separation = min(gap, available / max(len(entries) - 1, 1))
    ys: list[float] = []
    for entry in entries:
        ys.append(max(entry.y, ys[-1] + separation if ys else TOP))
    ys[-1] = min(ys[-1], panel.bottom)
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - separation)
    for entry, y in zip(entries, ys):
        panel.rules.append(Rule(entry.x + 5, entry.y, RIGHT + 8, y, entry.color, "2 3", 0.5))
        panel.marks.append(Mark(RIGHT + 12, y + 4, entry.label, entry.color))
