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
# Submit from the repository root:
#   sbatch tools/adcirc_inputs/make_fort20.sh /path/to/hotstart_run
# or run interactively the same way with bash instead of sbatch.
#
#   RUN_DIR  folder holding fort.14 and fort.15 (default: the current directory).
#            The dates come from its fort.15: start = base_date, end = base_date + RNDAY.
#            Use the HOT-START run folder (its RNDAY covers cold + hot start), so one
#            fort.20 serves both runs.
#
# Optional environment overrides:
#   FORT14, FORT15  use these files instead of RUN_DIR/fort.14, RUN_DIR/fort.15
#   START, END      dates in UTC instead of fort.15, e.g. START="2022-12-01 00:00"
#   OUT_DIR    where fort.20 and fort_discharge.csv go
#              (default: tools/adcirc_inputs/output/<run folder name>)
#   CACHE_DIR  raw USGS downloads, reused across runs (default: tools/adcirc_inputs/usgs_cache)
#   DT         record spacing FTIMINC in seconds (default 900 = USGS 15-min data)
#   WIDTH      length (default) or tributary (reproduces the old 2018 fort.20 method)
#   OFFLINE=1  use only files already in CACHE_DIR (for nodes without internet)
#   CONDA_ROOT miniconda install holding the floodwater env
#
# fort.20 is not written into RUN_DIR, so an existing fort.20 there is never
# overwritten; copy it in after checking fort_discharge.csv.
# Needs internet access to USGS (waterservices.usgs.gov) unless OFFLINE=1.

set -euo pipefail

RUN_DIR=$(cd "${1:-.}" && pwd)
FORT14=${FORT14:-$RUN_DIR/fort.14}
FORT15=${FORT15:-$RUN_DIR/fort.15}

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
DT=${DT:-900}
WIDTH=${WIDTH:-length}
OUT_DIR=${OUT_DIR:-$REPO/tools/adcirc_inputs/output/$(basename "$RUN_DIR")}
CACHE_DIR=${CACHE_DIR:-$REPO/tools/adcirc_inputs/usgs_cache}
CONDA_ROOT=${CONDA_ROOT:-/work/pi_reza_hashemi_uri_edu/group_tools/miniconda3}

EXTRA=()
[[ -f "$FORT15" ]] && EXTRA+=(--fort15 "$FORT15")
[[ -n "${START:-}" ]] && EXTRA+=(--start "$START")
[[ -n "${END:-}" ]] && EXTRA+=(--end "$END")
[[ "${OFFLINE:-0}" == "1" ]] && EXTRA+=(--offline)

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

if [[ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    set +u; conda activate floodwater; set -u   # conda's activate scripts use unset variables
else
    echo "WARNING: $CONDA_ROOT not found; using the current python environment" >&2
fi

log "fort.20 for run folder $RUN_DIR (every $DT s, width=$WIDTH)"
log "fort14=$FORT14  fort15=$([[ -f "$FORT15" ]] && echo "$FORT15" || echo none)"
log "out=$OUT_DIR  cache=$CACHE_DIR  python=$(command -v python)"
mkdir -p "$OUT_DIR" "$CACHE_DIR"

# make_fort20.py reads the dates from fort.15, writes fort.20 and checks it
python "$REPO/tools/adcirc_inputs/make_fort20.py" \
    --fort14 "$FORT14" \
    --dt "$DT" \
    --width "$WIDTH" \
    --cache "$CACHE_DIR" \
    --out "$OUT_DIR/fort.20" \
    "${EXTRA[@]}"

log "Done. Outputs in $OUT_DIR:"
ls -lh "$OUT_DIR"
