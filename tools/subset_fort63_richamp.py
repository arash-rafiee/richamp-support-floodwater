#!/usr/bin/env python3
"""
Python port of subset_fort63_richamp.m: cut the RICHAMP region out of an ADCIRC fort.63.nc.

Keeps the nodes inside the RICHAMP box, the elements whose three nodes are all inside,
renumbers them, and writes RICHAMP_fort63.nc with the same variables the MATLAB script
produces: time, x, y, element, depth, zeta, time_unix (plus the source's global attributes).

Usage:
    python subset_fort63_richamp.py /path/to/fort.63.nc                 # -> RICHAMP_fort63.nc
    python subset_fort63_richamp.py /path/to/fort.63.nc -o out.nc
    python subset_fort63_richamp.py fort.63.nc --box -71.9 -71.108333 41.141667 42.041667

Requires: netCDF4 numpy
"""
import argparse
import datetime
import os

import netCDF4
import numpy

# Same box as subset_fort63_richamp.m: lon [-71-54/60, -71-6/60-30/3600], lat [41+8/60+30/3600, 42+2/60+30/3600]
RICHAMP_BOX = (-71 - 54 / 60, -71 - 6 / 60 - 30 / 3600, 41 + 8 / 60 + 30 / 3600, 42 + 2 / 60 + 30 / 3600)
FILL = -99999.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fort63", help="full-domain ADCIRC fort.63.nc")
    ap.add_argument("-o", "--out", default="RICHAMP_fort63.nc")
    ap.add_argument("--box", type=float, nargs=4, metavar=("W", "E", "S", "N"), default=RICHAMP_BOX)
    ap.add_argument("--block", type=int, default=24, help="time steps read per block (memory control)")
    args = ap.parse_args()
    w, e, s, n = args.box

    src = netCDF4.Dataset(args.fort63, "r")
    x = numpy.array(src["x"][:])
    y = numpy.array(src["y"][:])
    depth = numpy.array(src["depth"][:])
    elem = numpy.array(src["element"][:])  # (nele, 3), 1-based
    time = numpy.array(src["time"][:])
    base = src["time"].getncattr("base_date") if "base_date" in src["time"].ncattrs() else src["time"].units.split("since")[1].strip()
    base_dt = datetime.datetime.strptime(base[:19], "%Y-%m-%d %H:%M:%S")
    print(f"source: {x.size} nodes, {elem.shape[0]} elements, {time.size} times, base {base_dt}")

    inside = (x >= w) & (x <= e) & (y >= s) & (y <= n)
    new_id = numpy.full(x.size, -1, dtype=numpy.int64)
    idx = numpy.where(inside)[0]
    new_id[idx] = numpy.arange(1, idx.size + 1)          # 1-based like ADCIRC
    keep_el = inside[elem - 1].all(axis=1)
    elem_sub = new_id[elem[keep_el] - 1].astype(numpy.int32)
    print(f"subset: {idx.size} nodes, {elem_sub.shape[0]} elements in box W{w:.4f} E{e:.4f} S{s:.4f} N{n:.4f}")

    t0_unix = (base_dt - datetime.datetime(1970, 1, 1)).total_seconds()
    time_unix = t0_unix + time

    if os.path.exists(args.out):
        os.remove(args.out)
    dst = netCDF4.Dataset(args.out, "w", format="NETCDF4")
    for a in src.ncattrs():
        if a != "_FillValue":
            dst.setncattr(a, src.getncattr(a))
    dst.setncattr("_FillValue", FILL)
    dst.setncattr("richamp_subset", f"subset_fort63_richamp.py from {os.path.basename(args.fort63)} on {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M:%SZ}")

    dst.createDimension("time", None)
    dst.createDimension("node", idx.size)
    dst.createDimension("nele", elem_sub.shape[0])
    dst.createDimension("nvertex", 3)

    def copy_attrs(name, var):
        for a in src[name].ncattrs():
            if a != "_FillValue":
                var.setncattr(a, src[name].getncattr(a))

    v_time = dst.createVariable("time", "f8", ("time",)); copy_attrs("time", v_time)
    v_x = dst.createVariable("x", "f8", ("node",), zlib=True, complevel=2); copy_attrs("x", v_x)
    v_y = dst.createVariable("y", "f8", ("node",), zlib=True, complevel=2); copy_attrs("y", v_y)
    v_el = dst.createVariable("element", "i4", ("nele", "nvertex"), zlib=True, complevel=2); copy_attrs("element", v_el)
    v_dp = dst.createVariable("depth", "f8", ("node",), zlib=True, complevel=2); copy_attrs("depth", v_dp)
    v_z = dst.createVariable("zeta", "f8", ("time", "node"), zlib=True, complevel=2, fill_value=FILL,
                             chunksizes=(1, idx.size)); copy_attrs("zeta", v_z)
    v_tu = dst.createVariable("time_unix", "f8", ("time",))
    v_tu.long_name = "model time_unix"
    v_tu.standard_name = "time_unix"
    v_tu.units = "seconds since 1970-01-01 00:00:00"
    v_tu.base_date = "1970-01-01 00:00:00"

    v_time[:] = time
    v_tu[:] = time_unix
    v_x[:] = x[idx]
    v_y[:] = y[idx]
    v_dp[:] = depth[idx]
    v_el[:, :] = elem_sub

    zsrc = src["zeta"]
    zsrc.set_auto_mask(False)
    for t0 in range(0, time.size, args.block):
        t1 = min(time.size, t0 + args.block)
        block = zsrc[t0:t1, :][:, idx]
        v_z[t0:t1, :] = block
        print(f"  zeta {t1}/{time.size}", flush=True)
    dst.close()
    src.close()
    first = base_dt + datetime.timedelta(seconds=float(time[0]))
    last = base_dt + datetime.timedelta(seconds=float(time[-1]))
    print(f"Wrote {args.out}: {idx.size} nodes, {elem_sub.shape[0]} elements, {time.size} times {first:%Y-%m-%d %H:%M} .. {last:%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
