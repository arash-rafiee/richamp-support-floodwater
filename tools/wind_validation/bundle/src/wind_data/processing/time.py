"""Time handling: UTC parsing, model cycles, forecast hours, valid times.

Conventions
-----------
* All times are UTC. Values are stored as *naive* ``datetime64`` /
  :class:`pandas.Timestamp` objects understood to be UTC (see
  :mod:`wind_data.schema` for why they are not timezone-aware).
* Naive inputs are assumed to already be UTC; timezone-aware inputs are
  converted to UTC.
* GFS and GDAS both initialize four cycles per day at 00, 06, 12, 18 UTC.
* ``valid_time = init_time + forecast_hour``. The internal ``time`` axis is
  always the valid time.

This module is source-agnostic. Functions that need the set of available
forecast hours take it as an argument (e.g. ``gfs.SPEC.forecast_hours``) so
that this module never imports the download layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Iterable, TypeVar

import numpy as np
import pandas as pd
import xarray as xr

from wind_data import schema

#: Initialization (cycle) hours, UTC, for both GFS and GDAS.
CYCLES: Final[tuple[int, ...]] = (0, 6, 12, 18)
#: Hours between consecutive cycles.
CYCLE_INTERVAL_HOURS: Final = 6

TimeLike = str | datetime | np.datetime64 | pd.Timestamp
Table = TypeVar("Table", pd.DataFrame, xr.Dataset)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def to_utc_naive(value: TimeLike) -> pd.Timestamp:
    """Parse ``value`` into a naive UTC :class:`pandas.Timestamp`.

    Naive inputs are assumed to already be UTC. Timezone-aware inputs are
    converted to UTC and then stripped of their timezone.

    Examples
    --------
    >>> to_utc_naive("2024-01-15T06:00")
    Timestamp('2024-01-15 06:00:00')
    >>> to_utc_naive("2024-01-15T01:00-05:00")
    Timestamp('2024-01-15 06:00:00')
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def series_to_utc_naive(times: pd.Series) -> pd.Series:
    """Vectorized :func:`to_utc_naive` for a datetime column."""
    times = pd.to_datetime(times)
    if times.dt.tz is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)
    return times


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------
def is_cycle(value: TimeLike) -> bool:
    """True if ``value`` is exactly on a 00/06/12/18 UTC cycle hour."""
    ts = to_utc_naive(value)
    return ts.hour in CYCLES and ts == ts.floor("h")


def validate_cycle(init_time: TimeLike) -> pd.Timestamp:
    """Parse ``init_time`` and check it falls on a model cycle (00/06/12/18 UTC).

    Raises
    ------
    ValueError
        If the hour is not a cycle hour or minutes/seconds are non-zero.
    """
    ts = to_utc_naive(init_time)
    if not is_cycle(ts):
        raise ValueError(
            f"init_time {ts} is not a model cycle; cycles run at {CYCLES} UTC on the hour"
        )
    return ts


def floor_cycle(value: TimeLike) -> pd.Timestamp:
    """Latest cycle at or before ``value``. ``2024-01-15 10:30`` -> ``06:00``."""
    return to_utc_naive(value).floor(f"{CYCLE_INTERVAL_HOURS}h")


def ceil_cycle(value: TimeLike) -> pd.Timestamp:
    """Earliest cycle at or after ``value``. ``2024-01-15 10:30`` -> ``12:00``."""
    return to_utc_naive(value).ceil(f"{CYCLE_INTERVAL_HOURS}h")


def cycle_range(start: TimeLike, end: TimeLike) -> pd.DatetimeIndex:
    """All cycles within ``[start, end]``, inclusive, in order.

    ``start``/``end`` need not be on cycle hours; the range is snapped
    inward to the cycles it contains. Dates alone mean 00 UTC, so
    ``cycle_range("2024-01-15", "2024-01-16")`` gives five cycles:
    15th 00, 06, 12, 18 and 16th 00.
    """
    first, last = ceil_cycle(start), floor_cycle(end)
    if first > last:
        return pd.DatetimeIndex([])
    return pd.date_range(first, last, freq=f"{CYCLE_INTERVAL_HOURS}h")


def latest_cycle(now: TimeLike | None = None, delay_hours: float = 6.0) -> pd.Timestamp:
    """Most recent cycle expected to be published by ``now``.

    NCEP output appears several hours after the nominal cycle time: early
    GFS forecast hours after roughly 3.5-4 h, the GDAS final analysis after
    roughly 6 h. ``delay_hours`` is therefore an *estimate*; the download
    layer raises ``FileNotFoundError`` if a file is not there yet.

    Parameters
    ----------
    now
        Reference time; defaults to the current UTC time.
    delay_hours
        Assumed publication delay after the cycle time.
    """
    ref = to_utc_naive(now) if now is not None else to_utc_naive(pd.Timestamp.now(tz="UTC"))
    return floor_cycle(ref - timedelta(hours=delay_hours))


# ---------------------------------------------------------------------------
# Forecast hours and valid times
# ---------------------------------------------------------------------------
def valid_time(init_time: TimeLike, forecast_hour: int) -> pd.Timestamp:
    """``init_time + forecast_hour``."""
    return to_utc_naive(init_time) + pd.Timedelta(hours=forecast_hour)


def init_time_for(valid: TimeLike, forecast_hour: int) -> pd.Timestamp:
    """Cycle whose ``forecast_hour`` forecast is valid at ``valid``.

    Raises
    ------
    ValueError
        If ``valid - forecast_hour`` is not a cycle (no such forecast exists).
    """
    init = to_utc_naive(valid) - pd.Timedelta(hours=forecast_hour)
    if not is_cycle(init):
        raise ValueError(
            f"no cycle has an f{forecast_hour:03d} forecast valid at {to_utc_naive(valid)} "
            f"(would need init {init})"
        )
    return init


def forecasts_valid_at(
    valid: TimeLike,
    available_hours: Iterable[int],
    max_forecast_hour: int | None = None,
) -> list[tuple[pd.Timestamp, int]]:
    """Every (init_time, forecast_hour) pair whose forecast is valid at ``valid``.

    Used to build lead-time comparisons: e.g. all GFS runs that forecast
    2024-01-16 00 UTC are ``(2024-01-15 18, 6)``, ``(2024-01-15 12, 12)``, ...

    Parameters
    ----------
    valid
        Target valid time.
    available_hours
        Forecast hours the source publishes, e.g. ``gfs.SPEC.forecast_hours``.
    max_forecast_hour
        Optional upper limit on lead time.

    Returns
    -------
    list of (init_time, forecast_hour)
        Sorted by increasing forecast hour (most recent run first).
    """
    target = to_utc_naive(valid)
    pairs = []
    for hour in sorted(available_hours):
        if max_forecast_hour is not None and hour > max_forecast_hour:
            break
        init = target - pd.Timedelta(hours=hour)
        if is_cycle(init):
            pairs.append((init, hour))
    return pairs


def analysis_cycle_for(valid: TimeLike) -> pd.Timestamp:
    """The analysis (``f000``) cycle valid at ``valid``.

    This is the GDAS cycle to download as the reference for a forecast
    valid at ``valid``. Analyses exist only at cycle times, so ``valid``
    must itself be a cycle.
    """
    return init_time_for(valid, 0)


# ---------------------------------------------------------------------------
# Selecting times in data
# ---------------------------------------------------------------------------
def select_time_range(data: Table, start: TimeLike | None = None, end: TimeLike | None = None) -> Table:
    """Rows/steps with valid ``time`` in ``[start, end]`` (inclusive; open if None).

    Works on both the tabular (DataFrame with a ``time`` column) and gridded
    (Dataset with a ``time`` dimension) internal formats.
    """
    lo = to_utc_naive(start) if start is not None else None
    hi = to_utc_naive(end) if end is not None else None

    if isinstance(data, xr.Dataset):
        times = pd.DatetimeIndex(data[schema.TIME].values)
    else:
        times = pd.DatetimeIndex(series_to_utc_naive(data[schema.TIME]))
    keep = np.ones(len(times), dtype=bool)
    if lo is not None:
        keep &= times >= lo
    if hi is not None:
        keep &= times <= hi

    if isinstance(data, xr.Dataset):
        return data.isel({schema.TIME: np.flatnonzero(keep)})
    return data.loc[keep]
