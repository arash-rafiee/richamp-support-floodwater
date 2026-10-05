#!/bin/bash
#SBATCH --job-name=wind_dl
#SBATCH --output=wind_dl_%j.out
#SBATCH --error=wind_dl_%j.err
#SBATCH -p uri-cpu
#SBATCH -c 1
#SBATCH --mem=8G
#SBATCH -t 08:00:00
##SBATCH --mail-user=arash_rafiee@uri.edu
##SBATCH --mail-type=END,FAIL
#
# Step 1 of the land-friction wind validation: download GDAS and/or GFS 10-m wind and
# pressure from MetGet as OWI ASCII (.wnd/.pre). Step 2 is 02_friction_compare.sh.
#
#   GDAS = metget build ... --multiple-forecasts
#   GFS  = metget build ... --analysis
#
# Submit from the repository root:
#   sbatch tools/wind_validation/01_download_wind.sh
# Any setting below can be overridden from the environment, e.g.
#   PRODUCTS=gdas START="2026-10-01 00:00" END="2026-10-03 00:00" sbatch tools/wind_validation/01_download_wind.sh
# Use the same START/END/WORK_ROOT for 02_friction_compare.sh.

set -euo pipefail

# ------------------------------------------------------------------ settings
PRODUCTS=${PRODUCTS:-both}                       # gdas | gfs | both
START=${START:-"2026-09-24 12:00"}               # UTC
END=${END:-"2026-09-29 12:00"}                   # UTC, inclusive
DOMAIN=${DOMAIN:-"0.1 -100.0 5.0 -60.0 47.0"}    # resolution x0 y0 x1 y1 (must cover 40-43N, 73-70W)
WORK_ROOT=${WORK_ROOT:-/scratch4/workspace/${USER:-$(id -un)}-richamp/wind_validation}
OVERWRITE=${OVERWRITE:-false}                    # true: download again even if the files exist
CONDA_ROOT=${CONDA_ROOT:-/work/pi_reza_hashemi_uri_edu/group_tools/miniconda3}
# -----------------------------------------------------------------------------

TAG=$(date -u -d "$START" +%Y%m%d%H)-$(date -u -d "$END" +%Y%m%d%H)
WORK_DIR=$WORK_ROOT/$TAG
DL_DIR=$WORK_DIR/download

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

case $PRODUCTS in
    both) LIST="gdas gfs" ;;
    gdas|gfs) LIST=$PRODUCTS ;;
    *) die "PRODUCTS must be gdas, gfs or both (got '$PRODUCTS')" ;;
esac

# ------------------------------------------------------------------ pre-flight
if [[ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    set +u; conda activate floodwater; set -u   # conda's activate scripts use unset variables
else
    echo "WARNING: $CONDA_ROOT not found; using the current environment" >&2
fi
command -v metget >/dev/null || die "metget not found on PATH (activate the floodwater env)"
[[ -n "${METGET_API_KEY:-}" ]] || die "METGET_API_KEY is not set (see README.md, step 1)"
[[ -n "${METGET_ENDPOINT:-}" ]] || die "METGET_ENDPOINT is not set (see README.md, step 1)"

log "wind download $TAG: $START -> $END, products: $LIST"
log "domain: $DOMAIN   work dir: $WORK_DIR"
mkdir -p "$DL_DIR"

# ------------------------------------------------------------------ download
download() {  # product, metget flag
    local p=$1 flag=$2 d=$DL_DIR/$1 name=${1}_${TAG}
    mkdir -p "$d"
    if [[ -f "$d/.complete" && $OVERWRITE != true ]]; then
        log "$p: already downloaded ($d); set OVERWRITE=true to fetch again"
        return
    fi
    rm -f "$d"/*.wnd "$d"/*.pre "$d"/*.gz "$d/.complete"
    log "$p: metget build ... $flag"
    # metget writes into the current folder; one folder per product keeps filelist.json apart
    (cd "$d" && metget build --domain gfs $DOMAIN \
        --start "$START" \
        --end "$END" \
        --variable wind_pressure \
        --format owi-ascii \
        --timestep 3600 \
        --strict \
        --compression \
        --output "$name" \
        $flag)
    # --compression returns gzipped files; scale_and_subset.py reads plain text
    shopt -s nullglob
    for gz in "$d"/*.gz; do gunzip -f "$gz"; done
    shopt -u nullglob
    local nw np
    nw=$(find "$d" -maxdepth 1 -name '*.wnd' | wc -l)
    np=$(find "$d" -maxdepth 1 -name '*.pre' | wc -l)
    [[ $nw -eq 1 && $np -eq 1 ]] || { ls -la "$d"; die "$p: expected one .wnd and one .pre in $d (found $nw and $np)"; }
    touch "$d/.complete"
    log "$p: $(ls "$d"/*.wnd)"
}

for p in $LIST; do
    case $p in
        gdas) download gdas --multiple-forecasts ;;
        gfs)  download gfs --analysis ;;
    esac
done

# ------------------------------------------------------------------ checks
log "checking the downloaded files"
python - "$START" "$END" "$DL_DIR" $LIST <<'EOF'
import datetime as dt, glob, json, sys
start, end = (dt.datetime.strptime(a, "%Y-%m-%d %H:%M") for a in sys.argv[1:3])
dl, products = sys.argv[3], sys.argv[4:]

def scan(path):
    times, grids = [], set()
    with open(path) as f:
        f.readline()
        for line in f:
            if line.startswith("iLat="):
                grids.add(line[:65])
                times.append(dt.datetime.strptime(line[68:80], "%Y%m%d%H%M"))
    return times, grids

ok, grid_of = True, {}
for p in products:
    for kind in ("wnd", "pre"):
        path = glob.glob(f"{dl}/{p}/*.{kind}")[0]
        times, grids = scan(path)
        steps = {(b - a).total_seconds() for a, b in zip(times, times[1:])}
        problems = []
        if len(grids) != 1:
            problems.append(f"{len(grids)} different grids")
        if steps != {3600.0}:
            problems.append(f"time steps {sorted(steps)} s (expected 3600)")
        if not times or times[0] != start or times[-1] != end:
            problems.append(f"covers {times[0] if times else '-'} -> {times[-1] if times else '-'}")
        print(f"{p} .{kind}: {len(times)} hourly fields {times[0]:%Y-%m-%d %H:%M} -> {times[-1]:%Y-%m-%d %H:%M}"
              + ("  OK" if not problems else "  PROBLEM: " + "; ".join(problems)))
        ok &= not problems
        grid_of[(p, kind)] = next(iter(grids), None)
    files = glob.glob(f"{dl}/{p}/filelist.json")
    if files:
        try:
            info = json.load(open(files[0]))
            print(f"{p} filelist.json (model files MetGet used):")
            print("  " + json.dumps(info, indent=1)[:3000].replace("\n", "\n  "))
        except ValueError:
            print(f"{p} filelist.json: not JSON, left as is")
if len(set(grid_of.values())) > 1:
    print("PROBLEM: the products are not on the same grid:", set(grid_of.values()))
    ok = False
sys.exit(0 if ok else 1)
EOF

log "done. Next: sbatch tools/wind_validation/02_friction_compare.sh (same START/END/WORK_ROOT)"
