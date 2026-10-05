"""Observed 10-m wind at NDBC, NOAA CO-OPS and NCEI GHCNh stations.

Fetches the station metadata and wind records for a list of ``source:id`` keys and a UTC period,
applies quality control and adjusts the sustained speed to 10 m. Same rules as the gfs-run-vs-obs
skill of wind_comparison_bundle.zip, so the statistics match it.

Sources
-------
NDBC     station table and station page (position, type, site elevation, anemometer height);
         stdmet data from the annual file, else the monthly files, else the realtime2 file (last 45 days).
         WSPD is an 8-min mean on buoys and a 2-min mean at C-MAN stations.
CO-OPS   met station list and sensors.json (wind-sensor height above site, site elevation above MSL);
         6-min product=wind, requested in chunks of at most 30 days.
GHCNh    station list (position, elevation; no anemometer height); hourly .psv by year.

Quality control (nothing is deleted; ``qc_pass`` marks usable records)
----------------------------------------------------------------------
Blocking: speed missing, speed < 0 or > 90 m/s, direction outside 0-360, provider flag (CO-OPS X/R;
GHCNh letter codes and legacy suspect/erroneous codes), duplicate (station, time).

Height adjustment (neutral log law)
-----------------------------------
U(10) = U(z) ln(10/z0) / ln(z/z0), sustained speed only. z: buoys, anemometer height above sea level;
C-MAN and CO-OPS, site elevation above MSL plus anemometer height above the site. z0 by station class:
offshore 0.0002, coastal 0.005, land 0.03 m (WMO-No. 8). GHCNh airports publish no anemometer height,
so they are not adjusted (``adj_flag = height_unknown``) and must be labelled "sensor height".

Raw downloads are kept unchanged under ``<obs_dir>/raw/`` and reused on later runs.
"""
from __future__ import annotations

import gzip
import html
import io
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

USER_AGENT = "richamp-support-floodwater/wind_validation (python-requests)"
TARGET_HEIGHT_M = 10.0
Z0_M = {"offshore": 0.0002, "coastal": 0.005, "land": 0.03}
MAX_SPEED = 90.0  # m/s
AVERAGE_MINUTES = 60
AVERAGE_MIN_COVERAGE = 0.5
MIN_SPEED_FOR_DIRECTION = 1.0  # m/s; direction statistics use only pairs with both speeds at least this
FT_TO_M = 0.3048

NDBC_TABLE_URL = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"
NDBC_PAGE_URL = "https://www.ndbc.noaa.gov/station_page.php?station={id}"
NDBC_ANNUAL_URL = "https://www.ndbc.noaa.gov/data/historical/stdmet/{id}h{year}.txt.gz"
NDBC_REALTIME_URL = "https://www.ndbc.noaa.gov/data/realtime2/{id}.txt"
NDBC_MONTH_CODES = "123456789abc"  # monthly file names use 1-9, a, b, c for Jan-Dec
NDBC_REALTIME_DAYS = 45
COOPS_STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=met"
COOPS_SENSORS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{id}/sensors.json"
COOPS_DATA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
COOPS_CHUNK_DAYS = 30
GHCNH_LIST_URL = ("https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/"
                  "ghcnh-station-list.csv")
GHCNH_DATA_URL = ("https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/"
                  "{year}/psv/GHCNh_{id}_{year}.psv")

OBS_COLUMNS = ("source", "station_id", "time", "wind_speed", "wind_direction", "wind_gust", "averaging",
               "source_qc", "missing", "calm", "raw_file")


# ---------------------------------------------------------------- downloads
class Fetcher:
    """Cached GET: a file already on disk is reused; otherwise downloaded with retries and checked."""

    def __init__(self, attempts=6, backoff_s=2.0, timeout=(10.0, 180.0), interval_s=0.25):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.attempts, self.backoff_s, self.timeout, self.interval_s = attempts, backoff_s, timeout, interval_s

    def fetch(self, url, dest: Path, params=None, validate=None) -> Path:
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        err = None
        for attempt in range(self.attempts):
            time.sleep(self.interval_s)
            try:
                with self.session.get(url, params=params, timeout=self.timeout, stream=True) as r:
                    if r.status_code == 404:
                        raise FileNotFoundError(f"404 Not Found: {url}")
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise requests.HTTPError(f"HTTP {r.status_code} for {url}")
                    r.raise_for_status()
                    with part.open("wb") as fh:
                        for block in r.iter_content(1 << 16):
                            fh.write(block)
                if validate:
                    validate(part)
                os.replace(part, dest)
                return dest
            except FileNotFoundError:
                raise
            except requests.RequestException as e:  # connection, timeout, broken transfer, 429/5xx
                err = e
                part.unlink(missing_ok=True)
                if attempt + 1 < self.attempts:
                    delay = self.backoff_s * 2 ** attempt
                    print(f"  retry {attempt + 1}/{self.attempts - 1} in {delay:.0f}s: {url} ({e})", flush=True)
                    time.sleep(delay)
            except ValueError:
                part.unlink(missing_ok=True)
                raise
        raise RuntimeError(f"giving up on {url} after {self.attempts} attempts: {err}")


def non_empty(path: Path) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"{path.name} is empty")


def valid_gzip(path: Path) -> None:
    non_empty(path)
    with gzip.open(path, "rb") as fh:
        while fh.read(1 << 20):
            pass


def valid_json(path: Path) -> None:
    non_empty(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "error" in data:
        raise ValueError(f"service returned an error: {data['error']}")


def starts_with(prefix: str):
    def check(path: Path) -> None:
        non_empty(path)
        with path.open("rb") as fh:
            if fh.read(len(prefix.encode())).decode("utf-8", "replace") != prefix:
                raise ValueError(f"{path.name} does not start with {prefix!r}")
    return check


# ---------------------------------------------------------------- station metadata
_NDBC_LOC = re.compile(r"(-?\d+(?:\.\d+)?)\s*([NS])\s+(-?\d+(?:\.\d+)?)\s*([EW])")


def _ndbc_table(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        f = line.split("|")
        if line.startswith("#") or len(f) < 7:
            continue
        m = _NDBC_LOC.search(html.unescape(f[6]))
        if m:
            out[f[0].strip().upper()] = {
                "name": html.unescape(f[4].strip()), "platform_type": f[2].strip(), "owner": f[1].strip(),
                "lat": float(m.group(1)) * (1 if m.group(2) == "N" else -1),
                "lon": float(m.group(3)) * (1 if m.group(4) == "E" else -1)}
    return out


def _ndbc_page(raw_html: str) -> dict:
    """Site elevation and anemometer height as stated on an NDBC station page."""
    text = re.sub(r"<[^>]+>", "\n", raw_html)
    text = "\n".join(ln.strip() for ln in html.unescape(text).splitlines() if ln.strip())
    out = {"elevation_m": np.nan, "elevation_ref": "", "sensor_height_m": np.nan, "sensor_height_ref": ""}
    m = re.search(r"Site elevation:\s*\n?\s*([^\n]+)", text)
    if m:
        value = m.group(1).strip()
        if value.lower().startswith("sea level"):
            out["elevation_m"], out["elevation_ref"] = 0.0, "MSL (NDBC: 'sea level')"
        elif mm := re.match(r"(-?\d+(?:\.\d+)?)\s*m\s+(.*)", value):
            out["elevation_m"], out["elevation_ref"] = float(mm.group(1)), mm.group(2).strip()
    m = re.search(r"Anemometer height:\s*\n?\s*(-?\d+(?:\.\d+)?)\s*m\s+([^\n]+)", text)
    if m:
        out["sensor_height_m"], out["sensor_height_ref"] = float(m.group(1)), m.group(2).strip()
    return out


def _coops_sensors(data: dict) -> dict:
    """Wind-sensor height (above site) and site elevation (above MSL) from sensors.json."""
    factor = FT_TO_M if str(data.get("units", "feet")).lower().startswith("f") else 1.0
    out = {"elevation_m": np.nan, "elevation_ref": "", "sensor_height_m": np.nan, "sensor_height_ref": ""}
    for s in data.get("sensors", []):
        if s.get("elevation") is None:
            continue
        name, ref = str(s.get("name", "")).lower(), str(s.get("refdatum", ""))
        if s.get("sensorID") == "site" or name == "site":
            out["elevation_m"], out["elevation_ref"] = float(s["elevation"]) * factor, ref
        elif name == "wind":
            out["sensor_height_m"] = float(s["elevation"]) * factor
            out["sensor_height_ref"] = f"above {ref.lower()}" if ref else ""
    return out


def station_metadata(keys, raw: Path, fetcher: Fetcher) -> pd.DataFrame:
    """One row per requested ``source:id`` key found in its network's station list."""
    rows, lists = [], {}
    for key in keys:
        source, sid = key.split(":", 1)
        row = {"key": key, "source": source, "station_id": sid, "elevation_m": np.nan, "elevation_ref": "",
               "sensor_height_m": np.nan, "sensor_height_ref": ""}
        try:
            if source == "ndbc":
                if "ndbc" not in lists:
                    p = fetcher.fetch(NDBC_TABLE_URL, raw / "stations/ndbc/station_table.txt", validate=starts_with("#"))
                    lists["ndbc"] = _ndbc_table(p.read_text(encoding="utf-8", errors="replace"))
                info = lists["ndbc"][sid.upper()]
                row |= info | {"station_class": "offshore" if "buoy" in info["platform_type"].lower() else "coastal"}
                try:
                    p = fetcher.fetch(NDBC_PAGE_URL.format(id=sid.lower()),
                                      raw / f"stations/ndbc/pages/{sid.lower()}.html", validate=non_empty)
                    row |= _ndbc_page(p.read_text(encoding="utf-8", errors="replace"))
                except (FileNotFoundError, RuntimeError, ValueError) as err:
                    print(f"  {key}: station page unavailable ({err})")
            elif source == "coops":
                if "coops" not in lists:
                    p = fetcher.fetch(COOPS_STATIONS_URL, raw / "stations/coops/stations_met.json", validate=valid_json)
                    lists["coops"] = {str(s["id"]): s for s in json.loads(p.read_text(encoding="utf-8"))["stations"]}
                s = lists["coops"][sid]
                row |= {"name": s.get("name", ""), "lat": float(s["lat"]), "lon": float(s["lng"]),
                        "platform_type": "CO-OPS met station", "owner": "NOAA CO-OPS", "station_class": "coastal"}
                try:
                    p = fetcher.fetch(COOPS_SENSORS_URL.format(id=sid), raw / f"stations/coops/sensors/{sid}.json",
                                      validate=valid_json)
                    row |= _coops_sensors(json.loads(p.read_text(encoding="utf-8")))
                except (FileNotFoundError, RuntimeError, ValueError) as err:
                    print(f"  {key}: sensors.json unavailable ({err})")
            elif source == "ghcnh":
                if "ghcnh" not in lists:
                    p = fetcher.fetch(GHCNH_LIST_URL, raw / "stations/ghcnh/ghcnh-station-list.csv",
                                      validate=starts_with("GHCN_ID"))
                    df = pd.read_csv(p, dtype=str, keep_default_na=False)
                    df.columns = [c.strip() for c in df.columns]
                    lists["ghcnh"] = df.set_index(df["GHCN_ID"].str.strip())
                s = lists["ghcnh"].loc[sid]
                icao = s["ICAO"].strip()
                elev = pd.to_numeric(s["ELEVATION"], errors="coerce")
                row |= {"name": s["NAME"].strip(), "lat": float(s["LATITUDE"]), "lon": float(s["LONGITUDE"]),
                        "platform_type": f"surface (ICAO {icao})" if icao else "surface", "owner": "NCEI GHCNh",
                        "station_class": "land", "elevation_m": elev if elev > -999 else np.nan,
                        "elevation_ref": "MSL (GHCNh station list)" if elev > -999 else ""}
            else:
                raise KeyError(f"unknown source {source!r}")
        except KeyError as err:
            print(f"  {key}: not in the {source} station list ({err}); left out")
            continue
        rows.append(row)
    st = pd.DataFrame(rows)
    st["lon"] = (st["lon"] + 180.0) % 360.0 - 180.0
    return st


# ---------------------------------------------------------------- observations
def _frame(cols: dict, n: int) -> pd.DataFrame:
    df = pd.DataFrame({c: cols.get(c, [""] * n) for c in OBS_COLUMNS})
    df["time"] = pd.to_datetime(df["time"])
    for c in ("wind_speed", "wind_direction", "wind_gust"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["calm"] = df["calm"].astype(bool)
    return df


def _join(*parts) -> np.ndarray:
    return np.array([";".join(p for p in items if p) for items in zip(*parts)], dtype=object)


NDBC_MISSING = {"WDIR": 999.0, "WSPD": 99.0, "GST": 99.0}


def parse_ndbc(text: str, sid: str, station_class: str, raw_file: str) -> pd.DataFrame:
    """NDBC stdmet text (annual, monthly or realtime2)."""
    lines = text.splitlines()
    header = next((ln for ln in lines if ln.startswith("#YY") or ln.startswith("YY")), None)
    if header is None:
        raise ValueError(f"NDBC {sid}: no '#YY' header line")
    names = header.lstrip("#").split()
    body = "\n".join(ln for ln in lines if ln.strip() and not ln.startswith("#") and not ln.startswith("YY"))
    df = pd.read_csv(io.StringIO(body), sep=r"\s+", header=None, names=names, dtype=float, na_values=["MM"])
    year = df["YY" if "YY" in df.columns else names[0]].astype(int)
    year = np.where(year < 100, year + 1900, year)
    t = pd.to_datetime(dict(year=year, month=df["MM"].astype(int), day=df["DD"].astype(int),
                            hour=df["hh"].astype(int), minute=df["mm"].astype(int) if "mm" in df.columns else 0))
    miss, val = [], {}
    for col, k in (("WSPD", "speed"), ("WDIR", "direction"), ("GST", "gust")):
        v = df[col].to_numpy(dtype=float)
        mm = np.isnan(v)
        bad = mm | (v >= NDBC_MISSING[col])
        miss.append(np.where(mm, f"{k}=MM", np.where(bad, f"{k}={NDBC_MISSING[col]:g}", "")))
        val[k] = np.where(bad, np.nan, v)
    calm = val["speed"] == 0
    n = len(df)
    avg = "8-min mean (NDBC buoy)" if station_class == "offshore" else "2-min mean (NDBC land station)"
    return _frame({"source": ["ndbc"] * n, "station_id": [sid] * n, "time": t, "wind_speed": val["speed"],
                   "wind_direction": np.where(calm, np.nan, val["direction"]), "wind_gust": val["gust"],
                   "averaging": [avg] * n, "source_qc": [""] * n, "missing": _join(*miss), "calm": calm,
                   "raw_file": [raw_file] * n}, n)


def fetch_ndbc(sid, station_class, start, end, raw: Path, fetcher: Fetcher) -> pd.DataFrame:
    """Annual files; monthly files for a year without one; realtime2 for recent months not archived yet."""
    paths, unarchived = [], []
    for year in sorted({start.year, end.year}):
        try:
            paths.append(fetcher.fetch(NDBC_ANNUAL_URL.format(id=sid.lower(), year=year),
                                       raw / f"obs/ndbc/{sid.lower()}h{year}.txt.gz", validate=valid_gzip))
            continue
        except FileNotFoundError:
            pass
        for m in pd.period_range(max(start, pd.Timestamp(year=year, month=1, day=1)),
                                 min(end, pd.Timestamp(year=year, month=12, day=31)), freq="M"):
            name = f"{sid.lower()}{NDBC_MONTH_CODES[m.month - 1]}{m.year}.txt.gz"
            url = f"https://www.ndbc.noaa.gov/data/stdmet/{m.start_time:%b}/{name}"
            try:
                paths.append(fetcher.fetch(url, raw / "obs/ndbc" / name, validate=valid_gzip))
            except FileNotFoundError:
                unarchived.append(m)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    if any(m.end_time >= now - pd.Timedelta(days=NDBC_REALTIME_DAYS) for m in unarchived):
        try:  # changes every 10 min: the cache name carries the retrieval date
            paths.append(fetcher.fetch(NDBC_REALTIME_URL.format(id=sid.upper()),
                                       raw / f"obs/ndbc/realtime2/{sid.lower()}_{now:%Y%m%d}.txt",
                                       validate=starts_with("#YY")))
        except FileNotFoundError:
            pass
    if not paths:
        raise FileNotFoundError(f"NDBC {sid}: no annual, monthly or realtime stdmet file")
    frames = []
    for p in paths:  # archive files first: the duplicate rule then keeps the archived value
        with (gzip.open if p.suffix == ".gz" else open)(p, "rt", encoding="utf-8", errors="replace") as fh:
            frames.append(parse_ndbc(fh.read(), sid, station_class, p.name))
    return pd.concat(frames, ignore_index=True)


def parse_coops(data: dict, sid: str, raw_file: str) -> pd.DataFrame:
    rows = data.get("data", [])
    n = len(rows)

    def num(k):
        return pd.to_numeric(pd.Series([r.get(k, "") for r in rows], dtype=object).replace("", np.nan),
                             errors="coerce").to_numpy(dtype=float)

    s, d, g = num("s"), num("d"), num("g")
    calm = s == 0
    return _frame({"source": ["coops"] * n, "station_id": [sid] * n, "time": pd.to_datetime([r.get("t") for r in rows]),
                   "wind_speed": s, "wind_direction": np.where(calm, np.nan, d), "wind_gust": g,
                   "averaging": ["not stated by the CO-OPS API"] * n,
                   "source_qc": [f"X,R={r.get('f', '')}" for r in rows],
                   "missing": _join(np.where(np.isnan(s), "speed=blank", ""), np.where(np.isnan(d), "direction=blank", ""),
                                    np.where(np.isnan(g), "gust=blank", "")),
                   "calm": calm, "raw_file": [raw_file] * n}, n)


def fetch_coops(sid, start, end, raw: Path, fetcher: Fetcher) -> pd.DataFrame:
    frames, t0 = [], start
    while t0 <= end:
        t1 = min(t0 + pd.Timedelta(days=COOPS_CHUNK_DAYS) - pd.Timedelta(minutes=1), end)
        params = {"product": "wind", "station": sid, "begin_date": f"{t0:%Y%m%d %H:%M}", "end_date": f"{t1:%Y%m%d %H:%M}",
                  "time_zone": "gmt", "units": "metric", "format": "json", "application": "sea_grant_wind_data"}
        dest = raw / f"obs/coops/{sid}_{t0:%Y%m%d%H%M}_{t1:%Y%m%d%H%M}.json"
        try:
            p = fetcher.fetch(COOPS_DATA_URL, dest, params=params, validate=valid_json)
            frames.append(parse_coops(json.loads(p.read_text(encoding="utf-8")), sid, p.name))
        except ValueError as err:
            if "No data was found" not in str(err):
                raise
            print(f"  CO-OPS {sid}: no wind data {t0} .. {t1}")
        t0 = t1 + pd.Timedelta(minutes=1)
    return pd.concat(frames, ignore_index=True) if frames else _frame({}, 0)


def parse_ghcnh(path: Path, sid: str, start, end) -> pd.DataFrame:
    """Wind columns of a GHCNh .psv file. Direction 999 or code C = calm; V = variable (direction NaN)."""
    names = ("wind_direction", "wind_speed", "wind_gust")
    use = ["DATE"] + [f"{v}{s}" for v in names
                      for s in ("", "_Measurement_Code", "_Quality_Code", "_Report_Type", "_Source_Code")]
    df = pd.read_csv(path, sep="|", usecols=lambda c: c in use, dtype=str, keep_default_na=False)
    df["time"] = pd.to_datetime(df["DATE"], format="%Y-%m-%dT%H:%M:%S")
    df = df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)
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
    qc = ("speed_q=" + df["wind_speed_Quality_Code"] + ",dir_q=" + df["wind_direction_Quality_Code"]
          + ",gust_q=" + df["wind_gust_Quality_Code"] + ",meas=" + df["wind_speed_Measurement_Code"]
          + ",report=" + df["wind_speed_Report_Type"] + ",src=" + df["wind_speed_Source_Code"])
    n = len(df)
    return _frame({"source": ["ghcnh"] * n, "station_id": [sid] * n, "time": df["time"], "wind_speed": speed,
                   "wind_direction": direction, "wind_gust": gust,
                   "averaging": np.where(code == "H", "5-min mean", np.where(code == "R", "60-min mean", np.where(
                       code == "T", "180-min mean", "per originating report type (see source_qc)"))),
                   "source_qc": qc.to_numpy(),
                   "missing": _join(np.where(np.isnan(speed), "speed=blank", ""),
                                    np.where(dir_missing & ~calm & ~variable, "direction=blank/999",
                                             np.where(variable, "direction=variable", "")),
                                    np.where(np.isnan(gust), "gust=not reported", "")),
                   "calm": calm, "raw_file": [path.name] * n}, n)


def fetch_ghcnh(sid, start, end, raw: Path, fetcher: Fetcher) -> pd.DataFrame:
    frames = []
    for year in sorted({start.year, end.year}):
        p = fetcher.fetch(GHCNH_DATA_URL.format(year=year, id=sid), raw / f"obs/ghcnh/GHCNh_{sid}_{year}.psv",
                          validate=starts_with("STATION|"))
        frames.append(parse_ghcnh(p, sid, start, end))
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------- quality control
_GHCNH_GROUP_A = {"220", "221", "222", "223", "347", "348"}
_GHCNH_BAD_LEGACY = {"A": {"2", "3", "5"}, "B": {"2", "3", "6", "7"}}


def _provider_flagged(source: str, qc: str) -> bool:
    if source == "coops":  # X = maximum exceeded, R = rate of change exceeded
        return any(f.strip() == "1" for f in qc.partition("=")[2].split(","))
    if source == "ghcnh":  # NCEI GHCNh v1.1.0, Table 3
        fields = dict(p.split("=", 1) for p in qc.split(",") if "=" in p)
        code = fields.get("speed_q", "").strip()
        if not code:
            return False
        if code.isalpha() and code.islower():
            return True
        return code in _GHCNH_BAD_LEGACY["A" if fields.get("src", "").strip() in _GHCNH_GROUP_A else "B"]
    return False


def quality_control(obs: pd.DataFrame) -> pd.DataFrame:
    df = obs.sort_values(["source", "station_id", "time"], kind="stable").reset_index(drop=True)
    s, d, g = (df[c].to_numpy(dtype=float) for c in ("wind_speed", "wind_direction", "wind_gust"))
    calm = df["calm"].to_numpy(dtype=bool)
    checks = [
        (np.isnan(s), "speed_missing", True),
        (np.isfinite(s) & ((s < 0) | (s > MAX_SPEED)), "speed_range", True),
        (np.isfinite(d) & ((d < 0) | (d > 360)), "direction_range", True),
        (np.array([_provider_flagged(a, str(b)) for a, b in zip(df["source"], df["source_qc"].fillna(""))],
                  dtype=bool), "provider_flag", True),
        (df.duplicated(["source", "station_id", "time"], keep="first").to_numpy(), "duplicate", True),
        (calm, "calm", False),
        (np.isnan(d) & ~calm, "direction_missing", False),
        (np.isnan(g), "gust_missing", False),
        (np.isfinite(g) & np.isfinite(s) & (g < s), "gust_lt_speed", False),
    ]
    blocking = np.zeros(len(df), dtype=bool)
    flags = [[] for _ in range(len(df))]
    for mask, code, block in checks:
        for i in np.flatnonzero(mask):
            flags[i].append(code)
        if block:
            blocking |= mask
    df["qc_flags"] = [";".join(f) for f in flags]
    df["qc_pass"] = ~blocking
    df["wind_direction"] = np.where(np.isfinite(d) & (d == 360), 0.0, d)
    return df


# ---------------------------------------------------------------- height adjustment
def log_law_height(st) -> tuple[float, str]:
    """Height above the log-law surface for one station, and how it was obtained (NaN + reason if unknown)."""
    h, elev = float(st["sensor_height_m"]), float(st["elevation_m"])
    href, eref = str(st["sensor_height_ref"] or "").lower(), str(st["elevation_ref"] or "").lower()
    if not np.isfinite(h):
        return np.nan, "height_unknown"
    if h <= 0:
        return np.nan, "height_invalid"
    to_site = "site" in href
    to_msl = "msl" in href or "mean sea level" in href
    if st["station_class"] == "offshore":
        if to_msl or (to_site and np.isfinite(elev) and elev == 0 and "msl" in eref):
            return h, "anemometer above sea level (site = sea level)"
        return (h, "anemometer above site (buoy deck)") if to_site else (np.nan, "height_reference_unknown")
    if st["station_class"] == "coastal":
        if to_msl:
            return h, "anemometer above MSL"
        if not to_site:
            return np.nan, "height_reference_unknown"
        if np.isfinite(elev) and ("msl" in eref or "mean sea level" in eref):
            return elev + h, "site elevation above MSL + anemometer above site"
        return np.nan, "height_reference_unknown (site elevation not relative to MSL)"
    if to_site or "ground" in href:
        return h, "anemometer above ground"
    return np.nan, "height_reference_unknown"


def adjust_to_10m(obs: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    meta = pd.DataFrame([{"source": st["source"], "station_id": st["station_id"], **dict(zip(
        ("z_sensor_m", "z_rule"), log_law_height(st))), "z0_m": Z0_M[st["station_class"]]}
        for _, st in stations.iterrows()])
    out = obs.merge(meta, on=["source", "station_id"], how="left")
    out["z_target_m"] = TARGET_HEIGHT_M
    zs, z0 = out["z_sensor_m"].to_numpy(float), out["z0_m"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = np.log(TARGET_HEIGHT_M / z0) / np.log(zs / z0)
    rule = out["z_rule"].fillna("").to_numpy(dtype=object)
    flag = np.where(out["z_rule"].isna(), "station_metadata_missing", "adjusted")
    for code in ("height_unknown", "height_invalid", "height_reference_unknown"):
        flag = np.where([r.startswith(code) for r in rule], code, flag)
    flag = np.where((flag == "adjusted") & np.isfinite(zs) & (zs <= z0), "height_below_z0", flag)
    at_target = (flag == "adjusted") & np.isclose(zs, TARGET_HEIGHT_M)
    flag = np.where(at_target, "at_target_height", flag)
    ok = np.isin(flag, ("adjusted", "at_target_height"))
    out["height_factor"] = np.where(at_target, 1.0, np.where(ok, factor, np.nan))
    out["wind_speed_adj"] = np.where(ok, out["wind_speed"] * out["height_factor"], np.nan)
    out["adj_method"] = np.where(ok, "neutral_log_law", "none")
    out["adj_flag"] = flag
    out["key"] = out["source"] + ":" + out["station_id"]
    return out


# ---------------------------------------------------------------- averaging
def average_observations(obs: pd.DataFrame, minutes: int = AVERAGE_MINUTES,
                         min_coverage: float = AVERAGE_MIN_COVERAGE) -> pd.DataFrame:
    """Means of quality-passing observations over ``minutes`` centred on each whole interval.

    Speed (sensor height and 10 m) is a scalar mean, direction the speed-weighted vector mean, the
    gust the window maximum. A window needs ``min_coverage`` of the samples the station's own sampling
    interval implies, otherwise it is dropped.
    """
    good = obs[obs["qc_pass"]].sort_values(["source", "station_id", "time"]).copy()
    if good.empty:
        return good
    freq = pd.Timedelta(minutes=minutes)
    good["_bin"] = (good["time"] + freq / 2).dt.floor(freq)  # [t - w/2, t + w/2) -> t
    spd = good["wind_speed"].to_numpy(dtype=float)
    rad = np.deg2rad(good["wind_direction"].to_numpy(dtype=float))
    good["_u"], good["_v"] = -spd * np.sin(rad), -spd * np.cos(rad)
    step = good.groupby(["source", "station_id"])["time"].diff()
    good["_expected"] = freq / step.groupby([good["source"], good["station_id"]]).transform("median")
    g = good.groupby(["source", "station_id", "_bin"], sort=True)
    out = g.agg(wind_speed=("wind_speed", "mean"), wind_speed_adj=("wind_speed_adj", "mean"),
                wind_gust=("wind_gust", "max"), _u=("_u", "mean"), _v=("_v", "mean"),
                n_samples=("wind_speed", "count"), _expected=("_expected", "first"))
    meta = [c for c in ("z_sensor_m", "z0_m", "adj_flag", "station_class") if c in good.columns]
    out = out.join(g[meta].first()).reset_index().rename(columns={"_bin": "time"})
    out["coverage"] = out["n_samples"] / out["_expected"]
    out = out[out["coverage"] >= min_coverage].copy()
    out["wind_direction"] = np.where(np.hypot(out["_u"], out["_v"]) > 0,
                                     np.rad2deg(np.arctan2(-out["_u"], -out["_v"])) % 360.0, np.nan)
    return out.drop(columns=["_u", "_v", "_expected"]).reset_index(drop=True)


# ---------------------------------------------------------------- all together
def fetch_observations(keys, start, end, obs_dir: Path, attempts: int = 3) -> None:
    """Writes stations.csv, obs_availability.csv and observations.csv.gz (QC'd, adjusted) into ``obs_dir``.

    A station whose download breaks (NCEI's GHCNh files often do) is retried up to ``attempts`` times;
    finished downloads are reused.
    """
    raw, fetcher = obs_dir / "raw", Fetcher()
    stations = station_metadata(keys, raw, fetcher)
    frames, status = {}, {}
    for attempt in range(attempts):
        todo = [r for _, r in stations.iterrows() if status.get(r["key"], {}).get("status") in (None, "failed")]
        if not todo:
            break
        if attempt:
            print(f"retrying failed downloads: {', '.join(r['key'] for r in todo)}", flush=True)
        for st in todo:
            key, sid = st["key"], st["station_id"]
            print(f"  {key}", flush=True)
            try:
                if st["source"] == "ndbc":
                    obs = fetch_ndbc(sid, st["station_class"], start, end, raw, fetcher)
                elif st["source"] == "coops":
                    obs = fetch_coops(sid, start, end, raw, fetcher)
                else:
                    obs = fetch_ghcnh(sid, start, end, raw, fetcher)
            except FileNotFoundError as err:
                status[key] = {"key": key, "status": "not available", "detail": str(err), "n_records": 0}
                continue
            except Exception as err:  # noqa: BLE001 - one station must not stop the others
                print(f"  {key}: {err}", flush=True)
                status[key] = {"key": key, "status": "failed", "detail": str(err), "n_records": 0}
                continue
            obs = obs[(obs["time"] >= start) & (obs["time"] <= end)]
            n = int(obs["wind_speed"].notna().sum())
            status[key] = {"key": key, "status": "ok" if n else ("no valid wind in period" if len(obs) else
                                                                 "no data in period"),
                           "detail": "", "n_records": len(obs), "n_with_speed": n}
            frames[key] = obs
    obs = quality_control(pd.concat(frames.values(), ignore_index=True) if frames else _frame({}, 0))
    obs = adjust_to_10m(obs, stations)
    obs_dir.mkdir(parents=True, exist_ok=True)
    stations.to_csv(obs_dir / "stations.csv", index=False)
    pd.DataFrame(list(status.values())).to_csv(obs_dir / "obs_availability.csv", index=False)
    obs.to_csv(obs_dir / "observations.csv.gz", index=False)
    print(f"observations: {len(obs)} records from {obs['key'].nunique()} stations "
          f"({int(obs['qc_pass'].sum())} pass QC); height adjustment: {obs.groupby('adj_flag').size().to_dict()}")


def read_observations(obs_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(stations, observations) as written by fetch_observations."""
    st = pd.read_csv(obs_dir / "stations.csv", dtype={"station_id": str})
    obs = pd.read_csv(obs_dir / "observations.csv.gz", dtype={"station_id": str, "key": str}, parse_dates=["time"],
                      low_memory=False)
    obs["qc_pass"] = obs["qc_pass"].astype(str).eq("True")
    return st, obs
