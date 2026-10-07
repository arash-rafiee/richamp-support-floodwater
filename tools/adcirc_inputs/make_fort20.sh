#!/bin/bash
#SBATCH --job-name=make_fort20
#SBATCH --output=make_fort20_%j.out
#SBATCH --error=make_fort20_%j.err
#SBATCH -p uri-cpu
#SBATCH -c 1
#SBATCH --mem=8G
#SBATCH -t 00:30:00
##SBATCH --mail-user=arash_rafiee@uri.edu
##SBATCH --mail-type=END,FAIL
#
# USGS 15-min discharge -> ADCIRC fort.20 (river flux) with make_fort20.py.
#
# Submit from the repository root (arguments are optional; defaults below):
#   sbatch tools/adcirc_inputs/make_fort20.sh /path/to/fort.14 "2022-12-01 00:00" "2022-12-30 00:00"
# or run interactively the same way with bash instead of sbatch.
#
#   START  cold start time in UTC (= base_date in fort.15); record 0 of fort.20
#   END    end of the last run (cold start + RNDAY of the hot start), UTC
# One fort.20 covering START -> END serves both the cold start and the hot start.
#
# Optional environment overrides:
#   OUT_DIR    where fort.20 and fort_discharge.csv go (default: tools/adcirc_inputs/output/fort20_<start date>)
#   CACHE_DIR  raw USGS downloads, reused across runs (default: tools/adcirc_inputs/usgs_cache)
#   DT         record spacing FTIMINC in seconds (default 900 = USGS 15-min data)
#   WIDTH      length (default) or tributary (reproduces the old 2018 fort.20 method)
#   OFFLINE=1  use only files already in CACHE_DIR (for nodes without internet)
#   CONDA_ROOT miniconda install holding the floodwater env
#
# Needs internet access to USGS (waterservices.usgs.gov) unless OFFLINE=1.

set -euo pipefail

FORT14=${1:-fort.14}
START=${2:-"2022-12-01 00:00"}
END=${3:-"2022-12-30 00:00"}

# sbatch runs a copy of this script, so locate the repository from the submit directory.
REPO=${REPO:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}
if [[ ! -f "$REPO/tools/adcirc_inputs/make_fort20.py" ]]; then
    echo "ERROR: $REPO is not the richamp-support-floodwater root; submit from there or set REPO" >&2
    exit 1
fi
if [[ ! -f "$FORT14" ]]; then
    echo "ERROR: fort.14 not found: $FORT14" >&2
    exit 1
fi
FORT14=$(cd "$(dirname "$FORT14")" && pwd)/$(basename "$FORT14")
DT=${DT:-900}
WIDTH=${WIDTH:-length}
OUT_DIR=${OUT_DIR:-$REPO/tools/adcirc_inputs/output/fort20_${START%% *}}
CACHE_DIR=${CACHE_DIR:-$REPO/tools/adcirc_inputs/usgs_cache}
CONDA_ROOT=${CONDA_ROOT:-/work/pi_reza_hashemi_uri_edu/group_tools/miniconda3}
OFFLINE_FLAG=()
[[ "${OFFLINE:-0}" == "1" ]] && OFFLINE_FLAG=(--offline)

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

if [[ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    set +u; conda activate floodwater; set -u   # conda's activate scripts use unset variables
else
    echo "WARNING: $CONDA_ROOT not found; using the current python environment" >&2
fi

log "fort.20 for $START -> $END UTC, every $DT s, width=$WIDTH"
log "fort14=$FORT14"
log "out=$OUT_DIR  cache=$CACHE_DIR  python=$(command -v python)"
mkdir -p "$OUT_DIR" "$CACHE_DIR"

python "$REPO/tools/adcirc_inputs/make_fort20.py" \
    --fort14 "$FORT14" \
    --start "$START" \
    --end "$END" \
    --dt "$DT" \
    --width "$WIDTH" \
    --cache "$CACHE_DIR" \
    --out "$OUT_DIR/fort.20" \
    "${OFFLINE_FLAG[@]}"

# Check the file: FTIMINC on line 1, then a whole number of records.
python - "$OUT_DIR/fort.20" "$START" "$END" "$DT" <<'PY'
import math, sys
import pandas as pd
path, start, end, dt = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
with open(path) as f:
    ftiminc = float(f.readline())
    nvals = sum(1 for _ in f)
nrec = math.ceil((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / dt) + 2  # +1 end, +1 pad
if ftiminc != dt:
    sys.exit(f"ERROR: FTIMINC {ftiminc} != {dt}")
if nvals % nrec:
    sys.exit(f"ERROR: {nvals} values do not split into {nrec} records")
print(f"{path} OK: FTIMINC {ftiminc:g} s, {nrec} records x {nvals // nrec} flux nodes")
PY

log "Done. In fort.15 use base_date = $START:00, NFFR = 0. Outputs in $OUT_DIR:"
ls -lh "$OUT_DIR"
