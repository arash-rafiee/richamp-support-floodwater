"""Download and parse observed winds from NDBC, NOAA CO-OPS and NCEI GHCNh.

Raw files are saved unchanged under ``<raw_dir>/obs/<source>/`` (see
:mod:`wind_data.hurricane.fetch`). Each parser turns one raw file into the
common observation table below; missing-value sentinels become NaN and are
recorded in ``missing`` rather than dropped, and the provider's own quality
flags are kept verbatim in ``source_qc``.

Observation table
-----------------
===================  ======================================================
source, station_id   provider and its station identifier
time                 UTC, naive datetime64 (the package convention)
wind_speed           sustained speed at the sensor height, m s-1
wind_direction       degrees from true north the wind blows *from*; NaN if
                     calm or variable
wind_gust            peak gust, m s-1 (never height-adjusted)
averaging            averaging period as documented by the provider
source_qc            provider QC codes, verbatim ('' if none)
missing              which of speed/direction/gust were missing, and the
                     sentinel that marked them, e.g. 'gust=99.0'
calm                 True if the provider reported calm
raw_file             raw file the row came from
===================  ======================================================

Averaging periods (provider documentation):
NDBC https://www.ndbc.noaa.gov/faq/measdes.shtml: WSPD is an 8-minute mean
on buoys and a 2-minute mean at land (C-MAN) stations; GST is the peak 5- or
8-second gust in that period. CO-OPS: the API documentation does not state
the averaging period. GHCNh: METAR/SYNOP winds, averaging per the
``Measurement_Code`` (H 5-min, R 60-min, T 180-min; N 'normal', i.e. the
reporting practice of the originating service).
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from wind_data.hurricane.config import HurricaneConfig
from wind_data.hurricane.fetch import Fetcher, text_starting_with, valid_gzip, valid_json

log = logging.getLogger(__name__)

OBS_COLUMNS = ("source", "station_id", "time", "wind_speed", "wind_direction", "wind_gust", "averaging",
               "source_qc", "missing", "calm", "raw_file")

KNOT = 0.514444


def _frame(rows: dict[str, object], n: int) -> pd.DataFrame:
    df = pd.DataFrame({c: rows.get(c, [""] * n) for c in OBS_COLUMNS})
    df["time"] = pd.to_datetime(df["time"])
    for c in ("wind_speed", "wind_direction", "wind_gust"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["calm"] = df["calm"].astype(bool)
    return df


def _join_missing(*parts: np.ndarray) -> np.ndarray:
    out = []
    for items in zip(*parts):
        out.append(";".join(p for p in items if p))
    return np.array(out, dtype=object)


# ---------------------------------------------------------------------------
# NDBC standard meteorological data
# ---------------------------------------------------------------------------
NDBC_MISSING = {"WDIR": 999.0, "WSPD": 99.0, "GST": 99.0}


def parse_ndbc_stdmet(text: str, station_id: str, *, station_class: str = "offshore",
                      raw_file: str = "") -> pd.DataFrame:
    """Parse an NDBC stdmet file (historical annual or monthly)."""
    lines = text.splitlines()
    header = next((ln for ln in lines if ln.startswith("#YY") or ln.startswith("YY")), None)
    if header is None:
        raise ValueError(f"NDBC {station_id}: no '#YY' header line")
    names = header.lstrip("#").split()
    body = "\n".join(ln for ln in lines if ln.strip() and not ln.startswith("#") and not ln.startswith("YY"))
    # Realtime files mark missing values "MM" instead of 99/999.
    df = pd.read_csv(io.StringIO(body), sep=r"\s+", header=None, names=names, dtype=float, na_values=["MM"])
    for need in ("MM", "DD", "hh", "WDIR", "WSPD", "GST"):
        if need not in df.columns:
            raise ValueError(f"NDBC {station_id}: column {need} missing from header {names}")
    year_col = "YY" if "YY" in df.columns else names[0]
    year = df[year_col].astype(int)
    year = np.where(year < 100, year + 1900, year)
    minute = df["mm"].astype(int) if "mm" in df.columns else 0
    time = pd.to_datetime(dict(year=year, month=df["MM"].astype(int), day=df["DD"].astype(int),
                               hour=df["hh"].astype(int), minute=minute))

    missing_parts = []
    values = {}
    for col, key in (("WSPD", "speed"), ("WDIR", "direction"), ("GST", "gust")):
        v = df[col].to_numpy(dtype=float)
        is_mm = np.isnan(v)
        is_missing = is_mm | (v >= NDBC_MISSING[col])
        missing_parts.append(np.where(is_mm, f"{key}=MM", np.where(is_missing, f"{key}={NDBC_MISSING[col]:g}", "")))
        values[key] = np.where(is_missing, np.nan, v)

    calm = values["speed"] == 0
    averaging = "8-min mean (NDBC buoy)" if station_class == "offshore" else "2-min mean (NDBC land station)"
    n = len(df)
    return _frame({
        "source": ["ndbc"] * n, "station_id": [station_id] * n, "time": time,
        "wind_speed": values["speed"], "wind_direction": np.where(calm, np.nan, values["direction"]),
        "wind_gust": values["gust"], "averaging": [averaging] * n, "source_qc": [""] * n,
        "missing": _join_missing(*missing_parts), "calm": calm, "raw_file": [raw_file] * n,
    }, n)


NDBC_MONTH_CODES = "123456789abc"  # NDBC monthly file names use 1-9, a, b, c for Jan-Dec


def ndbc_monthly_url(station_id: str, year: int, month: int) -> str:
    """Monthly stdmet file, used when no annual file exists (e.g. .../Oct/41036a2025.txt.gz)."""
    name = pd.Timestamp(year=year, month=month, day=1).strftime("%b")
    return (f"https://www.ndbc.noaa.gov/data/stdmet/{name}/"
            f"{station_id.lower()}{NDBC_MONTH_CODES[month - 1]}{year}.txt.gz")


NDBC_REALTIME_DAYS = 45  # realtime2 files hold the last 45 days


def ndbc_realtime_url(station_id: str) -> str:
    """Rolling last-45-days stdmet file (plain text, newest first)."""
    return f"https://www.ndbc.noaa.gov/data/realtime2/{station_id.upper()}.txt"


def download_ndbc(cfg: HurricaneConfig, fetcher: Fetcher, station_id: str, years,
                  period: tuple[pd.Timestamp, pd.Timestamp] | None = None,
                  now: pd.Timestamp | None = None) -> list[Path]:
    """Annual historical files; monthly files for any year without one; the
    realtime file for recent months that are not archived yet.

    Archive files come first, so where they overlap the realtime file the QC
    duplicate rule keeps the archived (quality-controlled) value.
    Raises FileNotFoundError only if nothing exists for any needed month.
    """
    paths, unarchived = [], []
    raw = cfg.raw_dir / "obs" / "ndbc"
    for year in years:
        url = f"https://www.ndbc.noaa.gov/data/historical/stdmet/{station_id.lower()}h{year}.txt.gz"
        try:
            paths.append(fetcher.fetch(url, raw / f"{station_id.lower()}h{year}.txt.gz", validate=valid_gzip).path)
            continue
        except FileNotFoundError:
            if period is None:
                raise
        months = pd.period_range(max(period[0], pd.Timestamp(year=year, month=1, day=1)),
                                 min(period[1], pd.Timestamp(year=year, month=12, day=31)), freq="M")
        for m in months:
            url = ndbc_monthly_url(station_id, m.year, m.month)
            try:
                paths.append(fetcher.fetch(url, raw / Path(url).name, validate=valid_gzip).path)
            except FileNotFoundError:
                unarchived.append(m)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None) if now is None else now
    recent = [m for m in unarchived if m.end_time >= now - pd.Timedelta(days=NDBC_REALTIME_DAYS)]
    if recent:
        # Changes every 10 min, so the cache name carries the retrieval date.
        dest = raw / "realtime2" / f"{station_id.lower()}_{now:%Y%m%d}.txt"
        try:
            paths.append(fetcher.fetch(ndbc_realtime_url(station_id), dest, validate=text_starting_with("#YY")).path)
            unarchived = [m for m in unarchived if m not in recent]
        except FileNotFoundError:
            pass
    for m in unarchived:
        log.warning("NDBC %s: no annual, monthly or realtime file for %s", station_id, m)
    if not paths:
        raise FileNotFoundError(f"NDBC {station_id}: no annual, monthly or realtime stdmet files for {list(years)}")
    return paths


def read_ndbc(paths, station_id: str, station_class: str) -> pd.DataFrame:
    frames = []
    for p in paths:
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8", errors="replace") as fh:
            frames.append(parse_ndbc_stdmet(fh.read(), station_id, station_class=station_class, raw_file=p.name))
    return pd.concat(frames, ignore_index=True) if frames else _frame({}, 0)


# ---------------------------------------------------------------------------
# NOAA CO-OPS
# ---------------------------------------------------------------------------
def parse_coops_wind(data: dict, station_id: str, *, raw_file: str = "") -> pd.DataFrame:
    """Parse a CO-OPS ``product=wind`` JSON response (metric, GMT)."""
    if "error" in data:
        raise ValueError(f"CO-OPS {station_id}: {data['error']}")
    rows = data.get("data", [])
    n = len(rows)
    t = pd.to_datetime([r.get("t") for r in rows])

    def num(key):
        return pd.to_numeric(pd.Series([r.get(key, "") for r in rows], dtype=object).replace("", np.nan),
                             errors="coerce").to_numpy(dtype=float)

    s, d, g = num("s"), num("d"), num("g")
    missing = _join_missing(np.where(np.isnan(s), "speed=blank", ""), np.where(np.isnan(d), "direction=blank", ""),
                            np.where(np.isnan(g), "gust=blank", ""))
    calm = s == 0
    return _frame({
        "source": ["coops"] * n, "station_id": [station_id] * n, "time": t,
        "wind_speed": s, "wind_direction": np.where(calm, np.nan, d), "wind_gust": g,
        "averaging": ["not stated by the CO-OPS API"] * n,
        "source_qc": [f"X,R={r.get('f', '')}" for r in rows],
        "missing": missing, "calm": calm, "raw_file": [raw_file] * n,
    }, n)


def download_coops(cfg: HurricaneConfig, fetcher: Fetcher, station_id: str,
                   period: tuple[pd.Timestamp, pd.Timestamp]) -> list[Path]:
    """Request the period in chunks of ``chunk_days`` (the API caps request length)."""
    s = cfg.source_settings.get("coops", {})
    chunk = pd.Timedelta(days=int(s.get("chunk_days", 30)))
    start, end = period
    paths = []
    while start <= end:
        stop = min(start + chunk - pd.Timedelta(minutes=1), end)
        params = {"product": "wind", "station": station_id, "begin_date": f"{start:%Y%m%d %H:%M}",
                  "end_date": f"{stop:%Y%m%d %H:%M}", "time_zone": "gmt", "units": "metric", "format": "json",
                  "application": "sea_grant_wind_data"}
        dest = cfg.raw_dir / "obs" / "coops" / f"{station_id}_{start:%Y%m%d%H%M}_{stop:%Y%m%d%H%M}.json"
        try:
            paths.append(fetcher.fetch(s["data_url"], dest, params=params, validate=valid_json).path)
        except ValueError as err:
            if "No data was found" in str(err):
                log.warning("CO-OPS %s: no wind data %s..%s", station_id, start, stop)
            else:
                raise
        start = stop + pd.Timedelta(minutes=1)
    return paths


def read_coops(paths, station_id: str) -> pd.DataFrame:
    frames = [parse_coops_wind(json.loads(Path(p).read_text(encoding="utf-8")), station_id, raw_file=Path(p).name)
              for p in paths]
    return pd.concat(frames, ignore_index=True) if frames else _frame({}, 0)


# ---------------------------------------------------------------------------
# NCEI GHCNh
# ---------------------------------------------------------------------------
GHCNH_WIND = ("wind_direction", "wind_speed", "wind_gust")


def parse_ghcnh(path_or_text, station_id: str, *, raw_file: str = "",
                period: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> pd.DataFrame:
    """Parse the wind columns of a GHCNh ``.psv`` station file.

    Direction ``999`` or measurement code ``C`` means calm; ``V`` means
    variable (direction set to NaN, speed kept). Quality, measurement,
    report-type and source codes are kept verbatim in ``source_qc``.
    """
    usecols = ["DATE"] + [f"{v}{suffix}" for v in GHCNH_WIND
                          for suffix in ("", "_Measurement_Code", "_Quality_Code", "_Report_Type", "_Source_Code")]
    src = io.StringIO(path_or_text) if isinstance(path_or_text, str) and "\n" in path_or_text else path_or_text
    df = pd.read_csv(src, sep="|", usecols=lambda c: c in usecols, dtype=str, keep_default_na=False)
    missing_cols = sorted(set(usecols) - set(df.columns))
    if missing_cols:
        raise ValueError(f"GHCNh {station_id}: columns missing: {missing_cols}")
    df["time"] = pd.to_datetime(df["DATE"], format="%Y-%m-%dT%H:%M:%S")
    if period is not None:
        df = df[(df["time"] >= period[0]) & (df["time"] <= period[1])].reset_index(drop=True)

    speed = pd.to_numeric(df["wind_speed"], errors="coerce").to_numpy(dtype=float)
    direction = pd.to_numeric(df["wind_direction"], errors="coerce").to_numpy(dtype=float)
    gust = pd.to_numeric(df["wind_gust"], errors="coerce").to_numpy(dtype=float)
    code = df["wind_speed_Measurement_Code"].str.strip().to_numpy()
    dcode = df["wind_direction_Measurement_Code"].str.strip().to_numpy()

    calm = (code == "C") | (dcode == "C") | (speed == 0)
    variable = (dcode == "V") | (code == "V")
    dir_missing = np.isnan(direction) | (direction == 999)
    direction = np.where(calm | variable | dir_missing, np.nan, direction)
    speed = np.where(calm & np.isnan(speed), 0.0, speed)

    missing = _join_missing(
        np.where(np.isnan(speed), "speed=blank", ""),
        np.where(dir_missing & ~calm & ~variable, "direction=blank/999", np.where(variable, "direction=variable", "")),
        np.where(np.isnan(gust), "gust=not reported", ""),
    )
    qc = ("speed_q=" + df["wind_speed_Quality_Code"] + ",dir_q=" + df["wind_direction_Quality_Code"]
          + ",gust_q=" + df["wind_gust_Quality_Code"] + ",meas=" + df["wind_speed_Measurement_Code"]
          + ",report=" + df["wind_speed_Report_Type"] + ",src=" + df["wind_speed_Source_Code"])
    averaging = np.where(code == "H", "5-min mean", np.where(code == "R", "60-min mean", np.where(
        code == "T", "180-min mean", "per originating report type (see source_qc)")))
    n = len(df)
    return _frame({
        "source": ["ghcnh"] * n, "station_id": [station_id] * n, "time": df["time"],
        "wind_speed": speed, "wind_direction": direction, "wind_gust": gust, "averaging": averaging,
        "source_qc": qc.to_numpy(), "missing": missing, "calm": calm, "raw_file": [raw_file] * n,
    }, n)


def download_ghcnh(cfg: HurricaneConfig, fetcher: Fetcher, station_id: str, years) -> list[Path]:
    s = cfg.source_settings.get("ghcnh", {})
    paths = []
    for year in years:
        url = s["data_url"].format(year=year, id=station_id)
        dest = cfg.raw_dir / "obs" / "ghcnh" / f"GHCNh_{station_id}_{year}.psv"
        paths.append(fetcher.fetch(url, dest, validate=text_starting_with("STATION|")).path)
    return paths


def read_ghcnh(paths, station_id: str, period) -> pd.DataFrame:
    frames = [parse_ghcnh(p, station_id, raw_file=Path(p).name, period=period) for p in paths]
    return pd.concat(frames, ignore_index=True) if frames else _frame({}, 0)


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------
def download_and_read(cfg: HurricaneConfig, fetcher: Fetcher, stations: pd.DataFrame,
                      period: tuple[pd.Timestamp, pd.Timestamp]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch and parse every selected station.

    Returns ``(observations within the period, availability log)``. A
    station whose data cannot be fetched is logged with the reason; it is not
    silently dropped.
    """
    years = sorted({period[0].year, period[1].year})
    frames, status = [], []
    for row in stations.itertuples(index=False):
        entry = {"key": row.key, "source": row.source, "station_id": row.station_id}
        try:
            if row.source == "ndbc":
                obs = read_ndbc(download_ndbc(cfg, fetcher, row.station_id, years, period), row.station_id,
                                row.station_class)
            elif row.source == "coops":
                obs = read_coops(download_coops(cfg, fetcher, row.station_id, period), row.station_id)
            elif row.source == "ghcnh":
                obs = read_ghcnh(download_ghcnh(cfg, fetcher, row.station_id, years), row.station_id, period)
            else:
                raise ValueError(f"unknown source {row.source}")
        except FileNotFoundError as err:
            status.append({**entry, "status": "not available", "detail": str(err), "n_records": 0})
            continue
        except Exception as err:
            log.error("%s: %s", row.key, err)
            status.append({**entry, "status": "failed", "detail": str(err), "n_records": 0})
            continue
        obs = obs[(obs["time"] >= period[0]) & (obs["time"] <= period[1])]
        n_speed = int(obs["wind_speed"].notna().sum())
        state = "ok" if n_speed else ("no valid wind in period" if len(obs) else "no data in period")
        status.append({**entry, "status": state, "detail": "", "n_records": len(obs), "n_with_speed": n_speed})
        frames.append(obs)
    data = pd.concat(frames, ignore_index=True) if frames else _frame({}, 0)
    return data, pd.DataFrame(status)
