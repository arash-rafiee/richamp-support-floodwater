#!/bin/bash
#SBATCH --job-name=wind_fric
#SBATCH --output=wind_fric_%j.out
#SBATCH --error=wind_fric_%j.err
#SBATCH -p uri-cpu
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH -t 24:00:00
##SBATCH --mail-user=arash_rafiee@uri.edu
##SBATCH --mail-type=END,FAIL
#
# Step 2 of the land-friction wind validation (step 1: 01_download_wind.sh):
#   A  land friction: scale_and_subset.py (unchanged, operational settings) on each downloaded .wnd
#   B  observations: fetch, QC, adjust to 10 m (wind_data pipeline in tools/wind_validation/bundle)
#   C  model wind at every station, with and without land friction
#   D  statistics and figures (format of the bundle's gfs-run-vs-obs skill)
#
# Submit from the repository root:
#   sbatch tools/wind_validation/02_friction_compare.sh
# To redraw only (e.g. after changing SHOW_RAW), resubmit: finished steps are reused.
#   SHOW_RAW=false sbatch tools/wind_validation/02_friction_compare.sh

set -euo pipefail

# ------------------------------------------------------------------ settings
PRODUCTS=${PRODUCTS:-both}                       # gdas | gfs | both
SHOW_RAW=${SHOW_RAW:-true}                       # true: also show raw (no land friction) GDAS/GFS in plots and tables
START=${START:-"2026-09-24 12:00"}               # UTC, same as 01_download_wind.sh
END=${END:-"2026-09-29 12:00"}                   # UTC, same as 01_download_wind.sh
WORK_ROOT=${WORK_ROOT:-/scratch4/workspace/${USER:-$(id -un)}-richamp/wind_validation}
REPO=${REPO:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}
BUNDLE_DIR=${BUNDLE_DIR:-$REPO/tools/wind_validation/bundle}   # wind_data code (vendored from wind_comparison_bundle.zip)
STATIONS_FILE=${STATIONS_FILE:-$REPO/tools/wind_validation/stations_ri_ma_south_coast.json}
Z0_PICKLE=${Z0_PICKLE:-$REPO/z0_interp}          # directional z0 interpolant, without .pickle (as -z0name)
THREADS=${THREADS:-3}                            # scale_and_subset threads; -c above = THREADS + 1 (-wasync)
ZOOM=${ZOOM:-true}                               # true: also the two-panel zoom figure per station
OVERWRITE=${OVERWRITE:-false}                    # true: redo land friction, observations and extraction
CONDA_ROOT=${CONDA_ROOT:-/work/pi_reza_hashemi_uri_edu/group_tools/miniconda3}
# -----------------------------------------------------------------------------

TAG=$(date -u -d "$START" +%Y%m%d%H)-$(date -u -d "$END" +%Y%m%d%H)
WORK_DIR=$WORK_ROOT/$TAG
DL_DIR=$WORK_DIR/download
FRIC_DIR=$WORK_DIR/friction
OBS_DIR=$WORK_DIR/obs
ST_DIR=$WORK_DIR/stations
OBS_RUN_DIR=$OBS_DIR/processed/hurricane/periods/$TAG
SUFFIX=$([[ $SHOW_RAW == true ]] && echo with_raw || echo friction_only)
RESULTS=$WORK_DIR/results/$SUFFIX
TOOLS=$REPO/tools/wind_validation
export WIND_DATA_DIR=$OBS_DIR   # bundle data (observations, basemap cache) go to scratch, never into the repo

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

case $PRODUCTS in
    both) LIST="gdas gfs" ;;
    gdas|gfs) LIST=$PRODUCTS ;;
    *) die "PRODUCTS must be gdas, gfs or both (got '$PRODUCTS')" ;;
esac
[[ $SHOW_RAW == true || $SHOW_RAW == false ]] || die "SHOW_RAW must be true or false"

# ------------------------------------------------------------------ pre-flight
if [[ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    set +u; conda activate floodwater; set -u
else
    echo "WARNING: $CONDA_ROOT not found; using the current environment" >&2
fi
log "land-friction wind validation $TAG, products: $LIST, SHOW_RAW=$SHOW_RAW"
log "repo=$REPO  work=$WORK_DIR  python=$(command -v python)"

[[ -f $REPO/scale_and_subset.py ]] || die "$REPO is not the richamp-support-floodwater root; submit from there or set REPO"
[[ -f $REPO/NLCD_z0_RICHAMP_Reg_Grid.nc ]] || die "missing $REPO/NLCD_z0_RICHAMP_Reg_Grid.nc"
[[ -f $REPO/gfs-roughness.nc ]] || die "missing $REPO/gfs-roughness.nc"
[[ -f $Z0_PICKLE.pickle ]] || die "missing $Z0_PICKLE.pickle (directional z0 interpolant; set Z0_PICKLE)"
[[ -f $STATIONS_FILE ]] || die "missing $STATIONS_FILE"
for f in src/wind_data scripts/hurricane_eval.py config/melissa.toml; do
    [[ -e $BUNDLE_DIR/$f ]] || die "$BUNDLE_DIR/$f not found (tools/wind_validation/bundle is part of the repository; git pull?)"
done
python - <<'EOF' || die "the python environment lacks packages listed above"
import importlib, sys
missing = [m for m in ("netCDF4", "numpy", "scipy", "pandas", "pyproj", "matplotlib", "xarray", "requests", "tomllib")
           if importlib.util.find_spec(m) is None]
if missing:
    print("missing python packages:", ", ".join(missing))
    sys.exit(1)
if importlib.util.find_spec("cartopy") is None:
    print("note: cartopy not installed, the station map will be skipped")
EOF
declare -A WND
for p in $LIST; do
    [[ -f $DL_DIR/$p/.complete ]] || die "$p not downloaded yet: run 01_download_wind.sh with the same START/END/WORK_ROOT"
    WND[$p]=$(ls "$DL_DIR"/$p/*.wnd)
done
mkdir -p "$FRIC_DIR" "$OBS_DIR" "$ST_DIR" "$RESULTS"

# ------------------------------------------------------------------ A: land friction
for p in $LIST; do
    out=$FRIC_DIR/RICHAMP_wind_$p
    if [[ -f $out.nc && $OVERWRITE != true ]]; then
        log "A $p: land friction already done ($out.nc)"
        continue
    fi
    log "A $p: scale_and_subset.py on ${WND[$p]}"
    rm -f "$out.partial.nc"
    (cd "$FRIC_DIR" && python "$REPO/scale_and_subset.py" -o "$out.partial" -sl up-down \
        -hr "$REPO/NLCD_z0_RICHAMP_Reg_Grid.nc" -w "${WND[$p]}" -wfmt owi-ascii \
        -wr "$REPO/gfs-roughness.nc" -z0name "$Z0_PICKLE" -r 3000 -sigma 1000 -t "$THREADS" -wasync)
    mv "$out.partial.nc" "$out.nc"
    log "A $p: wrote $out.nc"
done

# ------------------------------------------------------------------ B: observations
if [[ -f $OBS_RUN_DIR/observations_final.csv.gz && $OVERWRITE != true ]]; then
    log "B observations already processed ($OBS_RUN_DIR)"
else
    log "B observations: fetch, QC, 10-m adjustment"
    python "$TOOLS/richamp_wind_vs_obs.py" observations --bundle "$BUNDLE_DIR" --stations-file "$STATIONS_FILE" \
        --start "$START" --end "$END" --obs-dir "$OBS_DIR" --run-name "$TAG"
fi

# ------------------------------------------------------------------ C: model wind at the stations
SW_ARGS=()
for p in $LIST; do
    csv=$ST_DIR/station_wind_$p.csv
    if [[ -f $csv && $csv -nt $FRIC_DIR/RICHAMP_wind_$p.nc && $OVERWRITE != true ]]; then
        log "C $p: station wind already extracted ($csv)"
    else
        log "C $p: model wind at the stations"
        python "$TOOLS/extract_station_wind.py" --stations-csv "$OBS_RUN_DIR/stations.csv" --product "$p" \
            --wnd "${WND[$p]}" --richamp "$FRIC_DIR/RICHAMP_wind_$p.nc" \
            --hr-roughness "$REPO/NLCD_z0_RICHAMP_Reg_Grid.nc" -o "$csv"
    fi
    SW_ARGS+=("$p=$csv")
done

# ------------------------------------------------------------------ D: statistics and figures
log "D statistics and figures -> $RESULTS"
EXTRA=()
[[ $SHOW_RAW == true ]] && EXTRA+=(--show-raw)
[[ $ZOOM == true ]] || EXTRA+=(--no-zoom)
python "$TOOLS/richamp_wind_vs_obs.py" compare --bundle "$BUNDLE_DIR" --stations-file "$STATIONS_FILE" \
    --start "$START" --end "$END" --obs-run-dir "$OBS_RUN_DIR" --station-wind "${SW_ARGS[@]}" --out "$RESULTS" \
    --source-note "gdas=MetGet --multiple-forecasts" "gfs=MetGet --analysis" ${EXTRA[@]+"${EXTRA[@]}"}

cat > "$RESULTS/run_info.txt" <<EOF
land-friction wind validation  $(date -u +'%Y-%m-%d %H:%M UTC')
period          $START -> $END UTC
products        $LIST   (GDAS = metget --multiple-forecasts, GFS = metget --analysis)
input .wnd      $(for p in $LIST; do echo -n "${WND[$p]} "; done)
land friction   scale_and_subset.py -sl up-down -hr NLCD_z0_RICHAMP_Reg_Grid.nc -wfmt owi-ascii -wr gfs-roughness.nc
                -z0name $Z0_PICKLE -r 3000 -sigma 1000
show raw        $SHOW_RAW
stations        $STATIONS_FILE
observations    $OBS_RUN_DIR
bundle          $BUNDLE_DIR
EOF
log "done. Figures and tables: $RESULTS"
