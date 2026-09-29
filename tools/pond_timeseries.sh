#!/bin/bash
# Run report_brief/pond_timeseries.py on Unity against a run's full-domain fort.63.nc.
#
#   bash report_brief/pond_timeseries.sh                        # the default run below
#   bash report_brief/pond_timeseries.sh /path/to/fort.63.nc [outdir]
#
# Reading a full fort.63.nc (tens of GB) is I/O bound, not CPU bound; a few GB of RAM
# is enough because only the selected nodes are read per time block. --map adds one
# full pass over the field to get peak zeta for the zoomed mesh maps; leave it on, it
# is the picture that shows whether an inlet is connected. Submit with sbatch or run
# inside salloc if the login node is too slow. Needs numpy netCDF4 matplotlib in the
# active Python environment (the same one used for generateGraphs.py).
set -euo pipefail
DEFAULT_FORT63=/scratch4/workspace/arash_rafiee_uri_edu-richamp/ecflow_output/ricv1/archive/20260924/hour_12/adcirc/forecast/forecast_base/fort.63.nc
FORT63="${1:-$DEFAULT_FORT63}"
OUT="${2:-pond_out}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$FORT63" ] || { echo "not found: $FORT63" >&2; exit 1; }
python "$HERE/pond_timeseries.py" "$FORT63" --points "$HERE/pond_points.json" --map --block 24 --out "$OUT"
echo "done: $OUT"
