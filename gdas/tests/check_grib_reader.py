#!/usr/bin/env python3
"""Check that this environment decodes GDAS GRIB2 correctly (no pytest needed).

Downloads one archived hour (2025-10-28 01 UTC, ~2.8 MB from NOAA's AWS archive) and
compares values at fixed 0.25 deg grid points with reference values decoded with
eccodes. Run from the repository root, e.g. on the cluster:

    python gdas/tests/check_grib_reader.py              # reader picked like download_gdas.py
    python gdas/tests/check_grib_reader.py builtin      # force the numpy decoder
"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdas_source import Downloader, grib_reader, hourly_timeline, read_hour  # noqa: E402

# (lat, lon, u10 m/s, v10 m/s, prmsl Pa) from gdas.20251028/00 f001, decoded with eccodes.
REFERENCE = [
    (41.5, -71.5, -0.8770, -2.0921, 102704.84),
    (17.75, -77.25, -14.4170, 1.6579, 99963.64),
    (25.25, -90.25, -2.6770, -7.0221, 101117.04),
    (3.0, -98.0, 2.0930, 6.5079, 101099.04),
    (47.0, 4.0, 1.6630, 0.6679, 101915.64),
    (36.0, -5.5, -5.0170, -1.8621, 101936.24),
    (10.0, -30.0, -6.2470, -2.7721, 101400.84),
]


def main() -> int:
    reader = grib_reader(sys.argv[1] if len(sys.argv) > 1 else "auto")
    h = hourly_timeline(dt.datetime(2025, 10, 28, 1), dt.datetime(2025, 10, 28, 1))[0]
    with tempfile.TemporaryDirectory() as tmp:
        dl = Downloader(Path(tmp), "aws", (-99.0, 2.0, 5.0, 48.0), retries=3)
        native = read_hour(dl.fetch(h), h, reader)
    print(f"GRIB reader: {reader}; grid {native.data['u10'].shape}, "
          f"lon {native.lon[0]}..{native.lon[-1]}, lat {native.lat[0]}..{native.lat[-1]}")
    bad = 0
    for lat, lon, u, v, p in REFERENCE:
        j, i = np.flatnonzero(native.lat == lat), np.flatnonzero(native.lon == lon)
        if len(j) != 1 or len(i) != 1:
            print(f"FAIL ({lat}, {lon}) is not a grid point")
            bad += 1
            continue
        got = [native.data[k][j[0], i[0]] for k in ("u10", "v10", "mslp")]
        ok = abs(got[0] - u) < 1e-3 and abs(got[1] - v) < 1e-3 and abs(got[2] - p) < 0.1
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'} ({lat:6.2f}, {lon:7.2f})  U {got[0]:8.3f} ({u:8.3f})  "
              f"V {got[1]:8.3f} ({v:8.3f})  P {got[2]:10.2f} ({p:10.2f})")
    print("GRIB reader check:", "PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
