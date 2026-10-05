"""GFS and GDAS fields for the storm period: download, read, crop.

Products
--------
Both come from the NOAA Open Data bucket ``noaa-gfs-bdp-pds`` (the same
archive :mod:`wind_data.download` uses), ``pgrb2`` at the configured
resolution (0.25 deg by default). Only the records listed in
``[models].fields`` are fetched, with HTTP byte ranges read from the file's
``.idx`` (by default UGRD and VGRD at 10 m and PRMSL at mean sea level).

* **GDAS f000** is the GDAS final analysis for its cycle (``field_type =
  "analysis"``).
* **GFS f000** is the GFS (early) analysis; every other GFS lead is a
  forecast. ``field_type`` records which, next to ``init_time``,
  ``forecast_hour`` and ``valid_time``.

The byte ranges are global fields: AWS offers no spatial subsetting. The
unmodified GRIB2 subset and its ``.idx`` are kept under
``<data_dir>/raw/<source>/<source>.YYYYMMDD/HH/`` next to wind_data's own
10-m wind files (different suffix, so they never collide), and a cropped
NetCDF copy of the analysis domain is written to the processed folder.

Which files
-----------
Valid times are every ``valid_step_hours`` (hourly by default) in the
analysis period. Each series is a *lead window* starting at lead ``L``: for
valid time ``t`` the run used is the latest cycle at or before ``t - L``, so
its lead lies in ``[L, L + 5]`` h (cycles are 6 h apart). Every valid time
therefore gets exactly one run per window, and ``lead_window``,
``init_time``, ``forecast_hour`` and ``valid_time`` are kept on every value.
GDAS window 0 gives the f000 analysis at the cycles and f001-f005 short-range
forecasts between them; ``field_type`` says which.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import xarray as xr

from wind_data import schema
from wind_data.download import common
from wind_data.download.gdas import SPEC as GDAS
from wind_data.download.gfs import SPEC as GFS
from wind_data.hurricane.config import HurricaneConfig
from wind_data.hurricane.fetch import Fetcher, sha256_of, utc_now_iso
from wind_data.processing import coordinates
from wind_data.processing.time import floor_cycle
from wind_data.processing.wind import add_speed_and_direction

log = logging.getLogger(__name__)

SPECS = {"gfs": GFS, "gdas": GDAS}
SUFFIX = ".10mwind_prmsl.grib2"
PRMSL = "prmsl"
CROP_MARGIN_DEG = 1.0  # extra grid around the domain so bilinear weights exist at the edge


def plan(cfg: HurricaneConfig, period: tuple[pd.Timestamp, pd.Timestamp]) -> pd.DataFrame:
    """Every (source, lead_window, init_time, forecast_hour, valid_time) the evaluation needs.

    Raises ``ValueError`` if a window needs a forecast hour the source does
    not publish (e.g. GFS f121: hourly output stops at 120 h).
    """
    step = pd.Timedelta(hours=cfg.valid_step_hours)
    valid = pd.date_range(pd.Timestamp(period[0]).ceil(step), pd.Timestamp(period[1]).floor(step), freq=step)
    rows = []
    for source, windows in (("gdas", cfg.gdas_lead_windows), ("gfs", cfg.gfs_lead_windows)):
        spec = SPECS[source]
        for window in windows:
            for v in valid:
                init = floor_cycle(v - pd.Timedelta(hours=window))
                while init.hour not in cfg.cycles:  # only when some cycles are excluded
                    init -= pd.Timedelta(hours=6)
                lead = int((v - init) / pd.Timedelta(hours=1))
                spec.check_forecast_hour(lead)
                rows.append({"source": source, "lead_window": int(window), "init_time": init, "forecast_hour": lead,
                             "valid_time": v, "field_type": "analysis" if lead == 0 else "forecast"})
    return pd.DataFrame(rows)


def window_label(source: str, window: int, width: int = 5) -> str:
    """``'GFS f006-f011'``: the lead range a window covers."""
    return f"{source.upper()} f{window:03d}-f{window + width:03d}"


def raw_path(cfg: HurricaneConfig, source: str, init: pd.Timestamp, lead: int) -> Path:
    base = common.local_path(SPECS[source], init, lead, cfg.resolution, cfg.model_raw_dir)
    return base.with_name(base.name.replace(".10mwind.grib2", SUFFIX))


def processed_path(cfg: HurricaneConfig, source: str, init: pd.Timestamp, lead: int) -> Path:
    return cfg.processed_dir / "models" / source / f"{source}_{init:%Y%m%d%H}_f{lead:03d}.nc"


def download(cfg: HurricaneConfig, fetcher: Fetcher, source: str, init: pd.Timestamp, lead: int,
             *, overwrite: bool = False) -> Path:
    """Fetch the configured records of one file (cached; atomic; manifested)."""
    spec = SPECS[source]
    dest = raw_path(cfg, source, init, lead)
    if dest.exists() and not overwrite:
        return dest
    url = common.build_url(spec, init, lead, cfg.resolution)
    idx_res = fetcher.fetch(url + ".idx", dest.with_name(dest.name.replace(SUFFIX, ".idx")), overwrite=overwrite)
    records = common.select_fields(common.parse_index(idx_res.path.read_text()), cfg.model_fields)
    ranges = common.merge_ranges(records)

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with part.open("wb") as fh:
            for start, end in ranges:
                header = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
                content, status = fetcher.get_bytes(url, headers={"Range": header})
                if status != 206:
                    raise RuntimeError(f"server ignored Range header for {url}")
                if end is not None and len(content) != end - start + 1:
                    raise RuntimeError(f"short read for {url} [{header}]: {len(content)} bytes")
                fh.write(content)
        _check_grib(part, len(records))
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    fetcher.manifest.write(url=url, dest=fetcher._rel(dest), cache="miss", retrieved_utc=utc_now_iso(),
                           http_status=206, byte_ranges=[list(r) for r in ranges],
                           records=[f"{r.variable}:{r.level}" for r in records], bytes=dest.stat().st_size,
                           sha256=sha256_of(dest))
    return dest


def _check_grib(path: Path, n_records: int) -> None:
    """Each record must start with 'GRIB' and end with '7777'."""
    data = path.read_bytes()
    if data.count(b"GRIB") < n_records or not data.startswith(b"GRIB") or not data.endswith(b"7777"):
        raise ValueError(f"{path.name}: not {n_records} complete GRIB2 records")


def read(path: Path, source: str) -> xr.Dataset:
    """Winds and PRMSL from one subset file in the internal gridded format."""
    with xr.open_dataset(path, engine="cfgrib", decode_timedelta=True, backend_kwargs={
            "indexpath": "", "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}}) as raw:
        ds = common.standardize(raw.load(), source)
    try:
        with xr.open_dataset(path, engine="cfgrib", decode_timedelta=True, backend_kwargs={
                "indexpath": "", "filter_by_keys": {"typeOfLevel": "meanSea"}}) as raw_p:
            p = raw_p.load()
        name = next(v for v in p.data_vars if str(v).lower() in ("prmsl", "msl"))
        field = coordinates.normalize_dataset_longitude(
            p[[name]].drop_vars(["time", "step", "valid_time", "meanSea"], errors="ignore"))[name]
        # Assignment aligns on latitude/longitude labels, so grid order cannot mismatch.
        ds[PRMSL] = (field / 100.0).expand_dims(time=ds["time"].values)
        ds[PRMSL].attrs = {"units": "hPa", "long_name": "pressure reduced to mean sea level"}
    except (StopIteration, ValueError, KeyError) as err:
        log.warning("%s: no PRMSL (%s)", path.name, err)
    return add_speed_and_direction(ds)


def crop(ds: xr.Dataset, domain, margin: float = CROP_MARGIN_DEG) -> xr.Dataset:
    lat = ds[schema.LATITUDE]
    lat_slice = (slice(domain.lat_max + margin, domain.lat_min - margin) if lat[0] > lat[-1]
                 else slice(domain.lat_min - margin, domain.lat_max + margin))
    return ds.sel({schema.LATITUDE: lat_slice,
                   schema.LONGITUDE: slice(domain.lon_min - margin, domain.lon_max + margin)})


def _domain_tag(domain) -> str:
    return f"{domain.lat_min:g},{domain.lat_max:g},{domain.lon_min:g},{domain.lon_max:g}"


def _cropped_domain(nc: Path) -> str:
    """The domain an existing cropped file was made for ('' if unknown)."""
    try:
        with xr.open_dataset(nc, decode_timedelta=False) as ds:
            return str(ds.attrs.get("domain", ""))
    except Exception:
        return ""


def _download_all(cfg: HurricaneConfig, fetcher: Fetcher, files: pd.DataFrame, overwrite: bool,
                  progress_every: int) -> dict[tuple, object]:
    """Download every distinct file in parallel; returns {(source, init, lead): Path or Exception}."""
    keys = list(dict.fromkeys((r.source, pd.Timestamp(r.init_time), int(r.forecast_hour))
                              for r in files.itertuples(index=False)))
    results: dict[tuple, object] = {}

    def one(key):
        try:
            return key, download(cfg, fetcher, *key, overwrite=overwrite)
        except Exception as err:  # recorded per file; the batch continues
            return key, err

    with ThreadPoolExecutor(max_workers=cfg.download_workers) as pool:
        for n, (key, res) in enumerate(pool.map(one, keys), start=1):
            results[key] = res
            if n % progress_every == 0 or n == len(keys):
                ok = sum(isinstance(v, Path) for v in results.values())
                log.info("models: downloaded %d/%d files (%d ok)", n, len(keys), ok)
    return results


def fetch_all(cfg: HurricaneConfig, fetcher: Fetcher, files: pd.DataFrame, domain, *,
              overwrite: bool = False, progress_every: int = 100) -> pd.DataFrame:
    """Download (in parallel), then read and crop (sequentially) every planned file.

    Returns the plan with ``status``/``detail``; a missing or unreadable file
    is recorded and the batch continues.
    """
    downloaded = _download_all(cfg, fetcher, files, overwrite, progress_every)
    out = files.copy()
    out["status"], out["detail"], out["raw_file"], out["processed_file"] = "", "", "", ""
    for n, (i, row) in enumerate(out.iterrows(), start=1):
        source, init, lead = row["source"], pd.Timestamp(row["init_time"]), int(row["forecast_hour"])
        nc = processed_path(cfg, source, init, lead)
        try:
            raw = downloaded[(source, init, lead)]
            if isinstance(raw, Exception):
                raise raw
            assert isinstance(raw, Path)
            if overwrite or not nc.exists() or _cropped_domain(nc) != _domain_tag(domain):
                ds = crop(read(raw, source), domain)
                ds.attrs.update(field_type=row["field_type"], resolution=cfg.resolution,
                                product=f"{source}.pgrb2.{cfg.resolution}",
                                url=common.build_url(SPECS[source], init, lead, cfg.resolution),
                                raw_file=raw.name, domain=_domain_tag(domain))
                nc.parent.mkdir(parents=True, exist_ok=True)
                ds.to_netcdf(nc)
            out.at[i, "status"] = "ok"
            out.at[i, "raw_file"], out.at[i, "processed_file"] = raw.name, str(nc)
        except FileNotFoundError as err:
            out.at[i, "status"], out.at[i, "detail"] = "not available", str(err)
        except Exception as err:
            log.error("%s %s f%03d: %s", source, init, lead, err)
            out.at[i, "status"], out.at[i, "detail"] = "failed", str(err)
        if n % progress_every == 0 or n == len(out):
            log.info("models: read/cropped %d/%d (%d ok)", n, len(out), int((out["status"] == "ok").sum()))
    return out
