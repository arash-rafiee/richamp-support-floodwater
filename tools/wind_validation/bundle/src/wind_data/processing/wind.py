"""Wind speed, direction, and component conversions.

All functions are source-agnostic and vectorized. They accept and return
NumPy arrays, pandas Series, or xarray DataArrays interchangeably; the
output type follows the input type.

Conventions (see README.md)
---------------------------
* ``u`` is the eastward component and ``v`` the northward component, in m/s.
* Direction is the *meteorological* direction: the compass direction the
  wind blows **from**, in degrees clockwise from north, in [0, 360).
  0 = from the north, 90 = from the east, 180 = from the south,
  270 = from the west.
* Calm wind has no defined direction and is returned as ``NaN``.

The direction formula is ``(270 - atan2(v, u) * 180/pi) mod 360``. Note the
argument order ``atan2(v, u)``: ``atan2`` gives the mathematical angle of the
vector (counter-clockwise from east, the direction the wind blows *to*), and
``270 - angle`` converts that to the meteorological convention.
"""

from __future__ import annotations

from typing import TypeVar

import numpy as np
import pandas as pd
import xarray as xr

from wind_data import schema

ArrayLike = TypeVar("ArrayLike", np.ndarray, pd.Series, xr.DataArray)
Table = TypeVar("Table", pd.DataFrame, xr.Dataset)


def wind_speed(u: ArrayLike, v: ArrayLike) -> ArrayLike:
    """Wind speed ``sqrt(u**2 + v**2)``.

    Parameters
    ----------
    u, v
        Eastward and northward components (m/s), same shape.

    Returns
    -------
    Speed in the same units as the inputs, same type as the inputs.
    """
    return np.hypot(u, v)


def wind_direction(u: ArrayLike, v: ArrayLike, *, calm_threshold: float = 0.0) -> ArrayLike:
    """Meteorological wind direction (degrees the wind blows *from*).

    Parameters
    ----------
    u, v
        Eastward and northward components (m/s), same shape.
    calm_threshold
        Speeds at or below this value (m/s) are treated as calm and get
        ``NaN`` direction. The default ``0.0`` marks only exactly-zero wind
        as calm. Some analyses use a small positive threshold (e.g. 0.5 m/s)
        because direction is numerically meaningless for very light wind.

    Returns
    -------
    Direction in degrees, in [0, 360), with ``NaN`` where calm.
    """
    direction = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    speed = wind_speed(u, v)
    return _mask_calm(direction, speed, calm_threshold)


def wind_components(speed: ArrayLike, direction: ArrayLike) -> tuple[ArrayLike, ArrayLike]:
    """Inverse of :func:`wind_speed`/:func:`wind_direction`.

    Parameters
    ----------
    speed
        Wind speed (m/s).
    direction
        Meteorological direction in degrees (direction the wind blows from).

    Returns
    -------
    (u, v)
        Eastward and northward components (m/s). ``NaN`` direction (calm)
        yields ``NaN`` components; callers may prefer to fill with 0.
    """
    rad = np.radians(direction)
    u = -speed * np.sin(rad)
    v = -speed * np.cos(rad)
    return u, v


def direction_difference(direction_a: ArrayLike, direction_b: ArrayLike) -> ArrayLike:
    """Signed smallest angular difference ``a - b`` in degrees, in [-180, 180).

    Handles wrap-around correctly: ``direction_difference(350, 10) == -20``,
    not ``340``. Positive means ``a`` is clockwise of ``b``.
    """
    return (direction_a - direction_b + 180.0) % 360.0 - 180.0


def add_speed_and_direction(data: Table, *, calm_threshold: float = 0.0) -> Table:
    """Add ``wind_speed`` and ``wind_direction`` to a standardized table.

    Works for both the tabular (:class:`pandas.DataFrame`) and gridded
    (:class:`xarray.Dataset`) forms of the internal format, reading the
    ``u10`` and ``v10`` variables named in :mod:`wind_data.schema`.

    Parameters
    ----------
    data
        Table containing ``u10`` and ``v10``. It is not modified.
    calm_threshold
        Passed to :func:`wind_direction`.

    Returns
    -------
    A copy of ``data`` with the two derived variables added (or replaced).
    """
    out = data.copy()
    out[schema.WIND_SPEED] = wind_speed(out[schema.U10], out[schema.V10])
    out[schema.WIND_DIRECTION] = wind_direction(
        out[schema.U10], out[schema.V10], calm_threshold=calm_threshold
    )
    if isinstance(out, xr.Dataset):
        out[schema.WIND_SPEED].attrs.update(units="m s-1", long_name="10 m wind speed")
        out[schema.WIND_DIRECTION].attrs.update(
            units="degrees",
            long_name="10 m wind direction (meteorological, from)",
        )
    return out


def _mask_calm(direction: ArrayLike, speed: ArrayLike, threshold: float) -> ArrayLike:
    """Set ``direction`` to NaN where ``speed <= threshold``, preserving type."""
    keep = speed > threshold
    if hasattr(direction, "where"):  # pandas Series and xarray DataArray
        return direction.where(keep)
    return np.where(keep, direction, np.nan)
