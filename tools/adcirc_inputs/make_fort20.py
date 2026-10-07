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
Record 0 is at the COLD START time. ADCIRC skips forward through the records on
a hot start, so one file made for the whole cold + hot start period serves
both runs.

Start and end dates (read from fort.15 unless --start/--end are given):
    start = base_date   the metadata block at the end of fort.15
                        (the 10th line after the ITITER line, e.g. 2022-12-01 00:00:00);
                        STATIM must be 0, i.e. the run starts at base_date
    end   = base_date + RNDAY
Give the HOT-START fort.15 (--fort15): it has the same base_date and the
RNDAY of the whole period (e.g. 29 d), so the file also covers the hot start.
The cold-start fort.15 (e.g. RNDAY 19) would give a file that ends too early.
--start / --end override the fort.15 values, e.g. when base_date is missing.

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
    # fort.14 and fort.15 of the hot-start run folder; dates from its fort.15
    python tools/adcirc_inputs/make_fort20.py --run-dir /path/to/hotstart_run --out fort.20

    # mesh and control file in different places
    python tools/adcirc_inputs/make_fort20.py --fort14 mesh/fort.14 --fort15 hot/fort.15

    # no fort.15 (or no base_date in it): give the dates yourself
    python tools/adcirc_inputs/make_fort20.py --fort14 fort.14 \\
        --start 2022-12-01T00:00 --end 2022-12-30T00:00

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
import re
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


def read_fort15(path):
    """base_date, STATIM, RNDAY, IHOT and NFFR from a fort.15 (None if absent).

    Numeric values are found by their usual trailing comments (! RNDAY ...);
    base_date is the last line after the ITITER line that is a date/time.
    """
    with open(path, "r", errors="replace") as f:
        raw = [l.rstrip("\r\n") for l in f]

    def labelled(label, cast):
        pat = re.compile(r"!\s*" + label + r"\b", re.IGNORECASE)
        for l in raw:
            if pat.search(l):
                return cast(l.split("!")[0].split()[0])
        return None

    out = {k: labelled(k, c) for k, c in
           (("IHOT", int), ("STATIM", float), ("RNDAY", float), ("NFFR", int))}
    out["base_date"] = None
    ititer = [i for i, l in enumerate(raw) if re.search(r"!\s*ITITER\b", l, re.IGNORECASE)]
    date_re = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?)\s*$")
    for l in raw[ititer[0] + 1:] if ititer else []:
        m = date_re.match(l)
        if m:
            out["base_date"] = pd.Timestamp(m.group(1))
    return out


def run_period(args):
    """(start, end) in UTC from --start/--end, else from fort.15."""
    f15 = read_fort15(args.fort15) if args.fort15 else None
    if f15:
        print(f"fort.15 {args.fort15}: base_date {f15['base_date']}, IHOT {f15['IHOT']}, "
              f"STATIM {f15['STATIM']}, RNDAY {f15['RNDAY']}, NFFR {f15['NFFR']}")
        if f15["NFFR"] not in (0, -1):
            print(f"  WARNING NFFR = {f15['NFFR']}: ADCIRC reads fort.20 as non-periodic flux "
                  "only with NFFR 0 (or -1)", file=sys.stderr)
        if f15["IHOT"] == 0 and not args.end:
            print("  NOTE this is a cold-start fort.15: the file ends at its RNDAY. For a cold + "
                  "hot start pair give the hot-start fort.15 (or --end).", file=sys.stderr)
    start = pd.Timestamp(args.start) if args.start else None
    end = pd.Timestamp(args.end) if args.end else None
    if start is None:
        if not f15 or f15["base_date"] is None:
            raise SystemExit("no start date: fort.15 has no base_date line after ITITER "
                             "(or no --fort15/--run-dir); give --start")
        if f15["STATIM"] not in (None, 0.0):
            raise SystemExit(f"STATIM = {f15['STATIM']} in fort.15; record 0 of fort.20 must be "
                             "the cold-start time, give --start explicitly")
        start = f15["base_date"]
    if end is None:
        if not f15 or f15["RNDAY"] is None:
            raise SystemExit("no end date: no RNDAY found in fort.15; give --end")
        base = f15["base_date"] if f15["base_date"] is not None else start
        end = base + pd.Timedelta(days=f15["RNDAY"])
    print(f"period: {start} -> {end} UTC (start from {'--start' if args.start else 'fort.15'}, "
          f"end from {'--end' if args.end else 'fort.15'})")
    return start, end


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
    ap.add_argument("--run-dir", help="folder with fort.14 and fort.15 (the hot-start run)")
    ap.add_argument("--fort14", help="mesh (default: <run-dir>/fort.14)")
    ap.add_argument("--fort15", help="control file for the dates (default: <run-dir>/fort.15)")
    ap.add_argument("--start", help="override: cold start time, UTC, e.g. 2022-12-01T00:00")
    ap.add_argument("--end", help="override: end of the last run, UTC")
    ap.add_argument("--dt", type=float, default=900.0, help="record spacing FTIMINC in s (default 900)")
    ap.add_argument("--pad", type=int, default=1, help="extra records after --end (default 1)")
    ap.add_argument("--width", choices=["length", "tributary"], default="length")
    ap.add_argument("--rivers", help="JSON list replacing the built-in RIVERS table")
    ap.add_argument("--out", default="fort.20")
    ap.add_argument("--cache", default="usgs_cache", help="folder for raw USGS files")
    ap.add_argument("--offline", action="store_true", help="use only cached USGS files")
    ap.add_argument("--list", action="store_true", help="list flux boundaries and exit")
    args = ap.parse_args()
    if args.run_dir:
        args.fort14 = args.fort14 or os.path.join(args.run_dir, "fort.14")
        if not args.fort15 and os.path.exists(os.path.join(args.run_dir, "fort.15")):
            args.fort15 = os.path.join(args.run_dir, "fort.15")
    if not args.fort14:
        raise SystemExit("give --run-dir or --fort14")
    for p in (args.fort14, args.fort15):
        if p and not os.path.exists(p):
            raise SystemExit(f"not found: {p}")

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
    t0, t1 = run_period(args)
    if t1 <= t0:
        raise SystemExit(f"end {t1} is not after start {t0}")
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

    # read the file back: FTIMINC on line 1, then exactly nrec x nflux values
    with open(args.out) as f:
        ftiminc = float(f.readline())
        nvals = sum(1 for _ in f)
    if ftiminc != args.dt or nvals != nrec * nflux:
        raise SystemExit(f"check failed: FTIMINC {ftiminc} (want {args.dt}), "
                         f"{nvals} values (want {nrec} x {nflux})")
    print(f"wrote {args.out} ({nrec} records x {nflux} nodes, FTIMINC {args.dt:g} s, "
          f"checked) and {csv}")
    print(f"fort.15: base_date must be {t0:%Y-%m-%d %H:%M:%S}, NFFR 0, "
          f"RNDAY <= {(times[-1] - t0).total_seconds() / 86400:g}")


if __name__ == "__main__":
    main()
