"""GDAS (Global Data Assimilation System) 10-m winds.

GDAS is the data-assimilation cycle that produces the initial conditions for
GFS. Cycles run at 00, 06, 12 and 18 UTC.

* ``f000`` is the GDAS **final analysis** for the cycle. It uses the same
  model as GFS but is run about six hours later with a later observation
  cutoff, so it assimilates more observations than the GFS ``f000``. This is
  why GDAS ``f000`` is used as the reference ("truth") when verifying GFS.
* ``f001``-``f009`` are short-range forecasts used as the first guess for the
  next cycle. They are available but are rarely what you want for analysis.
* Published on a 0.25 and 1.0 degree grid (no 0.5 degree product).

Four analyses per day give a 6-hourly reference series. To compare a GFS
forecast against GDAS, request the GDAS cycle whose init time equals the GFS
valid time, e.g. GFS 2024-01-15 00z f006 against GDAS 2024-01-15 06z f000.

Everything is implemented in :mod:`wind_data.download.common`; this module
only fixes the source-specific parameters.

Usage::

    from wind_data.download import gdas
    ds = gdas.get_wind("2024-01-15T06")   # analysis (f000) by default
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import xarray as xr

from wind_data.download import common
from wind_data.processing.time import TimeLike

SPEC = common.SourceSpec(
    name="gdas",
    forecast_hours=frozenset(range(0, 10)),
    description="NCEP Global Data Assimilation System, final analysis (f000) and f001-f009",
    resolutions=("0p25", "1p00"),
)


def download(
    init_time: TimeLike,
    forecast_hour: int = 0,
    *,
    data_dir: Path | None = None,
    resolution: str = "0p25",
    overwrite: bool = False,
) -> Path:
    """Download the 10-m U/V subset for one GDAS file; see :func:`common.download_wind`."""
    return common.download_wind(
        SPEC,
        init_time,
        forecast_hour,
        data_dir=data_dir,
        resolution=resolution,
        overwrite=overwrite,
    )


def get_wind(
    init_time: TimeLike,
    forecast_hours: int | Sequence[int] = 0,
    *,
    data_dir: Path | None = None,
    resolution: str = "0p25",
    overwrite: bool = False,
) -> xr.Dataset:
    """Gridded 10-m winds for one GDAS cycle in the internal format; see :func:`common.get_wind`.

    The default ``forecast_hours=0`` returns the final analysis.
    """
    return common.get_wind(
        SPEC,
        init_time,
        forecast_hours,
        data_dir=data_dir,
        resolution=resolution,
        overwrite=overwrite,
    )
