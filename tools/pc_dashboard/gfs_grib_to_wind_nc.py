#!/usr/bin/env python3
"""
Merge GFS GRIB2 wind files (as downloaded by get_gfs_wind.py from NOMADS) into
one "generic-netcdf" wind file that scale_and_subset.py understands.

The output mirrors what `metget build ... --format generic-netcdf --variable wind_pressure`
produces, minus pressure:

    dimensions: time (unlimited), lat, lon
    variables:  lon(lon) f8, lat(lat) f8,
                time(time) i4  [minutes since <first forecast time>],
                wind_u(time, lat, lon) f4, wind_v(time, lat, lon) f4

Usage:
    python gfs_grib_to_wind_nc.py gfs_wind/20260923_12z            # -> gfs_wind.nc
    python gfs_grib_to_wind_nc.py gfs_wind/20260923_12z -o my.nc
    python gfs_grib_to_wind_nc.py f000.grb2 f003.grb2 ... -o gfs_wind.nc
    python gfs_grib_to_wind_nc.py gfs_wind/20260923_12z --bbox 43 40 -73 -70

Then:
    python scale_and_subset.py -o RICHAMP_wind -sl up-down -hr NLCD_z0_RICHAMP_Reg_Grid.nc \
        -w gfs_wind.nc -wfmt generic-netcdf -wr gfs-roughness.nc -z0name z0_interp -r 3000 -sigma 1000 -t 3 -wasync
    (add -z0sv the first time so z0_interp.pickle gets generated)

Requires: xarray cfgrib netCDF4 numpy
"""
import argparse
import datetime
import glob
import os
import sys

import netCDF4
import numpy
import xarray


def find_grib_files(inputs):
    files = []
    for item in inputs:
        if os.path.isdir(item):
            files.extend(sorted(glob.glob(os.path.join(item, "*.grb2"))))
            files.extend(sorted(glob.glob(os.path.join(item, "*.grib2"))))
        else:
            files.extend(sorted(glob.glob(item)))
    files = [f for f in files if os.path.getsize(f) > 0]
    if not files:
        sys.exit("No non-empty GRIB2 files found in: " + " ".join(inputs))
    return files


def read_uv(path, level):
    """Return (valid_time, lat[asc], lon[asc, -180..180], u, v) for one GRIB2 file."""
    ds = xarray.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"indexpath": "",  # don't litter .idx files next to the data
                        "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": level}},
        decode_timedelta=True,
    )
    u = v = None
    for name, da in ds.data_vars.items():
        short = da.attrs.get("GRIB_shortName", name)
        if short in ("10u", "u", "100u") or name in ("u10", "u", "u100"):
            u = da
        elif short in ("10v", "v", "100v") or name in ("v10", "v", "v100"):
            v = da
    if u is None or v is None:
        sys.exit(f"{path}: could not find U/V at {level} m AGL (variables: {list(ds.data_vars)})")

    valid_time = ds["valid_time"].values
    valid_time = datetime.datetime.fromtimestamp(int(valid_time.astype("datetime64[s]").astype(int)),
                                                 tz=datetime.timezone.utc).replace(tzinfo=None)

    lat = numpy.asarray(ds["latitude"].values, dtype=numpy.float64)
    lon = numpy.asarray(ds["longitude"].values, dtype=numpy.float64)
    u = numpy.asarray(u.values, dtype=numpy.float32)
    v = numpy.asarray(v.values, dtype=numpy.float32)
    if u.ndim != 2:
        sys.exit(f"{path}: expected 2-D U/V fields, got shape {u.shape}; is more than one level present?")
    ds.close()

    # GFS is 0..360 in longitude and north-to-south in latitude in the raw files.
    # scale_and_subset.py uses RectBivariateSpline, which needs both axes strictly increasing,
    # and compares against roughness grids that use -180..180 longitudes.
    lon = numpy.where(lon > 180, lon - 360, lon)
    lon_order = numpy.argsort(lon)
    lat_order = numpy.argsort(lat)
    lon = lon[lon_order]
    lat = lat[lat_order]
    u = u[lat_order, :][:, lon_order]
    v = v[lat_order, :][:, lon_order]
    return valid_time, lat, lon, u, v


def crop(lat, lon, u, v, bbox):
    n, s, w, e = bbox
    li = numpy.where((lat >= s - 1e-6) & (lat <= n + 1e-6))[0]
    oi = numpy.where((lon >= w - 1e-6) & (lon <= e + 1e-6))[0]
    if li.size < 2 or oi.size < 2:
        sys.exit(f"bbox {bbox} leaves fewer than 2 points on one axis "
                 f"(lat {lat.min()}..{lat.max()}, lon {lon.min()}..{lon.max()})")
    return lat[li], lon[oi], u[li, :][:, oi], v[li, :][:, oi]


def write_generic_netcdf(out, times, lat, lon, u, v, source_files):
    base = times[0]
    nc = netCDF4.Dataset(out, "w", format="NETCDF4")
    nc.source = "gfs_grib_to_wind_nc.py (GFS 0.25 deg via NOMADS grib filter)"
    nc.history = f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ} merged {len(source_files)} GRIB2 files"
    nc.conventions = "CF-1.6"

    nc.createDimension("time", None)
    nc.createDimension("lat", lat.size)
    nc.createDimension("lon", lon.size)

    v_time = nc.createVariable("time", "i4", ("time",))
    v_time.units = base.strftime("minutes since %Y-%m-%d %H:%M:%S")  # exact layout GenericNetcdf() parses
    v_time.axis = "T"
    v_time.calendar = "standard"

    v_lat = nc.createVariable("lat", "f8", ("lat",))
    v_lat.units = "degrees_north"
    v_lat.standard_name = "latitude"
    v_lat.axis = "Y"

    v_lon = nc.createVariable("lon", "f8", ("lon",))
    v_lon.units = "degrees_east"
    v_lon.standard_name = "longitude"
    v_lon.axis = "X"

    kw = dict(zlib=True, complevel=2, fill_value=netCDF4.default_fillvals["f4"])
    v_u = nc.createVariable("wind_u", "f4", ("time", "lat", "lon"), **kw)
    v_u.units = "m s-1"
    v_u.long_name = "eastward wind at 10 m above ground"
    v_u.coordinates = "time lat lon"
    v_v = nc.createVariable("wind_v", "f4", ("time", "lat", "lon"), **kw)
    v_v.units = "m s-1"
    v_v.long_name = "northward wind at 10 m above ground"
    v_v.coordinates = "time lat lon"

    v_lat[:] = lat
    v_lon[:] = lon
    v_time[:] = [round((t - base).total_seconds() / 60) for t in times]
    v_u[:, :, :] = u
    v_v[:, :, :] = v
    nc.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="GRIB2 files, globs, or a directory of them")
    ap.add_argument("-o", "--out", default="gfs_wind.nc", help="output NetCDF (default gfs_wind.nc)")
    ap.add_argument("--level", type=int, default=10, help="height above ground to extract, m (default 10)")
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("N", "S", "W", "E"),
                    help="crop to this box (lon in -180..180); e.g. 43 40 -73 -70 matches gfs-roughness.nc")
    args = ap.parse_args()

    files = find_grib_files(args.inputs)
    print(f"Reading {len(files)} GRIB2 files ({args.level} m AGL)...")

    records = []
    ref_lat = ref_lon = None
    for path in files:
        t, lat, lon, u, v = read_uv(path, args.level)
        if args.bbox:
            lat, lon, u, v = crop(lat, lon, u, v, args.bbox)
        if ref_lat is None:
            ref_lat, ref_lon = lat, lon
        elif not (numpy.allclose(lat, ref_lat) and numpy.allclose(lon, ref_lon)):
            sys.exit(f"{path}: grid differs from {files[0]}; all files must share one grid")
        records.append((t, u, v, path))

    records.sort(key=lambda r: r[0])
    times = [r[0] for r in records]
    dups = [t for i, t in enumerate(times) if i and t == times[i - 1]]
    if dups:
        sys.exit(f"Duplicate valid times in input: {dups[:5]}")

    u = numpy.stack([r[1] for r in records])
    v = numpy.stack([r[2] for r in records])
    write_generic_netcdf(args.out, times, ref_lat, ref_lon, u, v, files)

    steps = sorted({int((b - a).total_seconds() // 60) for a, b in zip(times, times[1:])})
    print(f"Wrote {args.out}")
    print(f"  {len(times)} times: {times[0]:%Y-%m-%d %H:%M} .. {times[-1]:%Y-%m-%d %H:%M} UTC, step(s) {steps} min")
    print(f"  lat {ref_lat.min():.3f}..{ref_lat.max():.3f} ({ref_lat.size}), lon {ref_lon.min():.3f}..{ref_lon.max():.3f} ({ref_lon.size})")
    print(f"  |wind| max {numpy.sqrt(u**2 + v**2).max():.1f} m/s")


if __name__ == "__main__":
    main()
