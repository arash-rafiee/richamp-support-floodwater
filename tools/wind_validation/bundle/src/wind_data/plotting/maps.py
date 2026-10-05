"""Spatial wind maps.

Two map types:

* :func:`plot_wind_map`: wind speed as color (sequential ramp) with arrows
  showing the u/v vector. Arrows point the way the wind blows **to**,
  which is the standard vector convention; the meteorological direction
  (where it comes *from*) is the opposite way.
* :func:`plot_difference_map`: a model-minus-reference field from
  :func:`wind_data.analysis.comparison.difference_field` on a diverging
  ramp with symmetric limits, so zero is always the neutral midpoint.

Cells are drawn centered on grid points (``shading="nearest"``) because
GRIB values are point values at the grid nodes.

Coastlines need the optional ``cartopy`` package (``pip install -e ".[maps]"``).
Without it, maps are drawn on plain longitude-latitude axes with the aspect
ratio corrected for the latitude of the map center.

Dateline-crossing subsets from
:func:`~wind_data.processing.coordinates.subset_region` are handled by
unwrapping longitude for drawing; tick labels still show [-180, 180) values.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator

from wind_data import schema
from wind_data.plotting import _style

#: Aim for about this many arrows along the longer map side.
_ARROWS_ALONG_LONG_SIDE = 25


def _select_time(ds: xr.Dataset, time) -> xr.Dataset:
    """Reduce to one valid time: the given one, or the only one."""
    if schema.TIME not in ds.dims:
        return ds
    if time is None:
        if ds.sizes[schema.TIME] != 1:
            raise ValueError(f"dataset has {ds.sizes[schema.TIME]} times; pass time=...")
        return ds.isel({schema.TIME: 0})
    return ds.sel({schema.TIME: np.datetime64(time, "ns")})


def _unwrapped_longitude(lon: np.ndarray) -> np.ndarray:
    """Make longitude increase monotonically across the dateline for drawing."""
    lon = np.asarray(lon, dtype=float)
    return np.degrees(np.unwrap(np.radians(lon)))


def _lon_label(x: float, _pos=None) -> str:
    x = ((x + 180.0) % 360.0) - 180.0
    return f"{abs(x):g}°{'E' if x > 0 else 'W' if x < 0 else ''}"


def _lat_label(y: float, _pos=None) -> str:
    return f"{abs(y):g}°{'N' if y > 0 else 'S' if y < 0 else ''}"


def _map_axes(ax: Axes | None, lon: np.ndarray, lat: np.ndarray, coastlines: bool) -> tuple[Figure, Axes, dict]:
    """Create or prepare the map axes. Returns (fig, ax, plot kwargs)."""
    width = float(lon.max() - lon.min()) or 1.0
    height = float(lat.max() - lat.min()) or 1.0
    figsize = (9, max(3.5, min(8.0, 9 * height / width * 1.1)))

    if coastlines:
        try:
            import cartopy.crs as ccrs
            import cartopy.feature as cfeature
        except ImportError as err:
            raise ImportError("coastlines=True needs cartopy: pip install -e \".[maps]\"") from err
        if ax is None:
            center = float((lon.min() + lon.max()) / 2)
            fig, ax = plt.subplots(
                figsize=figsize, layout="constrained",
                subplot_kw={"projection": ccrs.PlateCarree(central_longitude=center)},
            )
            fig.patch.set_facecolor(_style.SURFACE)
        else:
            fig = ax.figure
        ax.set_extent([lon.min(), lon.max(), lat.min(), lat.max()], crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.COASTLINE, linewidth=0.6, edgecolor=_style.TEXT_SECONDARY, zorder=3)
        ax.add_feature(cfeature.BORDERS, linewidth=0.4, edgecolor=_style.TEXT_MUTED, zorder=3)
        gl = ax.gridlines(draw_labels=True, color=_style.GRID, linewidth=1.0)
        # Nice tick values from the (possibly unwrapped) range, expressed in [-180, 180)
        # because that is the range cartopy's gridliner works in.
        nice = MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10])
        lon_ticks = [t for t in nice.tick_values(lon.min(), lon.max()) if lon.min() <= t <= lon.max()]
        gl.xlocator = FixedLocator([((t + 180.0) % 360.0) - 180.0 for t in lon_ticks])
        gl.ylocator = nice
        gl.top_labels = gl.right_labels = False
        gl.xlabel_style = gl.ylabel_style = {"color": _style.TEXT_SECONDARY, "size": 9}
        return fig, ax, {"transform": ccrs.PlateCarree()}

    fig, ax = _style.new_axes(ax, figsize=figsize)
    ax.set_xlim(lon.min(), lon.max())
    ax.set_ylim(lat.min(), lat.max())
    ax.set_aspect(1.0 / np.cos(np.radians(float(np.mean(lat)))))
    ax.xaxis.set_major_formatter(FuncFormatter(_lon_label))
    ax.yaxis.set_major_formatter(FuncFormatter(_lat_label))
    _style.style_axes(ax, grid_axis="")
    return fig, ax, {}


def _draw_arrows(ax: Axes, lon: np.ndarray, lat: np.ndarray, u: np.ndarray, v: np.ndarray,
                 stride: int | None, key_speed: float, extra: dict) -> None:
    if stride is None:
        stride = max(1, int(np.ceil(max(lon.size, lat.size) / _ARROWS_ALONG_LONG_SIDE)))
    q = ax.quiver(lon[::stride], lat[::stride], u[::stride, ::stride], v[::stride, ::stride],
                  color=_style.TEXT_PRIMARY, width=0.0022, headwidth=4, zorder=4, **extra)
    ax.quiverkey(q, 0.90, 1.03, key_speed, f"{key_speed:g} m s$^{{-1}}$", labelpos="W",
                 coordinates="axes", color=_style.TEXT_PRIMARY,
                 fontproperties={"size": 9})


def plot_wind_map(
    ds: xr.Dataset,
    *,
    time=None,
    ax: Axes | None = None,
    arrows: bool = True,
    arrow_stride: int | None = None,
    arrow_key_speed: float = 10.0,
    vmax: float | None = None,
    coastlines: bool = False,
    title: str | None = None,
) -> tuple[Figure, Axes]:
    """Map of 10-m wind speed with optional wind-vector arrows.

    Parameters
    ----------
    ds
        Gridded internal-format dataset (subset the region first).
    time
        Valid time to show; required if ``ds`` has more than one.
    arrows, arrow_stride, arrow_key_speed
        Draw u/v arrows every ``arrow_stride`` grid points (automatic if
        None) with a reference arrow of ``arrow_key_speed`` m/s.
    vmax
        Upper color limit in m/s; defaults to the field's 99th percentile.
        Fix it when comparing several maps so they share a scale.
    coastlines
        Draw coastlines and borders (needs cartopy).
    title
        Defaults to source and valid time.
    """
    field = _select_time(ds, time)
    lon = _unwrapped_longitude(field[schema.LONGITUDE].values)
    lat = field[schema.LATITUDE].values
    speed = field[schema.WIND_SPEED].values

    fig, ax, extra = _map_axes(ax, lon, lat, coastlines)
    vmax = vmax if vmax is not None else float(np.nanpercentile(speed, 99))
    mesh = ax.pcolormesh(lon, lat, speed, cmap=_style.SPEED_CMAP, vmin=0, vmax=vmax,
                         shading="nearest", **extra)
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.85, pad=0.02, extend="max")
    cbar.set_label("10-m wind speed (m s$^{-1}$)", color=_style.TEXT_SECONDARY)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=_style.TEXT_MUTED, labelcolor=_style.TEXT_SECONDARY)

    if arrows:
        _draw_arrows(ax, lon, lat, field[schema.U10].values, field[schema.V10].values,
                     arrow_stride, arrow_key_speed, extra)

    ax.set_title(title if title is not None else _default_title(field), loc="left", fontsize=11,
                 color=_style.TEXT_PRIMARY)
    return fig, ax


def plot_difference_map(
    diff: xr.Dataset,
    variable: str = "wind_speed_diff",
    *,
    time=None,
    ax: Axes | None = None,
    limit: float | None = None,
    coastlines: bool = False,
    title: str | None = None,
) -> tuple[Figure, Axes]:
    """Map of a model-minus-reference field on a diverging ramp.

    Parameters
    ----------
    diff
        Output of :func:`wind_data.analysis.comparison.difference_field`.
    variable
        ``wind_speed_diff`` (default), ``u10_diff``, ``v10_diff``,
        ``wind_direction_diff`` or ``vector_error``. ``vector_error`` is a
        magnitude, so it uses the sequential ramp from zero.
    limit
        Symmetric color limit; defaults to the 99th percentile of ``|diff|``.
    """
    if variable not in diff:
        raise ValueError(f"unknown variable {variable!r}; available: {list(diff.data_vars)}")
    field = _select_time(diff, time)
    lon = _unwrapped_longitude(field[schema.LONGITUDE].values)
    lat = field[schema.LATITUDE].values
    values = field[variable].values

    fig, ax, extra = _map_axes(ax, lon, lat, coastlines)
    limit = limit if limit is not None else float(np.nanpercentile(np.abs(values), 99)) or 1.0
    if variable == "vector_error":
        cmap, vmin, vmax = _style.SPEED_CMAP, 0.0, limit
    else:
        cmap, vmin, vmax = _style.DIFF_CMAP, -limit, limit
    mesh = ax.pcolormesh(lon, lat, values, cmap=cmap, vmin=vmin, vmax=vmax, shading="nearest", **extra)

    unit = "degrees" if variable == "wind_direction_diff" else "m s$^{-1}$"
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.85, pad=0.02, extend="both" if vmin < 0 else "max")
    cbar.set_label(f"{variable.replace('_', ' ')} ({unit})", color=_style.TEXT_SECONDARY)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=_style.TEXT_MUTED, labelcolor=_style.TEXT_SECONDARY)

    if title is None:
        model = diff.attrs.get("model_source", "model").upper()
        ref = diff.attrs.get("reference_source", "reference").upper()
        title = f"{model} minus {ref}, {_valid_time_label(field)}"
    ax.set_title(title, loc="left", fontsize=11, color=_style.TEXT_PRIMARY)
    return fig, ax


def _valid_time_label(field: xr.Dataset) -> str:
    if schema.TIME not in field.coords:
        return ""
    t = np.datetime_as_string(field[schema.TIME].values, unit="m").replace("T", " ")
    label = f"valid {t} UTC"
    if schema.FORECAST_HOUR in field.coords:
        label += f" (f{int(field[schema.FORECAST_HOUR].values):03d})"
    return label


def _default_title(field: xr.Dataset) -> str:
    return f"{field.attrs.get('source', '').upper()} 10-m wind, {_valid_time_label(field)}".strip(", ")
