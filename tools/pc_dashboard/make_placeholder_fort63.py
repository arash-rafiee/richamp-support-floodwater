#!/usr/bin/env python3
"""
Build a placeholder RICHAMP_fort63.nc: the mesh, variables and attributes of an existing
RICHAMP_fort63.nc, but zeta = 0 everywhere on a new time axis.

Useful to give downstream consumers (dashboard) a structurally correct water level file
for a period that has no ADCIRC run yet, e.g. to pair with a RICHAMP_wind.nc.

Usage:
    python make_placeholder_fort63.py --template C:/path/RICHAMP_fort63.nc \
        --start "2026-09-23 12:00" --end "2026-09-27 12:00" [--step 1200] [-o RICHAMP_fort63.nc]
    python make_placeholder_fort63.py --template ref.nc --wind RICHAMP_wind.nc   # time span from the wind file

Requires: netCDF4 numpy
"""
import argparse
import datetime
import os

import netCDF4
import numpy

FILL = -99999.0


def parse_dt(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True, help="existing RICHAMP_fort63.nc to copy mesh and attributes from")
    ap.add_argument("--start", help="first time, 'YYYY-MM-DD HH:MM' UTC")
    ap.add_argument("--end", help="last time, 'YYYY-MM-DD HH:MM' UTC")
    ap.add_argument("--wind", help="RICHAMP_wind.nc; its first and last time are used when --start/--end are omitted")
    ap.add_argument("--step", type=int, default=1200, help="output interval in seconds (default 1200, as ADCIRC fort.63)")
    ap.add_argument("--value", type=float, default=0.0, help="constant water level to write (default 0)")
    ap.add_argument("-o", "--out", default="RICHAMP_fort63.nc")
    args = ap.parse_args()

    if args.wind and not (args.start and args.end):
        with netCDF4.Dataset(args.wind) as wnc:
            m = wnc["Main"]
            b = datetime.datetime(1990, 1, 1)
            t = m["time"][:]
            start = b + datetime.timedelta(minutes=float(t[0]))
            end = b + datetime.timedelta(minutes=float(t[-1]))
    else:
        start, end = parse_dt(args.start), parse_dt(args.end)
    nt = int((end - start).total_seconds() // args.step) + 1
    time = numpy.arange(nt, dtype=numpy.float64) * args.step
    time_unix = (start - datetime.datetime(1970, 1, 1)).total_seconds() + time
    base = start.strftime("%Y-%m-%d %H:%M:%S")

    src = netCDF4.Dataset(args.template, "r")
    nnode = len(src.dimensions["node"])
    nele = len(src.dimensions["nele"])
    print(f"template: {nnode} nodes, {nele} elements; new axis {nt} steps of {args.step}s from {start} to {end} UTC")

    if os.path.exists(args.out):
        os.remove(args.out)
    dst = netCDF4.Dataset(args.out, "w", format="NETCDF4")
    for a in src.ncattrs():
        if a != "_FillValue":
            dst.setncattr(a, src.getncattr(a))
    dst.setncattr("_FillValue", FILL)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S  00:00")
    tag = f"placeholder_zero_{start:%Y-%m-%d-%H}"
    dst.setncattr("description", tag)
    dst.setncattr("rundes", tag)
    dst.setncattr("title", tag)
    dst.setncattr("creation_date", stamp)
    dst.setncattr("modification_date", stamp)
    dst.setncattr("comments", f"PLACEHOLDER: zeta = {args.value} everywhere; mesh and attributes copied from "
                              f"{os.path.basename(args.template)} ({src.getncattr('description') if 'description' in src.ncattrs() else ''})")

    dst.createDimension("time", None)
    dst.createDimension("node", nnode)
    dst.createDimension("nele", nele)
    dst.createDimension("nvertex", 3)

    def make(name, dtype, dims, **kw):
        v = dst.createVariable(name, dtype, dims, **kw)
        for a in src[name].ncattrs():
            if a not in ("_FillValue", "units", "base_date"):
                v.setncattr(a, src[name].getncattr(a))
        return v

    v_time = make("time", "f8", ("time",))
    v_time.units = "seconds since " + base
    v_time.base_date = base
    v_x = make("x", "f8", ("node",), zlib=True, complevel=2); v_x.units = src["x"].units
    v_y = make("y", "f8", ("node",), zlib=True, complevel=2); v_y.units = src["y"].units
    v_el = make("element", "i4", ("nele", "nvertex"), zlib=True, complevel=2); v_el.units = src["element"].units
    v_dp = make("depth", "f8", ("node",), zlib=True, complevel=2); v_dp.units = src["depth"].units
    v_z = make("zeta", "f8", ("time", "node"), zlib=True, complevel=2, fill_value=FILL, chunksizes=(1, nnode)); v_z.units = src["zeta"].units
    v_tu = make("time_unix", "f8", ("time",))
    v_tu.units = "seconds since 1970-01-01 00:00:00"
    v_tu.base_date = "1970-01-01 00:00:00"

    v_time[:] = time
    v_tu[:] = time_unix
    v_x[:] = src["x"][:]
    v_y[:] = src["y"][:]
    v_el[:, :] = src["element"][:]
    v_dp[:] = src["depth"][:]
    row = numpy.full((1, nnode), args.value, dtype=numpy.float64)
    for k in range(nt):
        v_z[k, :] = row
    dst.close()
    src.close()
    print(f"Wrote {args.out}: {nt} times, zeta = {args.value}, {os.path.getsize(args.out) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
