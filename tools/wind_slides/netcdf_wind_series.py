"""Station wind time series from a RICHAMP wind netCDF (group /Main: time_unix, lon, lat, spd, dir).
netCDF4 + numpy only (floodwater packages). Writes the same CSV columns as gfs_wind_series.py.
Usage: python netcdf_wind_series.py <RICHAMP_wind.nc> <out.csv>"""
import sys
import csv
from datetime import datetime, timezone

import numpy as np
import netCDF4

if len(sys.argv) != 3:
    sys.exit(__doc__)
NC, OUT = sys.argv[1], sys.argv[2]
STATIONS = [("Providence, RI", 41.807, -71.401), ("Conimicut Light, RI", 41.717, -71.345),
            ("Quonset Pt, RI", 41.587, -71.410), ("Newport, RI", 41.504, -71.326)]

with netCDF4.Dataset(NC) as ds:
    g = ds.groups["Main"] if "Main" in ds.groups else ds
    lon, lat = g.variables["lon"][:], g.variables["lat"][:]
    times = [datetime.fromtimestamp(int(s), tz=timezone.utc).replace(tzinfo=None) for s in g.variables["time_unix"][:]]
    t0 = times[0]
    rows = []
    for name, la, lo in STATIONS:
        ix, iy = int(np.abs(lon - lo).argmin()), int(np.abs(lat - la).argmin())
        spd = np.asarray(g.variables["spd"][:, iy, ix], dtype=float)
        dirf = np.asarray(g.variables["dir"][:, iy, ix], dtype=float)      # direction the wind comes FROM
        u, v = -spd * np.sin(np.deg2rad(dirf)), -spd * np.cos(np.deg2rad(dirf))
        print(f"{name}: grid point {lat[iy]:.4f}, {lon[ix]:.4f}  (station {la}, {lo})  "
              f"speed {spd.min():.1f}-{spd.max():.1f} m/s", flush=True)
        for t, s_, d_, uu, vv in zip(times, spd, dirf, u, v):
            rows.append([name, t.strftime("%Y-%m-%d %H:%M:%S"), int((t - t0).total_seconds() // 3600), uu, vv, s_, d_])

with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["station", "time", "fhr", "u10", "v10", "speed_ms", "dir_from_deg"])
    w.writerows(rows)
print(OUT)
