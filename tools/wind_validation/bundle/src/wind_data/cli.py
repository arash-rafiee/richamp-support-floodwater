"""Command-line helpers shared by the scripts in ``scripts/``.

Keeping argument handling here means ``download_gfs.py`` and
``download_gdas.py`` differ only in which source they pass in.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

import pandas as pd

from wind_data.config import Settings, load_settings
from wind_data.download import common
from wind_data.processing.time import cycle_range

log = logging.getLogger("wind_data")


def parse_hours(spec: str) -> list[int]:
    """Parse an hour list such as ``"0-24:3,36,48"``.

    Items are comma-separated. ``a-b`` is an inclusive range with step 1;
    ``a-b:s`` uses step ``s``.

    >>> parse_hours("0-12:6,24")
    [0, 6, 12, 24]
    """
    hours: set[int] = set()
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        rng, _, step = item.partition(":")
        start, _, end = rng.partition("-")
        if end:
            hours.update(range(int(start), int(end) + 1, int(step or 1)))
        else:
            if step:
                raise ValueError(f"step given without a range in {item!r}")
            hours.add(int(start))
    if not hours:
        raise ValueError(f"no hours in {spec!r}")
    return sorted(hours)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments every script accepts."""
    parser.add_argument("--config", help="settings TOML file (default: config/settings.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every HTTP request")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Our own progress messages are always shown; per-request HTTP logging from
    # the download layer (a child logger) only with --verbose.
    log.setLevel(logging.INFO)
    logging.getLogger("wind_data.download").setLevel(logging.INFO if verbose else logging.WARNING)


def cycles_from_args(start: str, end: str | None) -> pd.DatetimeIndex:
    """Cycles from ``start`` to ``end`` (or just ``start``), UTC."""
    cycles = cycle_range(start, end or start)
    if cycles.empty:
        raise SystemExit(f"no model cycles between {start} and {end or start}")
    return cycles


def download_main(spec: common.SourceSpec, default_hours: str, argv: Sequence[str] | None = None) -> int:
    """Entry point for the download scripts. Returns a process exit code.

    A missing file (not yet published, or before the archive starts) is
    reported and skipped, so one gap does not abort a long batch. The exit
    code is 1 if anything failed.
    """
    name = spec.name.upper()
    parser = argparse.ArgumentParser(
        description=f"Download {name} 10-m wind (UGRD/VGRD) from the NOAA AWS archive.",
        epilog="Times are UTC. Example: --start 2024-01-15 --end 2024-01-16T18 --hours 0-24:6",
    )
    parser.add_argument("--start", required=True, help="first cycle, e.g. 2024-01-15 or 2024-01-15T06")
    parser.add_argument("--end", help="last cycle (default: same as --start)")
    parser.add_argument("--hours", default=default_hours,
                        help=f"forecast hours, e.g. '0-24:3,48' (default: {default_hours})")
    parser.add_argument("--resolution", help="0p25, 0p50 or 1p00 (default: from config)")
    parser.add_argument("--overwrite", action="store_true", help="re-download files already cached")
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    settings: Settings = load_settings(args.config)
    resolution = args.resolution or settings.resolution
    try:
        spec.check_resolution(resolution)
        hours = [spec.check_forecast_hour(h) for h in parse_hours(args.hours)]
    except ValueError as err:
        parser.error(str(err))

    cycles = cycles_from_args(args.start, args.end)
    total = len(cycles) * len(hours)
    log.info("%s: %d cycles x %d hours = %d files -> %s", name, len(cycles), len(hours), total,
             settings.data_dir / "raw" / spec.name)

    session = common.make_session()
    failed = 0
    for n, (cycle, hour) in enumerate(((c, h) for c in cycles for h in hours), start=1):
        try:
            path = common.download_wind(spec, cycle, hour, data_dir=settings.data_dir,
                                        resolution=resolution, overwrite=args.overwrite, session=session)
            log.info("[%d/%d] %s", n, total, path.name)
        except FileNotFoundError as err:
            failed += 1
            log.warning("[%d/%d] missing: %s", n, total, err)
        except Exception as err:  # keep going; report at the end
            failed += 1
            log.error("[%d/%d] %s %s f%03d failed: %s", n, total, name, cycle, hour, err)

    log.info("%s: %d of %d files available, %d failed", name, total - failed, total, failed)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit("run scripts/download_gfs.py or scripts/download_gdas.py instead")
