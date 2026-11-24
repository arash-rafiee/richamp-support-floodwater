"""GFS 10 m wind time series at the RI NOAA stations for one cycle (f000-f120 hourly), from NOMADS.

Same NOMADS filter request as narragansett_wind_stats.py (u10/v10, RI subregion), bilinear to each station.
NOMADS keeps only the last ~10 days of cycles. For local checks and decks: it needs requests and cfgrib,
so it is not meant for the Floodwater environment (use netcdf_wind_series.py there).

Usage: python gfs_wind_series.py 20261124 12 [out_dir]
Writes <out_dir>/gfs_wind_<YYYYMMDDHH>.csv; GRIB files are cached in <out_dir>/gfs_cache/.
"""
import os
import sys
import time
import datetime as dt

import numpy as np
import pandas as pd
import requests
import xarray as xr

if len(sys.argv) not in (3, 4):
    sys.exit(__doc__)
YMD, HH = sys.argv[1], sys.argv[2]
HERE = os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else ".")
CACHE = os.path.join(HERE, "gfs_cache", f"{YMD}{HH}")
URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
REGION = dict(toplat=42.2, bottomlat=41.0, leftlon=-72.0, rightlon=-70.5)
STATIONS = [("Providence, RI", 41.807, -71.401), ("Conimicut Light, RI", 41.717, -71.345),
            ("Quonset Pt, RI", 41.587, -71.410), ("Newport, RI", 41.504, -71.326)]


def fetch(fhr):
    path = os.path.join(CACHE, f"f{fhr:03d}.grb2")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(CACHE, exist_ok=True)
    params = {"dir": f"/gfs.{YMD}/{HH}/atmos", "file": f"gfs.t{HH}z.pgrb2.0p25.f{fhr:03d}",
              "var_UGRD": "on", "var_VGRD": "on", "lev_10_m_above_ground": "on", "subregion": "", **REGION}
    for attempt in range(4):
        try:
            r = requests.get(URL, params=params, timeout=120)
            if r.status_code == 200 and r.content[:4] == b"GRIB":
                open(path, "wb").write(r.content)
                time.sleep(0.4)
                return path
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(5 * (attempt + 1))
    return None


t0 = dt.datetime.strptime(YMD + HH, "%Y%m%d%H")
rows = []
for fhr in range(0, 121):
    p = fetch(fhr)
    if p is None:
        print("missing f%03d" % fhr, flush=True)
        continue
    ds = xr.open_dataset(p, engine="cfgrib", backend_kwargs={"indexpath": ""})
    ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
    for name, lat, lon in STATIONS:
        q = ds.interp(latitude=lat, longitude=lon)
        u, v = float(q.u10), float(q.v10)
        rows.append((name, t0 + dt.timedelta(hours=fhr), fhr, u, v, np.hypot(u, v),
                     (np.degrees(np.arctan2(-u, -v)) + 360) % 360))   # direction wind comes FROM
    ds.close()
    if fhr % 12 == 0:
        print(f"f{fhr:03d} ok", flush=True)
out = os.path.join(HERE, f"gfs_wind_{YMD}{HH}.csv")
pd.DataFrame(rows, columns=["station", "time", "fhr", "u10", "v10", "speed_ms", "dir_from_deg"]).to_csv(out, index=False)
print(out)
