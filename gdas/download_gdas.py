#!/usr/bin/env python3
"""Build hourly OWI .wnd/.pre forcing from NOAA/NCEP GDAS.

GDAS-path counterpart of ``metget build --domain gfs ... --format owi-ascii``.
The output is meant to go straight into OceanweatherTo306.py and owi2wind.py.

Example (from the repository root):

    python gdas/download_gdas.py --domain 0.1 -98.0 3.0 4.0 47.0 \\
        --start "2025-10-28 00:00" --end "2025-11-04 00:00" \\
        --timestep 3600 --output Oct25

writes Oct25_00_00.wnd and Oct25_00_00.pre. See gdas/README.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from gdas_source import NATIVE_RES, Downloader, grib_reader, hourly_timeline, read_hour
from grid import Bilinear, TargetGrid
from owi import LIMITS, OwiWriter, compare_with_reference, downstream_grid_size, scan_owi_file, title_line

HERE = Path(__file__).resolve().parent
log = logging.getLogger("gdas")


def valid_datetime(s: str) -> dt.datetime:
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date {s!r}; expected 'YYYY-MM-DD HH:MM' (UTC)")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Download NOAA GDAS (0.25 deg, hourly) U10/V10/PRMSL and write OWI .wnd/.pre "
                    "on the requested regular grid.")
    p.add_argument("--domain", nargs=5, required=True, metavar=("resolution", "x0", "y0", "x1", "y1"),
                   help="Target grid, as in MetGet without the model name, e.g. 0.1 -98.0 3.0 4.0 47.0")
    p.add_argument("--start", type=valid_datetime, required=True, help="'YYYY-MM-DD HH:MM' UTC")
    p.add_argument("--end", type=valid_datetime, required=True, help="'YYYY-MM-DD HH:MM' UTC (inclusive)")
    p.add_argument("--timestep", type=int, default=3600,
                   help="Output time step in seconds. Only 3600 is supported (OceanweatherTo306.py assumes it)")
    p.add_argument("--output", required=True, help="Base name; writes <output>_00_00.wnd/.pre")
    p.add_argument("--variable", default="wind_pressure", choices=["wind_pressure"],
                   help="Only wind_pressure (10 m U/V + mean sea level pressure)")
    p.add_argument("--format", default="owi-ascii", choices=["owi-ascii"], help="Only owi-ascii")
    p.add_argument("--output-dir", type=Path, default=Path("."), help="Where .wnd/.pre are written (default: .)")
    p.add_argument("--cache-dir", type=Path, default=HERE / "cache",
                   help="Downloaded GRIB2 subsets, reused across runs (default: gdas/cache)")
    p.add_argument("--work-dir", type=Path, default=HERE / "work",
                   help="Logs and failed-URL list (default: gdas/work)")
    p.add_argument("--source", choices=["auto", "nomads", "aws"], default="auto",
                   help="auto: NOMADS filter for cycles < 9 days old, otherwise NOAA's AWS archive")
    p.add_argument("--buffer", type=float, default=1.0,
                   help="Extra source-grid margin in degrees around the domain (default: 1.0)")
    p.add_argument("--workers", type=int, default=4, help="Parallel downloads (default: 4)")
    p.add_argument("--retries", type=int, default=5, help="Attempts per HTTP request (default: 5)")
    p.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout in seconds (default: 120)")
    p.add_argument("--reference-wnd", type=Path, help="Existing MetGet .wnd to compare structure with")
    p.add_argument("--reference-pre", type=Path, help="Existing MetGet .pre to compare structure with")
    p.add_argument("--grib-reader", choices=["auto", "eccodes", "rasterio"], default="auto",
                   help="GRIB2 decoder. auto: eccodes if installed, otherwise rasterio (GDAL)")
    p.add_argument("--dry-run", action="store_true", help="Print the hourly source plan and exit")
    return p.parse_args(argv)


def setup_logging(work_dir: Path):
    work_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(work_dir / "download_gdas.log")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def download_bounds(grid: TargetGrid, buffer: float):
    """Domain plus buffer, snapped outward to the native 0.25 deg grid."""
    snap_dn = lambda x: math.floor(x / NATIVE_RES) * NATIVE_RES
    snap_up = lambda x: math.ceil(x / NATIVE_RES) * NATIVE_RES
    return (max(-180.0, snap_dn(grid.west - buffer)), max(-90.0, snap_dn(grid.south - buffer)),
            min(180.0, snap_up(grid.east + buffer)), min(90.0, snap_up(grid.north + buffer)))


def download_all(dl: Downloader, hours, workers: int) -> dict:
    paths, failures = {}, []
    with ThreadPoolExecutor(max(1, workers)) as ex:
        futures = {ex.submit(dl.fetch, h): h for h in hours}
        for i, fut in enumerate(as_completed(futures), 1):
            h = futures[fut]
            try:
                paths[h] = fut.result()
            except Exception as e:  # collect all failures, report together
                failures.append(f"  {h.label}: {e}")
            if i % 24 == 0 or i == len(hours):
                log.info("Downloads: %d/%d hours done", i, len(hours))
    if failures:
        raise RuntimeError("Missing GDAS hours (no gap filling is done):\n" + "\n".join(sorted(failures)))
    return paths


def build(args, grid: TargetGrid, hours, dl: Downloader, paths: dict, wnd: Path, pre: Path) -> OwiWriter:
    regridders = {}
    with OwiWriter(wnd, pre, grid, hours[0].valid, hours[-1].valid) as writer:
        for n, h in enumerate(hours, 1):
            try:
                native = read_hour(paths[h], h, args.grib_reader)
            except Exception as e:
                log.warning("Cannot decode %s (%s); downloading again", paths[h], e)
                paths[h].unlink(missing_ok=True)
                native = read_hour(dl.fetch(h), h, args.grib_reader)
            key = (native.lat[0], native.lat[-1], len(native.lat), native.lon[0], native.lon[-1], len(native.lon))
            if key not in regridders:
                regridders[key] = Bilinear(native.lat, native.lon, grid.lats, grid.lons)
            rg = regridders[key]
            for name, arr in native.data.items():
                sub = arr[rg.j0.min():rg.j0.max() + 2, rg.i0.min():rg.i0.max() + 2]
                if not np.all(np.isfinite(sub)):
                    raise ValueError(f"{name} has missing values inside the domain at {h.label}")
            writer.write(h.valid, rg(native.data["u10"]), rg(native.data["v10"]),
                         rg(native.data["mslp"]) / 100.0)  # Pa -> hPa
            if n % 24 == 0 or n == len(hours):
                log.info("Regridded and written: %d/%d hours", n, len(hours))
    return writer


def validate(args, grid, hours, writer, wnd: Path, pre: Path):
    """Re-read the written files the way the downstream tools do. Returns (ok, report dict)."""
    errors = []
    sw, sp = scan_owi_file(wnd, "wnd", grid), scan_owi_file(pre, "pre", grid)
    errors += sw.errors + sp.errors

    expected = [h.valid for h in hours]
    title = title_line(hours[0].valid, hours[-1].valid).rstrip("\n")
    for s in (sw, sp):
        if s.title != title:
            errors.append(f"{s.path.name}: title line {s.title!r}, expected {title!r}")
        if s.times != expected:
            errors.append(f"{s.path.name}: {len(s.times)} time slices do not match the {len(expected)} expected")
        if len(set(s.times)) != len(s.times):
            errors.append(f"{s.path.name}: duplicate timestamps")
        steps = {(b - a).total_seconds() for a, b in zip(s.times, s.times[1:])}
        if steps and steps != {float(args.timestep)}:
            errors.append(f"{s.path.name}: time steps {sorted(steps)} s, expected {args.timestep}")
    if sw.header_params != sp.header_params:  # OceanweatherTo306.py compares these
        errors.append("last .wnd and .pre headers differ (OceanweatherTo306.py would reject them)")

    # owi2wind.py rebuilds the grid from Wind_Inp.txt (bounds .1f, resolution fixed at 0.1).
    if downstream_grid_size(grid) != (grid.ny, grid.nx):
        errors.append(f"owi2wind.py would rebuild this grid as NY,NX={downstream_grid_size(grid)} "
                      f"instead of {grid.ny},{grid.nx}; choose a 0.1 deg domain it reconstructs exactly")

    # Orientation self-check: what the file holds at the corners == what was written.
    first = writer.first_slice
    for name, s in (("u10", sw), ("v10", sw), ("mslp", sp)):
        arr = s.first_block.get(name)
        if arr is None:
            continue
        for (j, i) in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
            if abs(arr[j, i] - round(first[name][j, i], 4)) > 1e-4:
                errors.append(f"{name}: corner ({j},{i}) mismatch after re-reading; orientation error")

    var_status = {}
    for name, s in (("u10", sw), ("v10", sw), ("mslp", sp)):
        st = s.stats.get(name)
        lo, hi = LIMITS[name]
        ok = st is not None and st.nan == 0 and st.count > 0 and lo <= st.min and st.max <= hi
        if st is not None and not ok:
            errors.append(f"{name}: range [{st.min:.2f}, {st.max:.2f}] or NaNs ({st.nan}) outside [{lo}, {hi}]")
        var_status[name] = (ok, st)

    ref_notes = []
    for ours, ref in ((wnd, args.reference_wnd), (pre, args.reference_pre)):
        if ref:
            probs = compare_with_reference(ours, ref)
            ref_notes.append((ref, probs))
            errors += [f"vs {ref.name}: {p}" for p in probs]

    return not errors, {"errors": errors, "vars": var_status, "times": sw.times, "refs": ref_notes}


def print_summary(grid, hours, sources_used, reader, wnd, pre, ok, rep, elapsed):
    t = rep["times"]
    cycles = sorted({f"{h.cycle:%H}" for h in hours})
    lines = [
        "", "GDAS forcing successfully created" if ok else "GDAS forcing FAILED validation", "",
        "Time", "----",
        f"Start:       {t[0]:%Y-%m-%d %H:%M} UTC" if t else "Start:       -",
        f"End:         {t[-1]:%Y-%m-%d %H:%M} UTC" if t else "End:         -",
        f"Interval:    {int((t[1] - t[0]).total_seconds()) if len(t) > 1 else '-'} s",
        f"Records:     {len(t)}", "",
        "Source", "------",
        "Dataset:     NOAA/NCEP GDAS pgrb2.0p25 (UGRD/VGRD 10 m, PRMSL)",
        f"Cycles:      {'/'.join(cycles)} (f000 analysis + f001-f005 hourly forecasts)",
        f"Resolution:  {NATIVE_RES} deg -> bilinear",
        f"GRIB reader: {reader}",
        f"Fetched via: {', '.join(sorted(sources_used))}", "",
        "Target grid", "-----------",
        f"West:        {grid.west:g}", f"East:        {grid.east:g}",
        f"South:       {grid.south:g}", f"North:       {grid.north:g}",
        f"Resolution:  {grid.res:g}", f"NX:          {grid.nx}", f"NY:          {grid.ny}", "",
        "Variables", "---------",
    ]
    units = {"u10": "m/s", "v10": "m/s", "mslp": "hPa"}
    for name, label in (("u10", "U10"), ("v10", "V10"), ("mslp", "MSLP")):
        vok, st = rep["vars"][name]
        rng = f"min {st.min:9.2f}  max {st.max:9.2f}  mean {st.mean:9.2f} {units[name]}" if st and st.count else ""
        lines.append(f"{label + ':':<13}{'PASS' if vok else 'FAIL'}   {rng}")
    for ref, probs in rep["refs"]:
        lines.append(f"Structure vs {ref.name}: {'PASS' if not probs else 'FAIL'}")
    lines += ["", "Outputs", "-------", str(wnd), str(pre), ""]
    if rep["errors"]:
        lines += ["Problems", "--------"] + [f"- {e}" for e in rep["errors"][:30]] + [""]
    lines += [f"Validation: {'PASS' if ok else 'FAIL'}   ({elapsed:.0f} s)"]
    print("\n".join(lines), flush=True)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError) as e:
        if log.handlers:
            log.error("%s", e)
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2


def run(args) -> int:
    if args.timestep != 3600:
        sys.exit("Only --timestep 3600 is supported: GDAS is used at its native hourly output "
                 "and OceanweatherTo306.py assumes 3600 s.")
    grid = TargetGrid.from_domain(*args.domain)
    hours = hourly_timeline(args.start, args.end)
    setup_logging(args.work_dir)

    if args.dry_run:
        print(f"Target grid: NX={grid.nx} NY={grid.ny} lon {grid.west}..{grid.east} "
              f"lat {grid.south}..{grid.north} res {grid.res}")
        for h in hours:
            print(h.label)
        return 0

    t0 = time.time()
    args.grib_reader = grib_reader(args.grib_reader)
    bounds = download_bounds(grid, args.buffer)
    dl = Downloader(args.cache_dir, args.source, bounds, args.retries, args.timeout,
                    failed_log=args.work_dir / "failed_urls.log")
    log.info("GDAS %s -> %s: %d hourly records, source=%s, cache=%s, GRIB reader=%s",
             args.start, args.end, len(hours), args.source, args.cache_dir, args.grib_reader)
    log.info("Target grid NX=%d NY=%d; download box lon %g..%g lat %g..%g",
             grid.nx, grid.ny, bounds[0], bounds[2], bounds[1], bounds[3])
    paths = download_all(dl, hours, args.workers)
    sources_used = set(dl.used.values())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    wnd = args.output_dir / f"{args.output}_00_00.wnd"
    pre = args.output_dir / f"{args.output}_00_00.pre"
    writer = build(args, grid, hours, dl, paths, wnd, pre)

    log.info("Validating %s and %s", wnd.name, pre.name)
    ok, rep = validate(args, grid, hours, writer, wnd, pre)
    print_summary(grid, hours, sources_used, args.grib_reader, wnd, pre, ok, rep, time.time() - t0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
