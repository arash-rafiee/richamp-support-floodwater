#!/usr/bin/env python3
"""
Extract ADCIRC water surface elevation (zeta, m NAVD88) time series at a list of
points straight from a fort.63.nc, and plot them.

Made for the salt-pond question (Ninigret, Green Hill, Potter Ponds, Goose Neck
Cove, the pond by Watchemoket Cove): the dashboard shows depth above ground from
the post-processing, this shows what ADCIRC itself computed at those places.

Works on a full-domain fort.63.nc (tens of GB, on the cluster) or on a
RICHAMP_fort63.nc subset. Only the requested nodes are read from the time
dependent field, in blocks of time steps, so memory stays small.

For every point the nearest *subtidal* mesh node (depth > 0, i.e. below NAVD88)
is used, so the series is the water level in the pond and not on a dry bank.
Points more than --max-dist metres from any subtidal node are reported and
skipped. Dry time steps (fill value) come out as NaN and as gaps in the plot.

Outputs in --out:
    pond_zeta.csv          time_utc + one column per point (m NAVD88, NaN = dry)
    pond_nodes.json        which node each point mapped to, distance, depth, max zeta
    pond_<group>.png       one time-series figure per group (points of one area together)
    pond_map_<group>.png   with --map: peak zeta on the mesh, zoomed on the group

Examples (Unity):
    python report_brief/pond_timeseries.py /path/to/run/fort.63.nc --out pond_out/
    python report_brief/pond_timeseries.py fort.63.nc --points report_brief/pond_points.json --map --out pond_out/

Requires: numpy netCDF4 matplotlib
"""
import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import netCDF4

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POINTS = os.path.join(HERE, "pond_points.json")
FILL = -99999.0

# fixed categorical order (dataviz palette, light surface); reference point is slot 1
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]


def parseTimes(dataset):
    """Return a list of UTC datetimes for the file's time axis."""
    timeVar = dataset.variables["time"]
    raw = np.asarray(timeVar[:], dtype=np.float64)
    units = getattr(timeVar, "units", "seconds since 1970-01-01 00:00:00")
    base = getattr(timeVar, "base_date", None)
    match = re.match(r"\s*(\w+)\s+since\s+(.*)", units)
    unit = match.group(1).lower() if match else "seconds"
    baseText = base if base else (match.group(2) if match else "1970-01-01 00:00:00")
    baseText = baseText.strip().replace("T", " ")
    baseText = re.sub(r"\s*(UTC|Z|\+00:?00)$", "", baseText)
    baseText = re.sub(r"\s+\d{2}:\d{2}$", "", baseText) if baseText.count(":") > 2 else baseText
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            baseTime = datetime.strptime(baseText, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            baseTime = None
    if baseTime is None:
        raise ValueError("cannot parse time base '%s' / units '%s'" % (base, units))
    factor = {"seconds": 1.0, "second": 1.0, "minutes": 60.0, "minute": 60.0,
              "hours": 3600.0, "hour": 3600.0, "days": 86400.0, "day": 86400.0}[unit]
    return [baseTime + timedelta(seconds=float(v) * factor) for v in raw]


def loadPoints(path):
    with open(path) as handle:
        spec = json.load(handle)
    points = spec["points"] if isinstance(spec, dict) else spec
    for index, point in enumerate(points):
        point.setdefault("group", "points")
        point.setdefault("name", "point %d" % (index + 1))
        point["lon"] = float(point["lon"])
        point["lat"] = float(point["lat"])
    return points


def metresPerDegree(lat):
    return 111320.0 * math.cos(math.radians(lat)), 110540.0


def nearestNode(x, y, depth, lon, lat, maxDist, requireSubtidal=True):
    """Nearest node index to (lon, lat), among subtidal nodes if requested. Returns (index, dist_m)."""
    mx, my = metresPerDegree(lat)
    dx = (x - lon) * mx
    dy = (y - lat) * my
    dist = np.hypot(dx, dy)
    if requireSubtidal:
        dist = np.where(depth > 0, dist, np.inf)
    index = int(np.argmin(dist))
    return index, float(dist[index])


def readSeries(dataset, nodeIndices, block):
    """zeta[:, nodeIndices] read block time steps at a time; fill -> NaN."""
    zetaVar = dataset.variables["zeta"]
    zetaVar.set_auto_mask(False)
    numTimes = zetaVar.shape[0]
    order = np.argsort(nodeIndices)
    sortedNodes = [int(nodeIndices[i]) for i in order]
    fill = getattr(zetaVar, "_FillValue", FILL)
    out = np.full((numTimes, len(nodeIndices)), np.nan)
    for start in range(0, numTimes, block):
        stop = min(start + block, numTimes)
        chunk = np.asarray(zetaVar[start:stop, sortedNodes], dtype=np.float64)
        chunk[(chunk == fill) | (chunk < -1000)] = np.nan
        out[start:stop, order] = chunk
        print("  read time steps %d..%d of %d" % (start, stop - 1, numTimes), flush=True)
    return out


def plotGroup(groupName, members, times, series, outDir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(11, 5), dpi=130)
    for slot, member in enumerate(members):
        values = series[:, member["column"]]
        color = SERIES_COLORS[slot % len(SERIES_COLORS)]
        ax.plot(times, values, color=color, linewidth=2 if slot == 0 else 1.6,
                linestyle="-" if slot == 0 else "-", label=member["name"])
        finite = np.isfinite(values)
        # label the peak only where the series actually moves; flat pond traces would pile up labels
        if finite.any() and (np.nanmax(values) - np.nanmin(values)) > 0.1:
            peak = int(np.nanargmax(values))
            ax.annotate("%.2f" % values[peak], (times[peak], values[peak]), textcoords="offset points",
                        xytext=(4, 4), fontsize=8, color="#333333")
    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.set_ylabel("Water surface elevation (m NAVD88)")
    ax.set_title("ADCIRC zeta: %s" % groupName)
    ax.grid(True, color="#e6e6e6", linewidth=0.6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%HZ"))
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.text(0.99, 0.01, "gaps = node dry in ADCIRC; nearest subtidal node to each point",
             ha="right", va="bottom", fontsize=7, color="#666666")
    fig.tight_layout()
    path = os.path.join(outDir, "pond_%s.png" % safeName(groupName))
    fig.savefig(path)
    plt.close(fig)
    return path


def plotMap(groupName, members, dataset, x, y, depth, peak, outDir, pad=0.012):
    pad = max(pad, 0.012)
    """Peak zeta on the mesh around the group's points (elements with all 3 nodes in the window)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri

    lons = [m["lon"] for m in members]
    lats = [m["lat"] for m in members]
    w, e = min(lons) - pad, max(lons) + pad
    s, n = min(lats) - pad, max(lats) + pad
    inside = (x >= w) & (x <= e) & (y >= s) & (y <= n)
    elements = np.asarray(dataset.variables["element"][:]) - 1
    keep = inside[elements].all(axis=1)
    if not keep.any():
        print("  no elements in the window for", groupName, flush=True)
        return None
    nodeIds = np.unique(elements[keep])
    remap = np.full(len(x), -1, dtype=np.int64)
    remap[nodeIds] = np.arange(len(nodeIds))
    tri = mtri.Triangulation(x[nodeIds], y[nodeIds], remap[elements[keep]])
    values = peak[nodeIds]
    dry = ~np.isfinite(values)
    tri.set_mask(dry[remap[elements[keep]]].any(axis=1))

    fig, ax = plt.subplots(figsize=(8, 7), dpi=130)
    ax.triplot(mtri.Triangulation(x[nodeIds], y[nodeIds], remap[elements[keep]]),
               color="#d0d0d0", linewidth=0.15)
    filled = np.where(np.isfinite(values), values, 0.0)
    vmax = float(np.nanmax(values)) if np.isfinite(values).any() else 1.0
    pc = ax.tripcolor(tri, filled, shading="gouraud", cmap="Blues", vmin=0, vmax=max(vmax, 0.1))
    ax.tricontour(mtri.Triangulation(x[nodeIds], y[nodeIds], remap[elements[keep]]), depth[nodeIds],
                  levels=[0.0], colors="#444444", linewidths=0.6)
    for slot, member in enumerate(members):
        color = SERIES_COLORS[slot % len(SERIES_COLORS)]
        ax.plot(member["lon"], member["lat"], "+", color="#000000", markersize=9, markeredgewidth=1.2)
        if member.get("node") is not None:
            ax.plot(x[member["node"]], y[member["node"]], "o", color=color, markersize=7,
                    markeredgecolor="white", markeredgewidth=1.0)
        ax.annotate(member["name"], (member["lon"], member["lat"]), textcoords="offset points",
                    xytext=(5, 5), fontsize=7, color="#222222")
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)
    ax.set_aspect(1.0 / math.cos(math.radians((s + n) / 2)))
    ax.set_title("Peak ADCIRC zeta (m NAVD88), %s\ngray mesh; dark line = NAVD88 shoreline (depth 0); white = never wet" % groupName, fontsize=9)
    fig.colorbar(pc, ax=ax, shrink=0.8, label="max zeta (m NAVD88)")
    fig.tight_layout()
    path = os.path.join(outDir, "pond_map_%s.png" % safeName(groupName))
    fig.savefig(path)
    plt.close(fig)
    return path


def safeName(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fort63", help="ADCIRC fort.63.nc (full domain or RICHAMP subset)")
    ap.add_argument("--points", default=DEFAULT_POINTS, help="JSON list of points (default report_brief/pond_points.json)")
    ap.add_argument("--out", default="pond_out", help="output directory")
    ap.add_argument("--max-dist", type=float, default=400.0, help="max metres from point to nearest subtidal node")
    ap.add_argument("--block", type=int, default=48, help="time steps per read")
    ap.add_argument("--map", action="store_true", help="also draw peak-zeta mesh maps per group (reads the whole field once)")
    ap.add_argument("--no-plots", action="store_true", help="CSV and JSON only")
    ap.add_argument("--pad", type=float, default=0.012, help="map window padding around the group points (degrees)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    points = loadPoints(args.points)
    dataset = netCDF4.Dataset(args.fort63, "r")
    print("opening", args.fort63, flush=True)
    x = np.asarray(dataset.variables["x"][:], dtype=np.float64)
    y = np.asarray(dataset.variables["y"][:], dtype=np.float64)
    depth = np.asarray(dataset.variables["depth"][:], dtype=np.float64)
    times = parseTimes(dataset)
    print("nodes %d, time steps %d, %s .. %s" % (len(x), len(times), times[0], times[-1]), flush=True)

    kept = []
    for point in points:
        index, dist = nearestNode(x, y, depth, point["lon"], point["lat"], args.max_dist, requireSubtidal=True)
        anyIndex, anyDist = nearestNode(x, y, depth, point["lon"], point["lat"], args.max_dist, requireSubtidal=False)
        limit = float(point.get("max_dist_m", args.max_dist))
        point["node"] = None
        point["nearest_any_node"] = anyIndex
        point["nearest_any_dist_m"] = round(anyDist, 1)
        point["nearest_any_depth_m"] = round(float(depth[anyIndex]), 2)
        if not np.isfinite(dist) or dist > limit:
            print("SKIP %-45s nearest subtidal node is %.0f m away (limit %.0f); nearest node of any kind %.0f m, depth %.2f"
                  % (point["name"], dist if np.isfinite(dist) else float("inf"), limit, anyDist, depth[anyIndex]), flush=True)
            continue
        point["node"] = index
        point["node_lon"] = float(x[index])
        point["node_lat"] = float(y[index])
        point["node_dist_m"] = round(dist, 1)
        point["node_depth_m"] = round(float(depth[index]), 2)
        point["column"] = len(kept)
        kept.append(point)
        print("%-45s -> node %8d  %.0f m away, depth %.2f m" % (point["name"], index, dist, depth[index]), flush=True)
    if not kept:
        sys.exit("no points mapped to the mesh")

    print("reading zeta at %d nodes" % len(kept), flush=True)
    series = readSeries(dataset, [p["node"] for p in kept], args.block)

    for point in kept:
        values = series[:, point["column"]]
        finite = np.isfinite(values)
        point["max_zeta_m"] = round(float(np.nanmax(values)), 3) if finite.any() else None
        point["min_zeta_m"] = round(float(np.nanmin(values)), 3) if finite.any() else None
        point["wet_fraction"] = round(float(finite.mean()), 3)
        if finite.any():
            point["time_of_max_utc"] = times[int(np.nanargmax(values))].strftime("%Y-%m-%d %H:%M")
    print()
    print("%-45s %9s %9s %6s  %s" % ("point", "max zeta", "min zeta", "wet", "time of max (UTC)"))
    for point in kept:
        print("%-45s %9s %9s %5.0f%%  %s" % (point["name"], point["max_zeta_m"], point["min_zeta_m"],
                                             100 * point["wet_fraction"], point.get("time_of_max_utc", "")))

    csvPath = os.path.join(args.out, "pond_zeta.csv")
    with open(csvPath, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_utc"] + [p["name"] for p in kept])
        for row, time in enumerate(times):
            writer.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ")] +
                            ["" if not np.isfinite(v) else "%.4f" % v for v in series[row]])
    meta = {"source": os.path.abspath(args.fort63), "title": getattr(dataset, "title", ""),
            "rundes": getattr(dataset, "rundes", ""), "agrid": getattr(dataset, "agrid", ""),
            "time_start_utc": times[0].isoformat(), "time_end_utc": times[-1].isoformat(),
            "num_time_steps": len(times), "max_dist_m": args.max_dist, "points": points}
    with open(os.path.join(args.out, "pond_nodes.json"), "w") as handle:
        json.dump(meta, handle, indent=2)
    print("\nwrote", csvPath, flush=True)

    if args.no_plots:
        return
    groups = []
    for point in kept:
        if point["group"] not in groups:
            groups.append(point["group"])
    for group in groups:
        members = [p for p in kept if p["group"] == group]
        print("plot", plotGroup(group, members, times, series, args.out), flush=True)

    if args.map:
        print("computing peak zeta over the whole mesh for the maps", flush=True)
        zetaVar = dataset.variables["zeta"]
        zetaVar.set_auto_mask(False)
        fill = getattr(zetaVar, "_FillValue", FILL)
        peak = np.full(len(x), np.nan)
        for start in range(0, len(times), args.block):
            chunk = np.asarray(zetaVar[start:start + args.block, :], dtype=np.float64)
            chunk[(chunk == fill) | (chunk < -1000)] = np.nan
            with np.errstate(all="ignore"):
                import warnings
                warnings.simplefilter("ignore", RuntimeWarning)
                peak = np.fmax(peak, np.nanmax(chunk, axis=0)) if chunk.size else peak
            print("  peak: time steps %d..%d" % (start, min(start + args.block, len(times)) - 1), flush=True)
        for group in groups:
            members = [p for p in points if p["group"] == group]
            print("map", plotMap(group, members, dataset, x, y, depth, peak, args.out, pad=args.pad), flush=True)


if __name__ == "__main__":
    main()
