"""Geographic coordinate utilities: longitude handling, point extraction, regions.

Conventions
-----------
* The internal format always stores longitude in [-180, 180). Native GFS/GDAS
  grids use [0, 360); :func:`normalize_dataset_longitude` converts them.
* Every function here accepts user longitudes in either convention and
  normalizes them first.
* Latitude may run north-to-south (as in GFS/GDAS) or south-to-north; nothing
  here assumes an ordering.

Point extraction uses the **nearest grid point**, with longitude distance
measured on the circle so that points near the dateline pick the truly
nearest column. No interpolation is performed: returned values are actual
model grid values, and the distance to the chosen grid point is reported as
``distance_km``.
"""

from __future__ import annotations

from typing import Sequence, TypeVar

import numpy as np
import pandas as pd
import xarray as xr

from wind_data import schema

Longitude = TypeVar("Longitude", float, np.ndarray, xr.DataArray)

#: Mean Earth radius (km), IUGG value, used for great-circle distances.
EARTH_RADIUS_KM = 6371.0088

#: Name of the dimension added by :func:`extract_points`.
LOCATION_DIM = "location"


# ---------------------------------------------------------------------------
# Longitude conventions
# ---------------------------------------------------------------------------
def normalize_longitude(lon: Longitude) -> Longitude:
    """Map longitude(s) from any convention into [-180, 180).

    Works on scalars, NumPy arrays, and xarray DataArrays.

    Examples
    --------
    >>> normalize_longitude(288.75)
    -71.25
    >>> normalize_longitude(180.0)
    -180.0
    """
    return ((lon + 180.0) % 360.0) - 180.0


def longitude_difference(lon_a: Longitude, lon_b: Longitude) -> Longitude:
    """Signed shortest longitude difference ``a - b`` in degrees, in [-180, 180)."""
    return normalize_longitude(lon_a - lon_b)


def normalize_dataset_longitude(ds: xr.Dataset, lon_name: str = schema.LONGITUDE) -> xr.Dataset:
    """Return ``ds`` with its longitude coordinate in [-180, 180), sorted ascending.

    A global 0.25 degree grid running 0 ... 359.75 becomes -180 ... 179.75
    with no duplicate columns. If the coordinate is already normalized and
    sorted the dataset is returned unchanged.
    """
    lon = ds[lon_name]
    already_ok = bool((lon >= -180).all() and (lon < 180).all()) and bool(
        (np.diff(lon.values) > 0).all()
    )
    if already_ok:
        return ds
    return ds.assign_coords({lon_name: normalize_longitude(lon)}).sortby(lon_name)


# ---------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in km between points given in degrees.

    Inputs broadcast against each other (NumPy rules).
    """
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


# ---------------------------------------------------------------------------
# Nearest grid point
# ---------------------------------------------------------------------------
def _grid_spacing(values: np.ndarray) -> float:
    """Typical spacing of a 1-D coordinate (0 for a single value)."""
    return float(np.median(np.abs(np.diff(values)))) if values.size > 1 else 0.0


def nearest_grid_indices(
    grid_lat: np.ndarray,
    grid_lon: np.ndarray,
    lat: Sequence[float] | np.ndarray,
    lon: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Indices of the nearest grid row/column for each requested point.

    Latitude and longitude are searched independently, which is exact for a
    regular latitude-longitude grid. Longitude distance is measured on the
    circle, so a point at 179.9 E maps to the -180 column, not 179.75.

    Raises
    ------
    ValueError
        If a point lies outside the grid by more than half a grid spacing.
        This catches points that fall outside a regional subset instead of
        silently returning the nearest edge value.
    """
    grid_lat = np.asarray(grid_lat, dtype=float)
    grid_lon = normalize_longitude(np.asarray(grid_lon, dtype=float))
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    lon = normalize_longitude(np.atleast_1d(np.asarray(lon, dtype=float)))
    if lat.shape != lon.shape:
        raise ValueError(f"lat and lon must have the same length, got {lat.size} and {lon.size}")
    if np.any(np.abs(lat) > 90):
        raise ValueError("latitude must be within [-90, 90]")

    dlat = np.abs(grid_lat[:, None] - lat[None, :])
    dlon = np.abs(longitude_difference(grid_lon[:, None], lon[None, :]))
    ilat = dlat.argmin(axis=0)
    ilon = dlon.argmin(axis=0)

    # Half a grid cell plus a small tolerance for floating-point grid values.
    lat_tol = 0.5 * _grid_spacing(grid_lat) + 1e-6
    lon_tol = 0.5 * _grid_spacing(grid_lon) + 1e-6
    outside = (dlat[ilat, np.arange(lat.size)] > lat_tol) | (
        dlon[ilon, np.arange(lon.size)] > lon_tol
    )
    if outside.any():
        bad = [(float(a), float(o)) for a, o in zip(lat[outside], lon[outside])]
        raise ValueError(f"points outside the grid: {bad}")
    return ilat, ilon


def extract_points(
    ds: xr.Dataset,
    lat: float | Sequence[float],
    lon: float | Sequence[float],
    names: str | Sequence[str] | None = None,
) -> xr.Dataset:
    """Nearest-grid-point values at one or more locations.

    Parameters
    ----------
    ds
        Gridded internal-format dataset with ``latitude``/``longitude``
        dimension coordinates.
    lat, lon
        Requested location(s) in degrees. Longitude may use either convention.
    names
        Optional label per location (e.g. station IDs). Defaults to
        ``"p0"``, ``"p1"``, ...

    Returns
    -------
    xarray.Dataset
        ``ds`` with the ``latitude``/``longitude`` dimensions replaced by a
        ``location`` dimension. Along it:

        * ``latitude``/``longitude``: the **grid point** actually used
        * ``requested_latitude``/``requested_longitude``: what was asked for
        * ``distance_km``: great-circle distance between the two
    """
    lat_arr = np.atleast_1d(np.asarray(lat, dtype=float))
    lon_arr = normalize_longitude(np.atleast_1d(np.asarray(lon, dtype=float)))
    if names is None:
        names = [f"p{i}" for i in range(lat_arr.size)]
    names = [names] if isinstance(names, str) else list(names)
    if len(names) != lat_arr.size:
        raise ValueError(f"got {len(names)} names for {lat_arr.size} points")

    ilat, ilon = nearest_grid_indices(
        ds[schema.LATITUDE].values, ds[schema.LONGITUDE].values, lat_arr, lon_arr
    )
    out = ds.isel(
        {
            schema.LATITUDE: xr.DataArray(ilat, dims=LOCATION_DIM),
            schema.LONGITUDE: xr.DataArray(ilon, dims=LOCATION_DIM),
        }
    )
    grid_lat = out[schema.LATITUDE].values
    grid_lon = out[schema.LONGITUDE].values
    return out.assign_coords(
        {
            LOCATION_DIM: names,
            "requested_latitude": (LOCATION_DIM, lat_arr),
            "requested_longitude": (LOCATION_DIM, lon_arr),
            "distance_km": (LOCATION_DIM, haversine_km(lat_arr, lon_arr, grid_lat, grid_lon)),
        }
    )


# ---------------------------------------------------------------------------
# Regional subsetting
# ---------------------------------------------------------------------------
def subset_region(
    ds: xr.Dataset,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
) -> xr.Dataset:
    """Grid points inside a latitude-longitude box (bounds inclusive).

    Longitudes may use either convention. The box runs **eastward** from
    ``lon_min`` to ``lon_max``, so ``lon_min > lon_max`` (after
    normalization) means the box crosses the dateline, e.g. ``170, -170``
    selects 170 E through 190 E.

    For a dateline-crossing box the columns are returned in geographic
    (west-to-east) order, so the longitude coordinate jumps from 179.75 to
    -180 and is not monotonic. Arrays stay spatially contiguous, which is
    what maps and spatial statistics need; label-based ``.sel`` on longitude
    should be avoided on such a subset.

    Latitude order of the input (north-to-south or south-to-north) is kept.
    """
    if lat_min > lat_max:
        raise ValueError(f"lat_min ({lat_min}) must not exceed lat_max ({lat_max})")

    lats = ds[schema.LATITUDE].values
    ilat = np.flatnonzero((lats >= lat_min) & (lats <= lat_max))

    lons = ds[schema.LONGITUDE].values
    west, east = float(normalize_longitude(lon_min)), float(normalize_longitude(lon_max))
    if west <= east:
        ilon = np.flatnonzero((lons >= west) & (lons <= east))
    else:  # crosses the dateline: [west, 180) then [-180, east]
        ilon = np.concatenate([np.flatnonzero(lons >= west), np.flatnonzero(lons <= east)])

    if ilat.size == 0 or ilon.size == 0:
        raise ValueError(
            f"region lat [{lat_min}, {lat_max}], lon [{lon_min}, {lon_max}] contains no grid points"
        )
    return ds.isel({schema.LATITUDE: ilat, schema.LONGITUDE: ilon})


# ---------------------------------------------------------------------------
# Gridded -> tabular
# ---------------------------------------------------------------------------
def to_dataframe(ds: xr.Dataset) -> pd.DataFrame:
    """Convert a gridded or point dataset to the tabular internal format.

    One row per (time, grid point). The dataset's ``attrs["source"]`` becomes
    the ``source`` column. Extra coordinates such as ``init_time``,
    ``forecast_hour``, ``location`` and ``distance_km`` are kept as columns.

    A full global 0.25 degree field is about a million rows per time step;
    subset or extract points first.
    """
    source = ds.attrs.get("source")
    if source is None:
        raise ValueError("dataset has no 'source' attribute; was it produced by wind_data.download?")
    wanted = [v for v in (schema.U10, schema.V10, schema.WIND_SPEED, schema.WIND_DIRECTION) if v in ds]
    df = ds[wanted].to_dataframe().reset_index()
    df[schema.SOURCE] = source
    df = schema.order_columns(df)
    return schema.validate_wind_frame(df, require_derived=False)
