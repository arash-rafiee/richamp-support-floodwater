#!/usr/bin/env python3
"""
Write raw GFS 10 m wind into a RICHAMP_wind.nc-style file (no roughness scaling).

Reads the generic-netcdf wind file produced by gfs_grib_to_wind_nc.py and writes
it with the exact structure scale_and_subset.py produces (reusing its NetcdfOutput
class): group /Main with time [minutes since 1990-01-01], time_unix, lon, lat,
spd [m/s] and dir [deg, meteorological, direction wind comes from].

By default the GFS 0.25-degree grid is kept as-is. With --grid, the wind is
bilinearly interpolated onto the lon/lat of that file (e.g. the RICHAMP
high-resolution grid NLCD_z0_RICHAMP_Reg_Grid.nc) so the extent and shape match
the scaled product exactly.

Usage:
    python gfs_grib_to_wind_nc.py gfs_wind/<YYYYMMDD>_<HH>z -o gfs_wind.nc
    python gfs_to_richamp_wind.py gfs_wind.nc -o RICHAMP_wind                 # GFS native grid
    python gfs_to_richamp_wind.py gfs_wind.nc -o RICHAMP_wind --grid NLCD_z0_RICHAMP_Reg_Grid.nc
"""
import argparse
import os
import sys

import netCDF4
import numpy
import scipy.interpolate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scale_and_subset import GenericNetcdf, NetcdfOutput  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wind", help="generic-netcdf wind file from gfs_grib_to_wind_nc.py")
    ap.add_argument("-o", default="RICHAMP_wind", help="output name without .nc (default RICHAMP_wind)")
    ap.add_argument("--grid", help="NetCDF file with lon/lat variables to interpolate onto (optional)")
    args = ap.parse_args()

    src = GenericNetcdf(args.wind)
    src_lon = src.grid().lon1d()
    src_lat = src.grid().lat1d()

    if args.grid:
        with netCDF4.Dataset(args.grid) as g:
            out_lon = numpy.array(g.variables["lon"][:], dtype=numpy.float64)
            out_lat = numpy.array(g.variables["lat"][:], dtype=numpy.float64)
        if (out_lon.min() < src_lon.min() or out_lon.max() > src_lon.max()
                or out_lat.min() < src_lat.min() or out_lat.max() > src_lat.max()):
            sys.exit(f"target grid ({out_lon.min():.2f}..{out_lon.max():.2f}, {out_lat.min():.2f}..{out_lat.max():.2f}) "
                     f"is not inside the GFS data ({src_lon.min():.2f}..{src_lon.max():.2f}, {src_lat.min():.2f}..{src_lat.max():.2f})")
    else:
        out_lon, out_lat = src_lon, src_lat

    out = NetcdfOutput(args.o, out_lon, out_lat)
    # NetcdfOutput stamps itself as scale_and_subset.py output; say what this really is.
    out._NetcdfOutput__nc.source = "gfs_to_richamp_wind.py (raw GFS 10 m wind, no roughness scaling)"
    out._NetcdfOutput__nc.wind_source = os.path.basename(args.wind)

    n = src.num_times()
    for i in range(n):
        w = src.get(i)
        u, v = w.u_velocity(), w.v_velocity()
        if args.grid:
            u = scipy.interpolate.RectBivariateSpline(src_lat, src_lon, u, kx=1, ky=1)(out_lat, out_lon)
            v = scipy.interpolate.RectBivariateSpline(src_lat, src_lon, v, kx=1, ky=1)(out_lat, out_lon)
        out.append(i, w.date(), u, v, None)
        print(f"  {i + 1}/{n} {w.date():%Y-%m-%d %H:%M} UTC  max {numpy.sqrt(u**2 + v**2).max():.1f} m/s", flush=True)
    out.close()
    src.close()
    print(f"Wrote {args.o}.nc: {n} times, {out_lat.size} x {out_lon.size} grid")


if __name__ == "__main__":
    main()
