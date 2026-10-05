"""GFS (Global Forecast System) 10-m winds.

GFS is NCEP's global deterministic forecast model. Cycles run at 00, 06, 12
and 18 UTC. The 0.25 degree product provides forecast hours 0-120 hourly and
123-384 every 3 hours. ``f000`` is the GFS analysis for the cycle (an early
analysis with an earlier observation cutoff than the GDAS final analysis).

Everything is implemented in :mod:`wind_data.download.common`; this module
only fixes the source-specific parameters.

Usage::

    from wind_data.download import gfs
    ds = gfs.get_wind("2024-01-15T00", forecast_hours=[0, 6, 12])
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import xarray as xr

from wind_data.download import common
from wind_data.processing.time import TimeLike

SPEC = common.SourceSpec(
    name="gfs",
    forecast_hours=frozenset(range(0, 121)) | frozenset(range(123, 385, 3)),
    description="NCEP Global Forecast System, 0.25 deg, f000-f384",
)


def download(
    init_time: TimeLike,
    forecast_hour: int = 0,
    *,
    data_dir: Path | None = None,
    resolution: str = "0p25",
    overwrite: bool = False,
) -> Path:
    """Download the 10-m U/V subset for one GFS file; see :func:`common.download_wind`."""
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
    """Gridded 10-m winds for one GFS cycle in the internal format; see :func:`common.get_wind`."""
    return common.get_wind(
        SPEC,
        init_time,
        forecast_hours,
        data_dir=data_dir,
        resolution=resolution,
        overwrite=overwrite,
    )
