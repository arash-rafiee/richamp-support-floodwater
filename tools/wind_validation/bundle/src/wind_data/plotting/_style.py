"""Shared visual style for all figures (colors, axes styling, saving).

Colors follow a validated categorical palette (colorblind-safe for the first
three slots when all pairs are on screen). Each data source always gets the
same color, whatever else is plotted, so GFS is blue and GDAS orange in
every figure.

* Categorical: fixed slot order, never cycled.
* Sequential (magnitude, e.g. wind speed): one hue, light to dark.
* Diverging (signed differences): blue and red arms around a neutral gray
  midpoint, with symmetric limits so zero is always the midpoint.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure

# Categorical slots in fixed order.
CATEGORICAL: tuple[str, ...] = (
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
)
#: Fixed color per source: color follows the entity, never its position.
SOURCE_COLORS: dict[str, str] = {"gfs": CATEGORICAL[0], "gdas": CATEGORICAL[1]}

# Ink and chrome.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

LINE_WIDTH = 2.0
MARKER_SIZE = 5.0  # points; about 8 px on screen at 100 dpi
#: Markers carry a thin ring in the surface color so overlapping dots stay distinct.
MARKER_RING: dict = {"markersize": MARKER_SIZE + 1.5, "markeredgecolor": SURFACE, "markeredgewidth": 1.2}

#: Sequential ramp for wind speed (blue, light to dark).
SPEED_CMAP = LinearSegmentedColormap.from_list(
    "wind_speed",
    ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
)
#: Diverging ramp for model-minus-reference (blue = model low, red = model high).
DIFF_CMAP = LinearSegmentedColormap.from_list(
    "wind_diff",
    ["#104281", "#2a78d6", "#86b6ef", "#f0efec", "#f0a3a2", "#e34948", "#8e1f1f"],
)


def source_color(source: str, fallback_index: int = 0) -> str:
    """Color for a data source; unknown sources take categorical slots from 3 on."""
    return SOURCE_COLORS.get(source, CATEGORICAL[(2 + fallback_index) % len(CATEGORICAL)])


def new_axes(ax: Axes | None = None, figsize: tuple[float, float] = (9, 4)) -> tuple[Figure, Axes]:
    """Return ``(figure, axes)``, creating them if ``ax`` is None."""
    if ax is not None:
        return ax.figure, ax
    fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def style_axes(ax: Axes, *, grid_axis: str = "y") -> Axes:
    """Recessive axes: hairline solid grid, no top/right spines, muted ticks."""
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(colors=TEXT_MUTED, labelcolor=TEXT_SECONDARY, length=3)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color=GRID, linewidth=1.0, linestyle="-")
        ax.set_axisbelow(True)
    ax.xaxis.label.set_color(TEXT_SECONDARY)
    ax.yaxis.label.set_color(TEXT_SECONDARY)
    ax.title.set_color(TEXT_PRIMARY)
    return ax


def style_legend(ax: Axes) -> None:
    """Legend in text ink with no frame, only when there are two or more series."""
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) >= 2:
        leg = ax.legend(frameon=False, labelcolor=TEXT_SECONDARY)
        leg.set_zorder(5)


def save_figure(fig: Figure, path: str | Path, dpi: int = 150) -> Path:
    """Save ``fig`` to ``path`` (format from the suffix), creating parent folders."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor=fig.get_facecolor(), bbox_inches="tight")
    return path
