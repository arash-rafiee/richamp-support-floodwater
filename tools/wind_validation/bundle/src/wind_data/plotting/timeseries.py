"""Time-series figures for point wind data and lead-time statistics.

Inputs are tabular internal-format frames (see :mod:`wind_data.schema`),
usually from :func:`wind_data.processing.coordinates.to_dataframe` after
:func:`~wind_data.processing.coordinates.extract_points`. Several sources may
be passed together in one frame or as a list of frames; each source is drawn
in its fixed color.

Direction is drawn as dots only, never joined by lines: a line from 355 to 5
degrees would cross the whole axis although the wind barely changed.
"""

from __future__ import annotations

from typing import Sequence

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from wind_data import schema
from wind_data.plotting import _style
from wind_data.processing.coordinates import LOCATION_DIM

#: Series with at most this many points get a marker on every value.
_MARKER_THRESHOLD = 50

_LABELS = {
    schema.WIND_SPEED: "10-m wind speed (m s$^{-1}$)",
    schema.U10: "10-m u (eastward) (m s$^{-1}$)",
    schema.V10: "10-m v (northward) (m s$^{-1}$)",
    schema.WIND_DIRECTION: "10-m wind direction (from)",
}


def _combine(data: pd.DataFrame | Sequence[pd.DataFrame], location: str | None) -> pd.DataFrame:
    """One frame, one location, one value per (source, time)."""
    df = data if isinstance(data, pd.DataFrame) else pd.concat(list(data), ignore_index=True)
    schema.validate_wind_frame(df)
    if location is not None:
        if LOCATION_DIM not in df:
            raise ValueError("location given but data has no 'location' column")
        df = df[df[LOCATION_DIM] == location]
        if df.empty:
            raise ValueError(f"no rows for location {location!r}")
    points = df[[schema.LATITUDE, schema.LONGITUDE]].drop_duplicates()
    per_source_points = df.groupby(schema.SOURCE)[[schema.LATITUDE, schema.LONGITUDE]].nunique()
    if (per_source_points > 1).any().any():
        if LOCATION_DIM in df:
            hint = f"pass location=... (one of {sorted(df[LOCATION_DIM].unique())})"
        else:
            hint = "extract a single point first"
        raise ValueError(f"data holds {len(points)} grid points; {hint}")
    if df.duplicated(subset=[schema.SOURCE, schema.TIME]).any():
        raise ValueError(
            "more than one value per source and valid time (several runs?); "
            "select one run, or plot lead-time statistics instead"
        )
    # Known sources in schema order (GFS first, so the sparser GDAS analyses draw on top).
    order = {s: i for i, s in enumerate(schema.SOURCES)}
    rank = df[schema.SOURCE].map(order).fillna(len(order))
    return df.assign(_rank=rank).sort_values(["_rank", schema.TIME]).drop(columns="_rank")


def _format_time_axis(ax: Axes) -> None:
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.set_xlabel("Valid time (UTC)")


def _format_direction_axis(ax: Axes) -> None:
    ax.set_ylim(0, 360)
    ax.set_yticks([0, 90, 180, 270, 360])
    ax.set_yticklabels(["N 0°", "E 90°", "S 180°", "W 270°", "N 360°"])


def plot_timeseries(
    data: pd.DataFrame | Sequence[pd.DataFrame],
    variable: str = schema.WIND_SPEED,
    *,
    location: str | None = None,
    ax: Axes | None = None,
    title: str | None = None,
) -> tuple[Figure, Axes]:
    """Plot one variable against valid time, one series per source.

    Parameters
    ----------
    data
        Tabular frame(s) holding a single location.
    variable
        ``wind_speed`` (default), ``wind_direction``, ``u10`` or ``v10``.
    location
        Location name to select when the frame holds several.
    ax
        Axes to draw on; a new figure is created if None.
    title
        Figure title; defaults to the location and grid point.

    Returns
    -------
    (figure, axes)
    """
    if variable not in _LABELS:
        raise ValueError(f"variable must be one of {list(_LABELS)}, got {variable!r}")
    df = _combine(data, location)
    fig, ax = _style.new_axes(ax)

    for i, (source, grp) in enumerate(df.groupby(schema.SOURCE, sort=False)):
        color = _style.source_color(source, i)
        label = source.upper()
        if variable == schema.WIND_DIRECTION:
            ax.plot(grp[schema.TIME], grp[variable], linestyle="none", marker="o",
                    color=color, label=label, **_style.MARKER_RING)
        else:
            marker = "o" if len(grp) <= _MARKER_THRESHOLD else None
            ax.plot(grp[schema.TIME], grp[variable], color=color, linewidth=_style.LINE_WIDTH,
                    marker=marker, **_style.MARKER_RING, solid_capstyle="round",
                    solid_joinstyle="round", label=label)

    if variable == schema.WIND_DIRECTION:
        _format_direction_axis(ax)
    elif variable == schema.WIND_SPEED:
        ax.set_ylim(bottom=0)
    else:
        ax.axhline(0, color=_style.AXIS, linewidth=1.0, zorder=1)

    ax.set_ylabel(_LABELS[variable])
    _format_time_axis(ax)
    _style.style_axes(ax)
    _style.style_legend(ax)
    ax.set_title(title if title is not None else _default_title(df), loc="left", fontsize=11)
    return fig, ax


def plot_wind_panel(
    data: pd.DataFrame | Sequence[pd.DataFrame],
    *,
    location: str | None = None,
    title: str | None = None,
) -> tuple[Figure, tuple[Axes, Axes]]:
    """Speed (top) and direction (bottom) on a shared time axis."""
    df = _combine(data, location)
    fig, (ax_s, ax_d) = plt.subplots(
        2, 1, figsize=(9, 6), sharex=True, layout="constrained", height_ratios=(3, 2)
    )
    fig.patch.set_facecolor(_style.SURFACE)
    plot_timeseries(df, schema.WIND_SPEED, ax=ax_s, title=title if title is not None else _default_title(df))
    plot_timeseries(df, schema.WIND_DIRECTION, ax=ax_d, title="")
    if ax_d.get_legend() is not None:
        ax_d.get_legend().remove()  # one legend is enough
    ax_s.set_xlabel("")
    return fig, (ax_s, ax_d)


def plot_lead_time(
    summary: pd.DataFrame,
    metric: str = "speed_rmse",
    *,
    group: str | None = None,
    ax: Axes | None = None,
    title: str | None = None,
) -> tuple[Figure, Axes]:
    """Plot one verification statistic against forecast hour.

    Parameters
    ----------
    summary
        Output of :func:`wind_data.analysis.comparison.summarize` grouped by
        forecast hour (and optionally by ``group``).
    metric
        A column of ``summary``, e.g. ``speed_rmse``, ``vector_rmse``,
        ``speed_bias``, ``direction_mae``. One metric per axes, so the axis
        never mixes units; use separate axes for m/s and degrees.
    group
        Optional column (e.g. ``location``) to draw one line per value.
    """
    hour_col = next((c for c in ("forecast_hour_model", schema.FORECAST_HOUR) if c in summary), None)
    if hour_col is None:
        raise ValueError("summary has no forecast-hour column; summarize(pairs, by='forecast_hour')")
    if metric not in summary:
        raise ValueError(f"unknown metric {metric!r}; available: {[c for c in summary.columns if c != hour_col]}")

    fig, ax = _style.new_axes(ax, figsize=(7, 4))
    groups = summary.groupby(group, sort=True) if group else [(None, summary)]
    for i, (name, grp) in enumerate(groups):
        grp = grp.sort_values(hour_col)
        ax.plot(grp[hour_col], grp[metric], color=_style.CATEGORICAL[i % len(_style.CATEGORICAL)],
                linewidth=_style.LINE_WIDTH, marker="o", **_style.MARKER_RING,
                label=None if name is None else str(name))
    if metric.endswith("_bias"):
        ax.axhline(0, color=_style.AXIS, linewidth=1.0, zorder=1)
    elif not metric.endswith("_corr"):
        ax.set_ylim(bottom=0)

    unit = _metric_unit(metric)
    ax.set_ylabel(metric.replace("_", " ") + (f" ({unit})" if unit else ""))
    ax.set_xlabel("Forecast hour")
    max_hour = float(summary[hour_col].max())
    ax.xaxis.set_major_locator(MultipleLocator(6 if max_hour <= 48 else 24 if max_hour <= 240 else 48))
    _style.style_axes(ax)
    _style.style_legend(ax)
    if title is not None:
        ax.set_title(title, loc="left", fontsize=11)
    return fig, ax


def _metric_unit(metric: str) -> str:
    """Unit label for a statistic column from comparison.summarize."""
    if metric.startswith("direction") and not metric.endswith("_n"):
        return "degrees"
    if metric == "n" or metric.endswith(("_n", "_corr", "_si")):
        return ""
    return "m s$^{-1}$"


def _default_title(df: pd.DataFrame) -> str:
    lat = df[schema.LATITUDE].iloc[0]
    lon = df[schema.LONGITUDE].iloc[0]
    where = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
    if LOCATION_DIM in df and df[LOCATION_DIM].nunique() == 1:
        where = f"{df[LOCATION_DIM].iloc[0]} ({where})"
    return where
