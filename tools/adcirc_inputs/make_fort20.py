#!/usr/bin/env python3
"""
Write an ADCIRC fort.20 (non-periodic normal flux, river inflow) from USGS
15-minute discharge.

For every specified-flux boundary in fort.14 (IBTYPE 2, 12, 22, 32, 52) the
river and its USGS gauge(s) are looked up in RIVERS (or a --rivers JSON file)
by location. The gauge discharge (cfs -> m3/s) is summed, multiplied by the
river's scale factor, linearly interpolated onto the fort.20 record times and
divided by a boundary width, giving flux per unit width (m2/s). Every node of
a boundary gets the same value, positive = into the domain.

File layout written:
    line 1           FTIMINC (s), the spacing of the records (--dt)
    then per record  one value per flux node, in fort.14 boundary order
Record 0 is at --start, which must be the COLD START time (fort.15 base_date).
ADCIRC skips forward through the records on a hot start, so one file made for
the whole cold + hot start period serves both runs.

How the width is found (all from fort.14, nothing is measured by hand):
    A river boundary is a short line of nodes running across the channel, bank
    to bank, where the river enters the mesh. For each one:
      1. read its node IDs, in order, from the boundary section of fort.14
         (header "<number of nodes> 22");
      2. look up each node's lon/lat in the node table of fort.14;
      3. add up the haversine distances between neighbouring nodes.
    The total is the channel width W at the model boundary, and
        q (m2/s) = Q (m3/s) / W (m)
    is written for every node of that boundary.
    Example, Blackstone River (9 nodes, 5204 ... 4340, at Pawtucket): eight
    gaps of ~3.8 m give W = 30.16 m, so 42.3 m3/s -> 42.3 / 30.16 = 1.40 m2/s.

Width (--width):
    length     (default) the width W above. ADCIRC integrates q only along the
               flux-boundary edges (q x W), so the discharge it admits equals
               the gauge discharge.
    tributary  boundary length + half the coastline edge at each end node. This
               is how the 2018-based fort.20 in adcircModel/ was made (it lets in
               only ~70-93% of the gauge flow); use it only to reproduce that file.

USGS data come from the NWIS instantaneous-values service and are cached as
raw .rdb files in --cache, so a second run (e.g. on a compute node without
internet) works offline with --offline. If a gauge has no 15-minute data the
daily mean is used instead (placed at local noon) and a warning is printed.

Examples (Unity):
    python tools/adcirc_inputs/make_fort20.py --fort14 fort.14 \\
        --start 2022-12-01T00:00 --end 2022-12-30T00:00 --dt 900 --out fort.20

    # download on the login node, then run offline anywhere
    python tools/adcirc_inputs/make_fort20.py ... --cache usgs_cache
    python tools/adcirc_inputs/make_fort20.py ... --cache usgs_cache --offline

    # print the flux boundaries found in a mesh and how they map to RIVERS
    python tools/adcirc_inputs/make_fort20.py --fort14 fort.14 --list

Outputs: --out (fort.20) and <out>_discharge.csv (time_utc, gauge discharge
in m3/s per river) for checking.

Requires: numpy pandas (standard library for the downloads).
"""
import argparse
import io
import itertools
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

FLUX_IBTYPES = {2, 12, 22, 32, 52}
CFS_TO_CMS = 0.028316846592
EARTH_RADIUS_M = 6371008.8
IV_URL = ("https://nwis.waterservices.usgs.gov/nwis/iv/?format=rdb&sites={site}"
          "&parameterCd=00060&startDT={start}&endDT={end}")
DV_URL = ("https://waterservices.usgs.gov/nwis/dv/?format=rdb&sites={site}"
          "&parameterCd=00060&statCd=00003&startDT={start}&endDT={end}")
TZ_OFFSET_H = {"EST": -5, "EDT": -4, "UTC": 0, "GMT": 0}

# River boundaries of hsofs_NE-hires_v18_weir_rivers_depsm2(_nopump): midpoint of
# the boundary, the USGS gauges summed for it, and a scale factor on their sum.
# These are the gauges the original fort.20 was built from (verified exactly).
RIVERS = [
    {"name": "Mystic River", "lon": -71.0832, "lat": 42.3999, "gauges": ["01102500"], "scale": 1.0},
    {"name": "Charles River", "lon": -71.1156, "lat": 42.3543, "gauges": ["01104500"], "scale": 1.0},
    {"name": "Neponset River", "lon": -71.0437, "lat": 42.2773, "gauges": ["01105554"], "scale": 1.0},
    {"name": "Taunton River", "lon": -71.1046, "lat": 41.8639, "gauges": ["01108000"], "scale": 1.0},
    {"name": "Blackstone River", "lon": -71.3810, "lat": 41.8820, "gauges": ["01113895"], "scale": 1.0},
    {"name": "Moshassuck River", "lon": -71.4113, "lat": 41.8339, "gauges": ["01114000"], "scale": 1.0},
    {"name": "Woonasquatucket River", "lon": -71.4420, "lat": 41.8223, "gauges": ["01114500"], "scale": 1.0},
    {"name": "Pawtuxet River", "lon": -71.4116, "lat": 41.7659, "gauges": ["01116500"], "scale": 1.0},
    {"name": "Pawcatuck River", "lon": -71.8413, "lat": 41.4017, "gauges": ["01117500", "01118000"], "scale": 1.0},
    {"name": "Shetucket River (incl. Quinebaug)", "lon": -72.0512, "lat": 41.5396,
     "gauges": ["011230695", "01127000"], "scale": 1.0},
    {"name": "Yantic River", "lon": -72.0865, "lat": 41.5323, "gauges": ["01127500"], "scale": 1.0},
    {"name": "Connecticut River", "lon": -72.6656, "lat": 41.7702, "gauges": ["01184000"], "scale": 1.0},
]
MATCH_KM = 2.0  # a boundary maps to the RIVERS entry within this distance
GAP_WARN_H = 6  # report gauge gaps longer than this (they are filled linearly)


def haversine_m(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def read_fort14_boundaries(path):
    """Return (land boundaries [{'ibtype', 'nodes'}], {node: (lon, lat)})."""
    with open(path, "r") as f:
        f.readline()
        ne, np_ = (int(x) for x in f.readline().split()[:2])
        lines = (l.split("!")[0].split() for l in itertools.islice(f, np_ + ne, None))
        nope = int(next(lines)[0])
        next(lines)  # NETA
        for _ in range(nope):
            n = int(next(lines)[0])
            for _ in range(n):
                next(lines)
        nbou = int(next(lines)[0])
        next(lines)  # NVEL
        land = []
        for _ in range(nbou):
            h = next(lines)
            n, ib = int(h[0]), int(h[1])
            land.append({"ibtype": ib, "nodes": [int(next(lines)[0]) for _ in range(n)]})
    # coordinates of flux nodes and their coastline neighbours only
    need = set()
    for k, b in enumerate(land):
        if b["ibtype"] in FLUX_IBTYPES:
            need.update(b["nodes"])
            for nb in (land[k - 1] if k > 0 else None, land[k + 1] if k + 1 < len(land) else None):
                if nb:
                    need.update(nb["nodes"][:2] + nb["nodes"][-2:])
    xy = pd.read_csv(path, skiprows=2, nrows=np_, sep=r"\s+", header=None,
                     usecols=[0, 1, 2], names=["node", "lon", "lat"], engine="c")
    xy = xy[xy.node.isin(need)].set_index("node")
    return land, {n: (r.lon, r.lat) for n, r in xy.iterrows()}


def flux_boundaries(land, coords, width_rule):
    out = []
    for k, b in enumerate(land):
        if b["ibtype"] not in FLUX_IBTYPES:
            continue
        ids = b["nodes"]
        pts = [coords[n] for n in ids]
        length = sum(haversine_m(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
        width = length
        if width_rule == "tributary":
            prv, nxt = land[k - 1], land[k + 1]
            if prv["nodes"][-1] == ids[0]:
                width += haversine_m(*coords[prv["nodes"][-2]], *pts[0]) / 2
            if nxt["nodes"][0] == ids[-1]:
                width += haversine_m(*pts[-1], *coords[nxt["nodes"][1]]) / 2
        out.append({"segment": k + 1, "ibtype": b["ibtype"], "nodes": ids,
                    "lon": float(np.mean([p[0] for p in pts])),
                    "lat": float(np.mean([p[1] for p in pts])),
                    "length_m": length, "width_m": width})
    return out


def match_rivers(bounds, rivers):
    for b in bounds:
        d = [haversine_m(b["lon"], b["lat"], r["lon"], r["lat"]) / 1000 for r in rivers]
        i = int(np.argmin(d)) if d else -1
        b["river"] = rivers[i] if i >= 0 and d[i] <= MATCH_KM else None
        b["match_km"] = d[i] if i >= 0 else float("nan")
    return bounds


def fetch(url, path, offline):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    if offline:
        raise SystemExit(f"--offline but not cached: {path}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)
            return path
        except Exception as e:
            if attempt == 3:
                raise SystemExit(f"download failed: {url}\n  {e}")
            time.sleep(5 * (attempt + 1))


def read_rdb(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [l for l in f if not l.startswith("#")]
    if len(lines) < 3:
        return pd.DataFrame()
    return pd.read_csv(io.StringIO("".join([lines[0]] + lines[2:])), sep="\t", dtype=str)


def gauge_series(site, t0, t1, cache, offline):
    """Discharge (m3/s) indexed by UTC time, and a note on the data type."""
    a, b = t0.strftime("%Y-%m-%d"), t1.strftime("%Y-%m-%d")
    df = read_rdb(fetch(IV_URL.format(site=site, start=a, end=b),
                        os.path.join(cache, f"iv_{site}_{a}_{b}.rdb"), offline))
    if not df.empty:
        qcol = [c for c in df.columns if c.endswith("_00060")][0]
        off = df["tz_cd"].map(TZ_OFFSET_H)
        if off.isna().any():
            raise SystemExit(f"{site}: unknown time zone codes {df['tz_cd'].unique()}")
        t = pd.to_datetime(df["datetime"]) - pd.to_timedelta(off, unit="h")
        s = pd.Series(pd.to_numeric(df[qcol], errors="coerce").values * CFS_TO_CMS, index=t.values)
        note = "15-min"
    else:
        df = read_rdb(fetch(DV_URL.format(site=site, start=a, end=b),
                            os.path.join(cache, f"dv_{site}_{a}_{b}.rdb"), offline))
        if df.empty:
            raise SystemExit(f"{site}: no USGS discharge between {a} and {b}")
        qcol = [c for c in df.columns if c.endswith("_00060_00003")][0]
        t = pd.to_datetime(df["datetime"]) + pd.Timedelta(hours=17)  # local noon (EST) in UTC
        s = pd.Series(pd.to_numeric(df[qcol], errors="coerce").values * CFS_TO_CMS, index=t.values)
        note = "DAILY MEAN (no 15-min data)"
        print(f"  WARNING {site}: no instantaneous data, using daily means", file=sys.stderr)
    s = s.dropna()
    s = s[~s.index.duplicated()].sort_index()
    return s, note


def on_times(s, times):
    """Linear interpolation in time onto `times`; returns values and a gap report."""
    x = s.reindex(s.index.union(times)).interpolate("time", limit_area="inside").reindex(times)
    outside = int(x.isna().sum())
    if outside:  # before the first / after the last observation: hold the nearest value
        x = x.ffill().bfill()
    missing = int((~times.isin(s.index)).sum())
    inside = s[(s.index >= times[0]) & (s.index <= times[-1])]
    if len(inside) > 1:
        steps = inside.index.to_series().diff()
        longest = steps.max()
        longest_at = steps.idxmax() - longest
    else:
        longest, longest_at = pd.Timedelta(0), None
    return x.values, missing, outside, longest, longest_at


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--fort14", required=True)
    ap.add_argument("--start", help="cold start time, UTC, e.g. 2022-12-01T00:00")
    ap.add_argument("--end", help="end of the last run (cold start + RNDAY), UTC")
    ap.add_argument("--dt", type=float, default=900.0, help="record spacing FTIMINC in s (default 900)")
    ap.add_argument("--pad", type=int, default=1, help="extra records after --end (default 1)")
    ap.add_argument("--width", choices=["length", "tributary"], default="length")
    ap.add_argument("--rivers", help="JSON list replacing the built-in RIVERS table")
    ap.add_argument("--out", default="fort.20")
    ap.add_argument("--cache", default="usgs_cache", help="folder for raw USGS files")
    ap.add_argument("--offline", action="store_true", help="use only cached USGS files")
    ap.add_argument("--list", action="store_true", help="list flux boundaries and exit")
    args = ap.parse_args()

    rivers = RIVERS
    if args.rivers:
        with open(args.rivers) as f:
            rivers = json.load(f)

    print(f"reading {args.fort14} ...")
    land, coords = read_fort14_boundaries(args.fort14)
    bounds = match_rivers(flux_boundaries(land, coords, args.width), rivers)
    nflux = sum(len(b["nodes"]) for b in bounds)
    print(f"{len(bounds)} flux boundaries, {nflux} nodes")
    for b in bounds:
        name = b["river"]["name"] if b["river"] else "NO MATCH"
        print(f"  seg {b['segment']:4d} IBTYPE {b['ibtype']} {len(b['nodes']):3d} nodes "
              f"({b['lon']:.4f}, {b['lat']:.4f}) length {b['length_m']:7.1f} m "
              f"width {b['width_m']:7.1f} m -> {name} ({b['match_km']:.2f} km)")
    unmatched = [b for b in bounds if b["river"] is None]
    if args.list:
        return
    if unmatched:
        raise SystemExit("flux boundaries without a river entry (add them to --rivers): "
                         + ", ".join(f"seg {b['segment']} at ({b['lon']:.4f}, {b['lat']:.4f})"
                                     for b in unmatched))
    if not (args.start and args.end):
        raise SystemExit("--start and --end are required")

    t0, t1 = pd.Timestamp(args.start), pd.Timestamp(args.end)
    if t1 <= t0:
        raise SystemExit("--end must be after --start")
    nrec = int(math.ceil((t1 - t0).total_seconds() / args.dt)) + 1 + args.pad
    times = t0 + pd.to_timedelta(np.arange(nrec) * args.dt, unit="s")
    # one day of margin so interpolation at the ends uses real data
    d0, d1 = t0 - pd.Timedelta(days=1), times[-1] + pd.Timedelta(days=1)
    print(f"records: {nrec} x {nflux} values, {times[0]} to {times[-1]} UTC, every {args.dt:g} s")

    cache = {}
    table = pd.DataFrame(index=times)
    table.index.name = "time_utc"
    q_cols = []
    for b in bounds:
        r = b["river"]
        Q = np.zeros(nrec)
        for site in r["gauges"]:
            if site not in cache:
                cache[site] = gauge_series(site, d0, d1, args.cache, args.offline)
            s, note = cache[site]
            v, missing, outside, longest, longest_at = on_times(s, times)
            Q += v
            msg = f"  {r['name']:34s} {site:>10s} {note}, mean {np.mean(v):9.2f} m3/s"
            if missing:
                msg += f", {missing} record times interpolated"
            if longest > pd.Timedelta(hours=GAP_WARN_H):
                msg += (f", WARNING longest gap {longest} from {longest_at:%Y-%m-%d %H:%M} UTC "
                        f"(linear fill)")
            if outside:
                msg += f", WARNING {outside} records outside the data (held constant)"
            print(msg)
        Q *= float(r.get("scale", 1.0))
        table[f"{r['name']} (m3/s)"] = Q
        q_cols.append((Q / b["width_m"], len(b["nodes"])))

    with open(args.out, "w") as f:
        f.write(f"{args.dt:.1f}\n")
        for k in range(nrec):
            for q, n in q_cols:
                f.write(f"{q[k]:.6f}\n" * n)
    csv = os.path.splitext(args.out)[0] + "_discharge.csv"
    table.to_csv(csv, float_format="%.4f")
    print(f"wrote {args.out} ({nrec} records, FTIMINC {args.dt:g} s) and {csv}")
    print(f"fort.15: base_date must be {t0:%Y-%m-%d %H:%M:%S}, NFFR 0, "
          f"RNDAY <= {(times[-1] - t0).total_seconds() / 86400:g}")


if __name__ == "__main__":
    main()
