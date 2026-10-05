"""Shared download and reading logic for NCEP GFS and GDAS.

Both products are published in the same NOAA Open Data bucket on AWS with
identical file naming and identical GRIB2 record layouts, so one
implementation serves both. :mod:`wind_data.download.gfs` and
:mod:`wind_data.download.gdas` only supply a :class:`SourceSpec`.

Data source
-----------
Bucket ``noaa-gfs-bdp-pds`` over anonymous HTTPS (no credentials, no AWS
SDK). URL pattern (GFS v16 layout, 2021-03-22 onward)::

    {base}/{source}.{YYYYMMDD}/{HH}/atmos/{source}.t{HH}z.pgrb2.{res}.f{FFF}

Each GRIB2 file has a ``.idx`` sidecar listing the byte offset of every
record. We read the index, find the ``UGRD``/``VGRD`` records at
``10 m above ground``, and fetch only those bytes with HTTP ``Range``
requests (about 2 MB instead of 540 MB per file). The result is a valid,
self-contained GRIB2 file that cfgrib can open.

Local cache
-----------
Downloads are saved under ``<data_dir>/raw/<source>/<source>.<YYYYMMDD>/<HH>/``
and reused on later calls. ``data_dir`` defaults to ``./data`` or the
``WIND_DATA_DIR`` environment variable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import requests
import xarray as xr
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from wind_data import schema
from wind_data.processing import coordinates
from wind_data.processing.time import TimeLike, validate_cycle
from wind_data.processing.wind import add_speed_and_direction

log = logging.getLogger(__name__)

AWS_BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
WIND_VARIABLES: tuple[str, ...] = ("UGRD", "VGRD")
WIND_LEVEL = "10 m above ground"
RESOLUTIONS: tuple[str, ...] = ("0p25", "0p50", "1p00")
#: (connect, read) timeouts in seconds.
DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 120.0)
USER_AGENT = "sea_grant wind_data (https://github.com/arash-rafiee/sea_grant)"


# ---------------------------------------------------------------------------
# Source description
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceSpec:
    """Everything that differs between GFS and GDAS."""

    name: str
    """Short name used in URLs, file names and the ``source`` column."""
    forecast_hours: frozenset[int]
    """Forecast hours available for the 0.25 degree product."""
    description: str
    resolutions: tuple[str, ...] = RESOLUTIONS
    """Grid resolutions published for this source (subset of ``RESOLUTIONS``)."""

    def check_resolution(self, resolution: str) -> str:
        if resolution not in self.resolutions:
            raise ValueError(
                f"resolution {resolution!r} is not published for {self.name.upper()}; "
                f"choose one of {self.resolutions}"
            )
        return resolution

    def check_forecast_hour(self, hour: int) -> int:
        if hour not in self.forecast_hours:
            raise ValueError(
                f"forecast hour {hour} is not available for {self.name.upper()}; "
                f"valid range is {min(self.forecast_hours)}-{max(self.forecast_hours)}"
            )
        return hour


@dataclass(frozen=True)
class IndexRecord:
    """One line of a GRIB ``.idx`` file, with its resolved byte range."""

    number: int
    start: int
    end: int | None  # None = to end of file (last record)
    variable: str
    level: str
    step: str


# ---------------------------------------------------------------------------
# Paths and URLs
# ---------------------------------------------------------------------------
def default_data_dir() -> Path:
    """``$WIND_DATA_DIR`` if set, otherwise ``./data``."""
    return Path(os.environ.get("WIND_DATA_DIR", "data"))


def remote_filename(
    spec: SourceSpec, init_time: pd.Timestamp, forecast_hour: int, resolution: str
) -> str:
    """e.g. ``gfs.t00z.pgrb2.0p25.f006``."""
    return f"{spec.name}.t{init_time:%H}z.pgrb2.{resolution}.f{forecast_hour:03d}"


def build_url(
    spec: SourceSpec,
    init_time: pd.Timestamp,
    forecast_hour: int,
    resolution: str = "0p25",
    base_url: str = AWS_BASE_URL,
) -> str:
    """Full URL of the GRIB2 file (append ``.idx`` for its index)."""
    spec.check_resolution(resolution)
    return (
        f"{base_url}/{spec.name}.{init_time:%Y%m%d}/{init_time:%H}/atmos/"
        f"{remote_filename(spec, init_time, forecast_hour, resolution)}"
    )


def local_path(
    spec: SourceSpec,
    init_time: pd.Timestamp,
    forecast_hour: int,
    resolution: str = "0p25",
    data_dir: Path | None = None,
) -> Path:
    """Where the 10-m wind subset for this file is cached locally."""
    root = (data_dir or default_data_dir()) / "raw" / spec.name
    name = remote_filename(spec, init_time, forecast_hour, resolution) + ".10mwind.grib2"
    return root / f"{spec.name}.{init_time:%Y%m%d}" / f"{init_time:%H}" / name


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def make_session(retries: int = 5, backoff: float = 1.0) -> requests.Session:
    """A :class:`requests.Session` with retries on transient errors."""
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = USER_AGENT
    return session


def parse_index(text: str) -> list[IndexRecord]:
    """Parse a NCEP ``.idx`` file into records with byte ranges.

    Line format: ``n:start_byte:d=YYYYMMDDHH:VARIABLE:LEVEL:STEP:``.
    Record ``n`` spans ``start_n`` to ``start_{n+1} - 1``; the last record
    runs to end of file.
    """
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 6:
            continue
        rows.append((int(parts[0]), int(parts[1]), parts[3], parts[4], parts[5]))
    records = []
    for i, (number, start, var, level, step) in enumerate(rows):
        end = rows[i + 1][1] - 1 if i + 1 < len(rows) else None
        records.append(IndexRecord(number, start, end, var, level, step))
    return records


def select_records(
    records: Sequence[IndexRecord],
    variables: Sequence[str] = WIND_VARIABLES,
    level: str = WIND_LEVEL,
) -> list[IndexRecord]:
    """Records matching the requested variables at the requested level."""
    chosen = [r for r in records if r.variable in variables and r.level == level]
    missing = set(variables) - {r.variable for r in chosen}
    if missing:
        raise ValueError(f"index has no {sorted(missing)} at '{level}'")
    return sorted(chosen, key=lambda r: r.number)


def select_fields(records: Sequence[IndexRecord], fields: Sequence[tuple[str, str]]) -> list[IndexRecord]:
    """Records matching any ``(variable, level)`` pair, e.g. PRMSL at mean sea level.

    Like :func:`select_records` but for variables on different levels.
    Raises ``ValueError`` naming every pair the index does not contain.
    """
    wanted = set(fields)
    chosen = [r for r in records if (r.variable, r.level) in wanted]
    missing = wanted - {(r.variable, r.level) for r in chosen}
    if missing:
        raise ValueError(f"index has no {sorted(missing)}")
    return sorted(chosen, key=lambda r: r.number)


def merge_ranges(records: Sequence[IndexRecord]) -> list[tuple[int, int | None]]:
    """Public alias of :func:`_merge_ranges` for other download layers."""
    return _merge_ranges(records)


def _merge_ranges(records: Sequence[IndexRecord]) -> list[tuple[int, int | None]]:
    """Collapse consecutive records into contiguous (start, end) byte ranges."""
    ranges: list[tuple[int, int | None]] = []
    for r in records:
        if ranges and ranges[-1][1] is not None and ranges[-1][1] + 1 == r.start:
            ranges[-1] = (ranges[-1][0], r.end)
        else:
            ranges.append((r.start, r.end))
    return ranges


def _range_header(start: int, end: int | None) -> str:
    return f"bytes={start}-" if end is None else f"bytes={start}-{end}"


def download_wind(
    spec: SourceSpec,
    init_time: TimeLike,
    forecast_hour: int = 0,
    *,
    data_dir: Path | None = None,
    resolution: str = "0p25",
    overwrite: bool = False,
    session: requests.Session | None = None,
) -> Path:
    """Download the 10-m U/V records for one file, returning the local path.

    Skips the network entirely if the file is already cached, unless
    ``overwrite`` is True. The download is written to a temporary file and
    renamed on success, so an interrupted run never leaves a partial file.

    Raises
    ------
    FileNotFoundError
        If the remote file does not exist (cycle not yet published, or date
        before the archive begins).
    """
    init = validate_cycle(init_time)
    spec.check_forecast_hour(forecast_hour)
    dest = local_path(spec, init, forecast_hour, resolution, data_dir)
    if dest.exists() and not overwrite:
        log.debug("cached: %s", dest)
        return dest

    url = build_url(spec, init, forecast_hour, resolution)
    session = session or make_session()

    idx = session.get(url + ".idx", timeout=DEFAULT_TIMEOUT)
    if idx.status_code == 404:
        raise FileNotFoundError(f"no {spec.name.upper()} file at {url}")
    idx.raise_for_status()
    records = select_records(parse_index(idx.text))

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with tmp.open("wb") as fh:
            for start, end in _merge_ranges(records):
                header = _range_header(start, end)
                log.info("GET %s [%s]", url, header)
                resp = session.get(url, headers={"Range": header}, timeout=DEFAULT_TIMEOUT)
                resp.raise_for_status()
                if resp.status_code != 206:
                    raise RuntimeError(f"server ignored Range header for {url}")
                fh.write(resp.content)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


# ---------------------------------------------------------------------------
# Reading and standardizing
# ---------------------------------------------------------------------------
def open_wind_grib(path: Path) -> xr.Dataset:
    """Open a 10-m wind GRIB2 subset with cfgrib.

    ``indexpath=""`` stops cfgrib writing an ``.idx`` file next to the data,
    which matters on read-only or shared HPC filesystems. The files are small
    so re-indexing on every open is cheap.
    """
    return xr.open_dataset(
        path,
        engine="cfgrib",
        decode_timedelta=True,  # `step` is a duration; silences an xarray FutureWarning
        backend_kwargs={
            "indexpath": "",
            "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10},
        },
    )


def standardize(ds: xr.Dataset, source: str) -> xr.Dataset:
    """Convert a cfgrib 10-m wind dataset into the gridded internal format.

    * variables ``u10``/``v10`` (float32, m/s)
    * dimension ``time`` = **valid time**; coordinates ``init_time`` and
      ``forecast_hour`` along it
    * ``longitude`` in [-180, 180), ascending
    * ``attrs["source"]`` = ``"gfs"`` / ``"gdas"`` (the tabular form turns
      this into the ``source`` column)
    """
    if schema.U10 not in ds or schema.V10 not in ds:
        raise ValueError(
            f"expected variables {schema.U10}/{schema.V10}, found {list(ds.data_vars)}"
        )
    if "time" in ds.dims:  # cfgrib only adds a time dim for multi-cycle files
        raise ValueError("standardize() expects one init time per file")

    init = pd.Timestamp(ds["time"].values)
    forecast_hour = float(ds["step"].values / np.timedelta64(1, "h"))
    valid = pd.Timestamp(ds["valid_time"].values)

    out = ds[[schema.U10, schema.V10]].drop_vars(
        ["time", "step", "valid_time", "heightAboveGround"], errors="ignore"
    )
    out = out.expand_dims(time=[valid.to_datetime64()])
    out = out.assign_coords(
        init_time=("time", [init.to_datetime64()]),
        forecast_hour=("time", [forecast_hour]),
    )
    out = coordinates.normalize_dataset_longitude(out)

    out.attrs = {"source": source, "conventions": "see README, section on GFS/GDAS wind data"}
    out[schema.U10].attrs = {"units": "m s-1", "long_name": "10 m eastward wind"}
    out[schema.V10].attrs = {"units": "m s-1", "long_name": "10 m northward wind"}
    out["time"].attrs = {"long_name": "valid time (UTC)"}
    out["init_time"].attrs = {"long_name": "model initialization time (UTC)"}
    out["forecast_hour"].attrs = {"units": "hours"}
    return out


def get_wind(
    spec: SourceSpec,
    init_time: TimeLike,
    forecast_hours: int | Sequence[int] = 0,
    *,
    data_dir: Path | None = None,
    resolution: str = "0p25",
    overwrite: bool = False,
) -> xr.Dataset:
    """Download (if needed), read, and standardize 10-m winds for one cycle.

    Parameters
    ----------
    spec
        Source description (``gfs.SPEC`` or ``gdas.SPEC``).
    init_time
        Cycle initialization time, UTC, on a 00/06/12/18 hour.
    forecast_hours
        One forecast hour or a sequence; results are concatenated along
        ``time`` (valid time).
    data_dir, resolution, overwrite
        See :func:`download_wind`.

    Returns
    -------
    xarray.Dataset
        Gridded internal format with ``u10``, ``v10``, ``wind_speed`` and
        ``wind_direction`` on (time, latitude, longitude).
    """
    hours = [forecast_hours] if isinstance(forecast_hours, int) else list(forecast_hours)
    session = make_session()
    pieces = []
    for hour in hours:
        path = download_wind(
            spec,
            init_time,
            hour,
            data_dir=data_dir,
            resolution=resolution,
            overwrite=overwrite,
            session=session,
        )
        with open_wind_grib(path) as raw:
            pieces.append(standardize(raw, spec.name).load())
    ds = pieces[0] if len(pieces) == 1 else xr.concat(pieces, dim="time")
    return add_speed_and_direction(ds)
