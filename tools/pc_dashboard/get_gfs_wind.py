#!/usr/bin/env python3
"""
Download GFS 0.25-degree wind forecasts (U/V components) for the next 4 days
from NOAA NOMADS, using the grib filter so only the wind fields are fetched.

Usage:
    python get_gfs_wind.py                      # global, 10 m wind, latest run
    python get_gfs_wind.py --region 45 35 -80 -65   # N S W E (lat/lon) subregion
    python get_gfs_wind.py --levels 10 80 100       # multiple heights (m AGL)
    python get_gfs_wind.py --netcdf                 # also merge into one NetCDF

For the RICHAMP pipeline use the gfs-roughness.nc box, then convert:
    python get_gfs_wind.py --region 43 40 -73 -70 --step 1
    python gfs_grib_to_wind_nc.py gfs_wind/<YYYYMMDD>_<HH>z -o gfs_wind.nc

Requires: requests  (pip install requests)
Optional for --netcdf: xarray cfgrib  (pip install xarray cfgrib)
"""
import argparse
import datetime as dt
import os
import sys
import time

import requests

BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
PROD = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"


def latest_cycle(max_hour):
    """Find the most recent GFS cycle whose final forecast hour is published."""
    now = dt.datetime.now(dt.timezone.utc)
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= dt.timedelta(hours=t.hour % 6)
    for _ in range(8):  # look back up to 2 days
        ymd, hh = t.strftime("%Y%m%d"), t.strftime("%H")
        url = f"{PROD}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{max_hour:03d}.idx"
        try:
            if requests.head(url, timeout=20).status_code == 200:
                return ymd, hh
        except requests.RequestException:
            pass
        t -= dt.timedelta(hours=6)
    sys.exit("Could not find a complete recent GFS cycle on NOMADS.")


def build_params(ymd, hh, fhr, levels, region):
    p = {
        "dir": f"/gfs.{ymd}/{hh}/atmos",
        "file": f"gfs.t{hh}z.pgrb2.0p25.f{fhr:03d}",
        "var_UGRD": "on",
        "var_VGRD": "on",
    }
    for lev in levels:
        p[f"lev_{lev}_m_above_ground"] = "on"
    if region:
        n, s, w, e = region
        p.update(subregion="", toplat=n, bottomlat=s, leftlon=w, rightlon=e)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--levels", type=int, nargs="+", default=[10],
                    help="heights above ground in m (e.g. 10 80 100)")
    ap.add_argument("--region", type=float, nargs=4, metavar=("N", "S", "W", "E"),
                    help="subregion bounds; longitudes may be -180..180")
    ap.add_argument("--step", type=int, default=1,
                    help="hours between forecast steps (GFS is hourly to 120 h)")
    ap.add_argument("--out", default="gfs_wind")
    ap.add_argument("--netcdf", action="store_true")
    args = ap.parse_args()

    max_hour = args.days * 24
    ymd, hh = latest_cycle(max_hour)
    outdir = os.path.join(args.out, f"{ymd}_{hh}z")
    os.makedirs(outdir, exist_ok=True)
    print(f"Using GFS run {ymd} {hh}Z, forecast hours 0-{max_hour} -> {outdir}")

    files = []
    for fhr in range(0, max_hour + 1, args.step):
        path = os.path.join(outdir, f"gfs_wind_f{fhr:03d}.grb2")
        files.append(path)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        params = build_params(ymd, hh, fhr, args.levels, args.region)
        for attempt in range(5):
            try:
                r = requests.get(BASE, params=params, timeout=120)
                if r.status_code == 200 and r.content[:4] == b"GRIB":
                    with open(path, "wb") as f:
                        f.write(r.content)
                    print(f"  f{fhr:03d} ok ({len(r.content)/1e6:.1f} MB)")
                    break
                print(f"  f{fhr:03d} HTTP {r.status_code}, retrying...")
            except requests.RequestException as e:
                print(f"  f{fhr:03d} error: {e}, retrying...")
            time.sleep(10 * (attempt + 1))
        else:
            print(f"  f{fhr:03d} FAILED")
        time.sleep(1)  # be polite: NOMADS blocks clients that hammer it

    if args.netcdf:
        import xarray as xr
        ds = xr.open_mfdataset(
            [f for f in files if os.path.exists(f)], engine="cfgrib",
            combine="nested", concat_dim="step",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround"}},
        )
        nc = os.path.join(outdir, "gfs_wind.nc")
        ds.to_netcdf(nc)
        print(f"Wrote {nc}")

    print("Done.")


if __name__ == "__main__":
    main()
