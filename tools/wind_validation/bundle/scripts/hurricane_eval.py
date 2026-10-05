"""Evaluate GFS and GDAS 10-m winds against observations during a hurricane.

Configured by config/melissa.toml (Hurricane Melissa, AL132025). Run the
stages in order, or all at once:

    python scripts/hurricane_eval.py track          # NHC best track, period, domain
    python scripts/hurricane_eval.py stations       # stations within the radius
    python scripts/hurricane_eval.py observations   # download, parse, QC
    python scripts/hurricane_eval.py adjust         # height adjustment, storm-relative
    python scripts/hurricane_eval.py models         # GFS/GDAS download and crop
    python scripts/hurricane_eval.py collocate      # matched dataset
    python scripts/hurricane_eval.py evaluate       # metrics tables
    python scripts/hurricane_eval.py report         # figures and report.md
    python scripts/hurricane_eval.py all

--smoke runs a small end-to-end test (48 h around the Jamaica landfall, the
three NDBC and CO-OPS stations nearest the track, no GHCNh, hourly GDAS and
GFS lead windows f000-f005 and f024-f029)
into a separate "smoke" run folder, reusing the shared download cache.
Times are UTC.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib  # noqa: E402
import pandas as pd  # noqa: E402

matplotlib.use("Agg")  # file output only

from wind_data.hurricane.config import load_config  # noqa: E402
from wind_data.hurricane.pipeline import STAGES, Run  # noqa: E402

log = logging.getLogger("wind_data")


def smoke(cfg):
    return dataclasses.replace(
        cfg, run_name="smoke", period_override=("2025-10-27T12:00", "2025-10-29T12:00"),
        obs_sources=tuple(s for s in cfg.obs_sources if s != "ghcnh"), max_stations_per_source=3,
        gfs_lead_windows=(0, 24), min_samples=3,
    )


def period_run(cfg, start: str, end: str, run_name: str | None, stations: str | None = None):
    """Any UTC period, for the requested NDBC stations, or for ``stations``
    ("source:id,source:id,...") when given.

    Outside the storm's lifetime the storm-relative parts (track map,
    distance and quadrant breakdowns) are left out of the figures and report.
    """
    name = run_name or f"{pd.Timestamp(start):%Y%m%d}_{pd.Timestamp(end):%Y%m%d}"
    if stations:
        include = tuple(k.strip() for k in stations.split(",") if k.strip())
    else:
        include = tuple(k for k in cfg.include if k.startswith("ndbc:"))
    sources = tuple(s for s in cfg.obs_sources if any(k.startswith(f"{s}:") for k in include))
    return dataclasses.replace(cfg, run_name=name, output_group="periods", period_override=(start, end),
                               obs_sources=sources, radius_km=0.0, include=include)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=(*STAGES, "all"), help="pipeline stage to run")
    parser.add_argument("--config", default="config/melissa.toml", help="TOML file (default: config/melissa.toml)")
    parser.add_argument("--smoke", action="store_true", help="small end-to-end test into the 'smoke' run folder")
    parser.add_argument("--start", help="period start (UTC, e.g. 2026-09-20T00:00): requested NDBC stations only, "
                                        "results under outputs/hurricane/periods/<run name>")
    parser.add_argument("--end", help="period end (UTC); required with --start")
    parser.add_argument("--run-name", help="folder name for a --start/--end run (default: <start>_<end>)")
    parser.add_argument("--stations", help="with --start/--end: comma-separated 'source:id' stations instead of the "
                                           "requested NDBC list, e.g. coops:8452660,coops:8454000")
    parser.add_argument("--highlight", help="comma-separated 'source:id' stations to ring on the station map")
    parser.add_argument("--region-name", help="name of the station map's detail panel, e.g. 'Narragansett Bay'")
    parser.add_argument("--overwrite", action="store_true", help="re-download the track / model files")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every download")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    log.setLevel(logging.INFO)
    if not args.verbose:
        logging.getLogger("wind_data.hurricane.fetch").setLevel(logging.WARNING)

    cfg = load_config(args.config)
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end go together")
    if args.smoke and args.start:
        parser.error("--smoke and --start/--end are separate runs")
    if args.smoke:
        cfg = smoke(cfg)
    if args.start:
        cfg = period_run(cfg, args.start, args.end, args.run_name, args.stations)
    elif args.stations:
        parser.error("--stations needs --start/--end")
    if args.highlight or args.region_name:
        cfg = dataclasses.replace(
            cfg, map_highlight=tuple(k.strip() for k in (args.highlight or "").split(",") if k.strip()),
            map_region_name=args.region_name)
    run = Run(cfg)
    stages = STAGES if args.stage == "all" else (args.stage,)
    run.run(stages, overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
