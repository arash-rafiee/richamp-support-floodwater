"""Definition of the common internal wind-data format.

Every download module converts its source into this format, so processing,
analysis, and plotting code never needs to know whether data came from GFS
or GDAS.

Point and regional extractions are :class:`pandas.DataFrame` objects with one
row per (time, latitude, longitude). The columns, their units, and the
conventions behind them are listed in ``UNITS`` and documented in README.md.

Time convention
    ``time`` is the valid time, stored as a *naive* ``datetime64`` that is
    understood to be UTC. Naive rather than timezone-aware is used because
    xarray/cfgrib produce naive ``datetime64[ns]`` and timezone-aware
    columns do not round-trip cleanly through xarray or NetCDF.
Longitude convention
    ``longitude`` is always in the range [-180, 180).
Wind convention
    ``u10``/``v10`` are eastward/northward components in m/s;
    ``wind_direction`` is the meteorological direction the wind blows
    *from*, in degrees clockwise from north; calm wind has ``NaN`` direction.
"""

from __future__ import annotations

from typing import Final

import pandas as pd

# ---------------------------------------------------------------------------
# Column names (use these constants instead of string literals elsewhere)
# ---------------------------------------------------------------------------
TIME: Final = "time"
LATITUDE: Final = "latitude"
LONGITUDE: Final = "longitude"
U10: Final = "u10"
V10: Final = "v10"
WIND_SPEED: Final = "wind_speed"
WIND_DIRECTION: Final = "wind_direction"
SOURCE: Final = "source"

# Optional provenance columns kept when the source provides them.
INIT_TIME: Final = "init_time"
FORECAST_HOUR: Final = "forecast_hour"

# Columns every standardized frame must have, before derived quantities.
BASE_COLUMNS: Final[tuple[str, ...]] = (TIME, LATITUDE, LONGITUDE, U10, V10, SOURCE)
# Columns computed from u10/v10 by wind_data.processing.wind.
DERIVED_COLUMNS: Final[tuple[str, ...]] = (WIND_SPEED, WIND_DIRECTION)
OPTIONAL_COLUMNS: Final[tuple[str, ...]] = (INIT_TIME, FORECAST_HOUR)

# Canonical output order.
COLUMN_ORDER: Final[tuple[str, ...]] = (
    TIME,
    INIT_TIME,
    FORECAST_HOUR,
    LATITUDE,
    LONGITUDE,
    U10,
    V10,
    WIND_SPEED,
    WIND_DIRECTION,
    SOURCE,
)

UNITS: Final[dict[str, str]] = {
    TIME: "UTC (naive datetime64)",
    INIT_TIME: "UTC (naive datetime64)",
    FORECAST_HOUR: "hours",
    LATITUDE: "degrees_north",
    LONGITUDE: "degrees_east, [-180, 180)",
    U10: "m s-1 (positive eastward)",
    V10: "m s-1 (positive northward)",
    WIND_SPEED: "m s-1",
    WIND_DIRECTION: "degrees, meteorological (direction wind blows from)",
    SOURCE: "str",
}

# Known data sources. Extend this tuple when a new source module is added.
SOURCES: Final[tuple[str, ...]] = ("gfs", "gdas")


def validate_wind_frame(df: pd.DataFrame, *, require_derived: bool = True) -> pd.DataFrame:
    """Check that ``df`` conforms to the internal wind-data format.

    Parameters
    ----------
    df
        Frame to check.
    require_derived
        If True (default), ``wind_speed`` and ``wind_direction`` must be
        present. Pass False to validate a frame that has not yet been through
        :func:`wind_data.processing.wind.add_speed_and_direction`.

    Returns
    -------
    pandas.DataFrame
        The same frame, unchanged, so the call can be used inline.

    Raises
    ------
    ValueError
        Listing every problem found, so a caller can fix them all at once.
    """
    required = BASE_COLUMNS + (DERIVED_COLUMNS if require_derived else ())
    problems: list[str] = []

    missing = [c for c in required if c not in df.columns]
    if missing:
        problems.append(f"missing columns: {missing}")

    if TIME in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df[TIME]):
            problems.append(f"'{TIME}' must be datetime64, got {df[TIME].dtype}")
        elif getattr(df[TIME].dt, "tz", None) is not None and str(df[TIME].dt.tz) != "UTC":
            problems.append(f"'{TIME}' is timezone-aware but not UTC ({df[TIME].dt.tz})")

    if LATITUDE in df.columns and not df[LATITUDE].between(-90, 90).all():
        problems.append(f"'{LATITUDE}' outside [-90, 90]")

    if LONGITUDE in df.columns and not df[LONGITUDE].between(-180, 180, inclusive="left").all():
        problems.append(f"'{LONGITUDE}' outside [-180, 180); normalize with processing.coordinates")

    if SOURCE in df.columns:
        unknown = sorted(set(df[SOURCE].dropna().unique()) - set(SOURCES))
        if unknown:
            problems.append(f"unknown '{SOURCE}' values {unknown}; expected one of {SOURCES}")

    if problems:
        raise ValueError("Invalid wind data frame:\n- " + "\n- ".join(problems))
    return df


def order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` with known columns in canonical order, extras appended.

    Purely cosmetic; useful before writing CSV/Parquet so files from different
    sources have identical layouts.
    """
    known = [c for c in COLUMN_ORDER if c in df.columns]
    extra = [c for c in df.columns if c not in COLUMN_ORDER]
    return df[known + extra]
