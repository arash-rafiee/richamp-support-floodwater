"""Station wind figure from a wind CSV, drawn by Grapher.drawStationWindFigure (the same figure as
generateGraphs.py --windFigureExists): speed, the direction the wind comes from, a day-by-hour barb grid
(m/s: half feather 5, full 10, flag 50) and a wind rose, under the water-graph heading.

Input: a CSV with station, time, u10, v10, speed_ms, dir_from_deg (from gfs_wind_series.py or
netcdf_wind_series.py). numpy, matplotlib and the repository's Grapher.py only.
Usage: python wind_figure.py <wind_csv> "<station>" <out.png> [forcing name, default GFS]"""
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the repository root, for Grapher.py
from Grapher import bannerSubtitle, drawStationWindFigure      # noqa: E402

NOAA_IDS = {"Providence, RI": "8454000", "Conimicut Light, RI": "8452944", "Quonset Pt, RI": "8454049",
            "Newport, RI": "8452660"}

if len(sys.argv) not in (4, 5):
    sys.exit(__doc__)
CSV, STATION, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
FORCING = sys.argv[4] if len(sys.argv) == 5 else "GFS"

with open(CSV, newline="") as f:
    rows = [r for r in csv.DictReader(f) if r["station"] == STATION]
if not rows:
    sys.exit(f"no rows for {STATION!r} in {CSV}")
times = [datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) for r in rows]
column = lambda name: [float(r[name]) for r in rows]

title = STATION + (" — Station " + NOAA_IDS[STATION] if STATION in NOAA_IDS else "")
drawn = drawStationWindFigure(OUT, times, column("speed_ms"), column("dir_from_deg"), column("u10"),
                              column("v10"), title, bannerSubtitle(FORCING, times[0], times[-1]))
print(OUT if drawn else f"no valid wind for {STATION}")
