#!/usr/bin/env python3
"""Model 10-m wind at observation stations, with and without land friction.

For one product (gdas or gfs) this reads

* the MetGet OWI ASCII ``.wnd`` (raw model wind, no land friction), and
* the ``RICHAMP_wind`` NetCDF that scale_and_subset.py made from it (land friction,
  30 m grid),

interpolates u and v bilinearly to every station and writes one CSV row per station
and hour. Stations outside the RICHAMP grid get raw wind only (``in_richamp`` False).
The local NLCD roughness at each station is kept for interpretation.

Example:

    python extract_station_wind.py --stations-csv stations.csv --product gdas \\
        --wnd gdas.wnd --richamp RICHAMP_wind_gdas.nc \\
        --hr-roughness NLCD_z0_RICHAMP_Reg_Grid.nc -o station_wind_gdas.csv
"""
import argparse
import datetime
import itertools
import math
import sys

import netCDF4
import numpy
import pandas

WATER_Z0 = 0.01  # m; NLCD open water is 0.003 m, the next class up is >= 0.03 m


def uv_from_met(speed, direction):
    """u, v from speed and meteorological direction (degrees, the wind blows from)."""
    rad = numpy.deg2rad(direction)
    return -speed * numpy.sin(rad), -speed * numpy.cos(rad)


def met_from_uv(u, v):
    return numpy.hypot(u, v), (numpy.degrees(numpy.arctan2(-u, -v)) + 360) % 360


def bracket(axis, value):
    """(i0, i1, weight of i1) of ``value`` on a monotonic ``axis``; None outside it."""
    axis = numpy.asarray(axis, dtype=float)
    ascending = axis[-1] > axis[0]
    a = axis if ascending else axis[::-1]
    if not a[0] <= value <= a[-1]:
        return None
    i = min(int(numpy.searchsorted(a, value, side="right")) - 1, len(a) - 2)
    w = (value - a[i]) / (a[i + 1] - a[i])
    if not ascending:  # map back to the original order
        return len(axis) - 1 - i, len(axis) - 2 - i, w
    return i, i + 1, w


def bilinear(corners, wy, wx):
    """corners[..., 2, 2] as [[y0x0, y0x1], [y1x0, y1x1]]; NaN if a weighted corner is NaN."""
    return ((1 - wy) * ((1 - wx) * corners[..., 0, 0] + wx * corners[..., 0, 1])
            + wy * ((1 - wx) * corners[..., 1, 0] + wx * corners[..., 1, 1]))


# ---------------------------------------------------------------- raw wind (OWI ASCII)
def owi_header(line):
    """Grid and time of one OWI block header (same fixed columns as scale_and_subset.py)."""
    return {"nlat": int(line[5:9]), "nlon": int(line[15:19]), "dx": float(line[22:28]), "dy": float(line[31:37]),
            "swlat": float(line[43:51]), "swlon": float(line[57:65]),
            "time": datetime.datetime.strptime(line[68:80], "%Y%m%d%H%M")}


def raw_at_stations(path, stations):
    """DataFrame (key, valid, raw_speed, raw_dir) from an OWI ASCII .wnd file."""
    rows = []
    grid = None
    with open(path) as f:
        f.readline()  # title
        for header in f:
            if not header.strip():
                continue
            h = owi_header(header)
            n = h["nlat"] * h["nlon"]
            nlines = math.ceil(n / 8)
            if grid is None or (h["nlat"], h["nlon"], h["swlat"], h["swlon"]) != grid["key"]:
                lat = h["swlat"] + h["dy"] * numpy.arange(h["nlat"])
                lon = h["swlon"] + h["dx"] * numpy.arange(h["nlon"])
                points = {}
                for s in stations.itertuples(index=False):
                    by, bx = bracket(lat, s.lat), bracket(lon, s.lon)
                    if by is None or bx is None:
                        print(f"  {s.key}: outside the {path} grid", file=sys.stderr)
                        continue
                    idx = [[by[0] * h["nlon"] + bx[0], by[0] * h["nlon"] + bx[1]],
                           [by[1] * h["nlon"] + bx[0], by[1] * h["nlon"] + bx[1]]]
                    points[s.key] = (numpy.array(idx), by[2], bx[2])
                grid = {"key": (h["nlat"], h["nlon"], h["swlat"], h["swlon"])}
            u_lines = list(itertools.islice(f, nlines))
            v_lines = list(itertools.islice(f, nlines))
            if len(v_lines) != nlines:
                raise SystemExit(f"{path}: truncated block at {h['time']}")

            def value(lines, k):
                c = k % 8
                return float(lines[k // 8][10 * c:10 * c + 10])

            for key, (idx, wy, wx) in points.items():
                u = numpy.vectorize(lambda k: value(u_lines, k))(idx)
                v = numpy.vectorize(lambda k: value(v_lines, k))(idx)
                spd, dirn = met_from_uv(bilinear(u, wy, wx), bilinear(v, wy, wx))
                rows.append({"key": key, "valid": h["time"], "raw_speed": float(spd), "raw_dir": float(dirn)})
    return pandas.DataFrame(rows)


# ---------------------------------------------------------------- land-friction wind (RICHAMP_wind.nc)
def friction_at_stations(path, stations):
    """DataFrame (key, valid, fric_speed, fric_dir) and the set of keys inside the RICHAMP grid."""
    rows, inside = [], set()
    with netCDF4.Dataset(path) as ds:
        g = ds["Main"]
        lat, lon = numpy.asarray(g["lat"][:], float), numpy.asarray(g["lon"][:], float)
        times = pandas.to_datetime(numpy.asarray(g["time_unix"][:], dtype="int64"), unit="s")
        for s in stations.itertuples(index=False):
            by, bx = bracket(lat, s.lat), bracket(lon, s.lon)
            if by is None or bx is None:
                continue
            ys, xs = sorted(by[:2]), sorted(bx[:2])
            sl = (slice(None), slice(ys[0], ys[1] + 1), slice(xs[0], xs[1] + 1))
            spd = numpy.ma.filled(numpy.ma.asarray(g["spd"][sl], dtype=float), numpy.nan)
            dirn = numpy.ma.filled(numpy.ma.asarray(g["dir"][sl], dtype=float), numpy.nan)
            # corners in [[y0x0, y0x1], [y1x0, y1x1]] order of the bracket, whatever the axis direction
            if by[0] > by[1]:
                spd, dirn = spd[:, ::-1, :], dirn[:, ::-1, :]
            if bx[0] > bx[1]:
                spd, dirn = spd[:, :, ::-1], dirn[:, :, ::-1]
            u, v = uv_from_met(spd, dirn)
            fs, fd = met_from_uv(bilinear(u, by[2], bx[2]), bilinear(v, by[2], bx[2]))
            inside.add(s.key)
            rows += [{"key": s.key, "valid": t, "fric_speed": a, "fric_dir": b} for t, a, b in zip(times, fs, fd)]
    return pandas.DataFrame(rows, columns=["key", "valid", "fric_speed", "fric_dir"]), inside


def local_roughness(path, stations):
    """Nearest-pixel NLCD z0 at each station inside the high-resolution roughness grid."""
    out = {}
    with netCDF4.Dataset(path) as ds:
        lat, lon = numpy.asarray(ds["lat"][:], float), numpy.asarray(ds["lon"][:], float)
        for s in stations.itertuples(index=False):
            if not (min(lat) <= s.lat <= max(lat) and min(lon) <= s.lon <= max(lon)):
                continue
            i, j = int(numpy.abs(lat - s.lat).argmin()), int(numpy.abs(lon - s.lon).argmin())
            out[s.key] = float(ds["land_rough"][i, j])
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stations-csv", required=True, help="stations.csv of the observation run (key, lat, lon, ...)")
    p.add_argument("--product", required=True, help="label written to the CSV, e.g. gdas or gfs")
    p.add_argument("--wnd", required=True, help="MetGet OWI ASCII .wnd (raw wind)")
    p.add_argument("--richamp", help="RICHAMP_wind NetCDF from scale_and_subset.py (land friction)")
    p.add_argument("--hr-roughness", help="NLCD_z0_RICHAMP_Reg_Grid.nc, for the local z0 column")
    p.add_argument("-o", "--out", required=True, help="output CSV")
    args = p.parse_args()

    stations = pandas.read_csv(args.stations_csv, dtype={"station_id": str}).drop_duplicates("key")
    stations = stations[["key", "source", "station_id", "lat", "lon"]]
    print(f"{args.product}: {len(stations)} stations")

    raw = raw_at_stations(args.wnd, stations)
    print(f"  raw wind: {raw.valid.nunique()} times from {args.wnd}")
    if args.richamp:
        fric, inside = friction_at_stations(args.richamp, stations)
        print(f"  land-friction wind: {fric.valid.nunique()} times, {len(inside)} stations inside the RICHAMP grid")
    else:
        fric, inside = pandas.DataFrame(columns=["key", "valid", "fric_speed", "fric_dir"]), set()

    df = raw.merge(fric, on=["key", "valid"], how="outer").merge(stations, on="key", how="left")
    df.insert(0, "product", args.product)
    df["in_richamp"] = df.key.isin(inside)
    z0 = local_roughness(args.hr_roughness, stations) if args.hr_roughness else {}
    df["z0_local_m"] = df.key.map(z0)
    df["surface"] = numpy.where(df.z0_local_m.isna(), "", numpy.where(df.z0_local_m < WATER_Z0, "water", "land"))
    df = df.sort_values(["key", "valid"])
    df.to_csv(args.out, index=False, float_format="%.4f")

    summary = df.groupby("key").agg(in_richamp=("in_richamp", "first"), z0=("z0_local_m", "first"),
                                    raw=("raw_speed", "mean"), friction=("fric_speed", "mean"))
    summary["friction/raw"] = summary.friction / summary.raw
    print(summary.round(3).to_string())
    print("wrote", args.out)


if __name__ == "__main__":
    main()
