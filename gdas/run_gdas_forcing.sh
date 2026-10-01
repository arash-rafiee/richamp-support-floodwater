#!/bin/bash
#SBATCH --job-name=gdas_forcing
#SBATCH --output=gdas_forcing_%j.out
#SBATCH --error=gdas_forcing_%j.err
#SBATCH -p uri-cpu
#SBATCH -c 1
#SBATCH --mem=64G
#SBATCH -t 08:00:00
##SBATCH --mail-user=arash_rafiee@uri.edu
##SBATCH --mail-type=END,FAIL
#
# GDAS -> .wnd/.pre -> fort.22 -> NetCDF for RICHAMP/ADCIRC.
#
# Submit from the repository root (arguments are optional; defaults below):
#   sbatch gdas/run_gdas_forcing.sh "2026-09-20 00:00" "2026-10-01 00:00" Sep26
# or run interactively the same way with bash instead of sbatch.
#
# Optional environment overrides:
#   CACHE_DIR  downloaded GRIB2 subsets (default: gdas/cache, reused across runs)
#   OUT_DIR    outputs (default: gdas/output/<name>)
#   CONDA_ROOT miniconda install holding the floodwater env
#
# Needs internet access to NOAA (NOMADS / AWS) for the download step.

set -euo pipefail

START=${1:-"2026-09-20 00:00"}
END=${2:-"2026-10-01 00:00"}
NAME=${3:-Sep26}

# RICHAMP atmospheric grid (fort.15: 441 1021 47.0 -98.0 0.1 0.1 3600 600)
DOMAIN="0.1 -98.0 3.0 4.0 47.0"
NX=1021
NY=441

# sbatch runs a copy of this script, so locate the repository from the submit directory.
REPO=${REPO:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}
if [[ ! -f "$REPO/gdas/download_gdas.py" ]]; then
    echo "ERROR: $REPO is not the richamp-support-floodwater root; submit from there or set REPO" >&2
    exit 1
fi
CACHE_DIR=${CACHE_DIR:-$REPO/gdas/cache}
OUT_DIR=${OUT_DIR:-$REPO/gdas/output/$NAME}
CONDA_ROOT=${CONDA_ROOT:-/work/pi_reza_hashemi_uri_edu/group_tools/miniconda3}

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

if [[ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    set +u; conda activate floodwater; set -u   # conda's activate scripts use unset variables
else
    echo "WARNING: $CONDA_ROOT not found; using the current python environment" >&2
fi

log "GDAS forcing $NAME: $START -> $END"
log "repo=$REPO  out=$OUT_DIR  cache=$CACHE_DIR  python=$(command -v python)"
mkdir -p "$OUT_DIR"

log "Step 1/4: GRIB2 reader check"
python "$REPO/gdas/tests/check_grib_reader.py"

log "Step 2/4: download GDAS and write .wnd/.pre"
python "$REPO/gdas/download_gdas.py" \
    --domain $DOMAIN \
    --start "$START" \
    --end "$END" \
    --timestep 3600 \
    --output "$NAME" \
    --output-dir "$OUT_DIR" \
    --cache-dir "$CACHE_DIR" \
    --work-dir "$OUT_DIR/work"

cd "$OUT_DIR"

log "Step 3/4: OceanweatherTo306.py"
python "$REPO/OceanweatherTo306.py" \
    --wind "${NAME}_00_00.wnd" \
    --pressure "${NAME}_00_00.pre" \
    --output "${NAME}.fort.22"

# OceanweatherTo306.py prints errors but still exits 0, so check its output here.
NREC=$(sed -n '5p' "${NAME}.fort.22Wind_Inp.txt")
EXPECTED=$((NREC * NX * NY))
LINES=$(wc -l < "${NAME}.fort.22")
if [[ "$LINES" -ne "$EXPECTED" ]]; then
    echo "ERROR: ${NAME}.fort.22 has $LINES lines, expected $EXPECTED ($NREC records x $NX x $NY)" >&2
    exit 1
fi
log "fort.22 OK: $NREC records, $LINES lines"

log "Step 4/4: owi2wind.py"
python "$REPO/owi2wind.py" "${NAME}.fort.22" "${NAME}.fort.22Wind_Inp.txt" -o "$NAME" > owi2wind.log

python - "$NAME.nc" "$NREC" "$NY" "$NX" <<'PY'
import sys
import netCDF4
path, nrec, ny, nx = sys.argv[1], *map(int, sys.argv[2:])
with netCDF4.Dataset(path) as nc:
    shape = nc["wind_u"].shape
if shape != (nrec, ny, nx):
    sys.exit(f"ERROR: {path} has shape {shape}, expected {(nrec, ny, nx)}")
print(f"{path} OK: shape {shape}")
PY

log "Done. Outputs in $OUT_DIR:"
ls -lh "$OUT_DIR"
