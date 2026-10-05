# Land-friction wind validation (GDAS / GFS vs observations)

Puts MetGet GDAS and GFS 10-m winds through the RICHAMP land-friction step
(`scale_and_subset.py`, unchanged), interpolates them to observation stations and
compares them with observed wind (adjusted to 10 m, 1-h means). The figures use the format of the
`gfs-run-vs-obs` skill from `wind_comparison_bundle.zip`. Everything runs on Unity in the
`floodwater` env. Nothing needs installing, and everything is in this repository: the observation, QC
and plotting code from `wind_comparison_bundle.zip` is in [`bundle/`](bundle/README.md), unchanged.

| Label | Source |
|---|---|
| GDAS | `metget build --domain gfs ... --multiple-forecasts` |
| GFS | `metget build --domain gfs ... --analysis` |

## Before the first run

1. MetGet access, as for the operational runs (see the main `README.md`):
   `export METGET_API_KEY=...` and `export METGET_ENDPOINT=https://api.metget.zachcobell.com`.
2. The directional roughness interpolant `z0_interp.pickle` must be in the repository root, the same
   file the operational post-processing uses (`-z0name $postprocessdir/z0_interp`), or set `Z0_PICKLE`.

## Run

From the repository root:

```bash
sbatch tools/wind_validation/01_download_wind.sh        # MetGet download -> .wnd/.pre
sbatch tools/wind_validation/02_friction_compare.sh     # friction, stations, statistics, figures
```

Settings are at the top of each script and can be overridden from the environment:

```bash
PRODUCTS=gdas START="2026-10-01 00:00" END="2026-10-03 00:00" sbatch tools/wind_validation/01_download_wind.sh
PRODUCTS=gdas START="2026-10-01 00:00" END="2026-10-03 00:00" SHOW_RAW=false sbatch tools/wind_validation/02_friction_compare.sh
```

| Setting | Script | Meaning |
|---|---|---|
| `PRODUCTS` | both | `gdas`, `gfs` or `both` |
| `START`, `END` | both | UTC period; must be the same in both scripts |
| `DOMAIN` | 1 | MetGet grid `res x0 y0 x1 y1`; must cover 40–43°N, 73–70°W (`gfs-roughness.nc`) |
| `SHOW_RAW` | 2 | `true`: also draw the raw (no land friction) winds as thin dashed lines and add "GDAS raw"/"GFS raw" rows to the tables and bars |
| `ZOOM` | 2 | `true`: also the two-panel zoom figure per station |
| `STATIONS_FILE` | 2 | station preset (default `stations_ri_ma_south_coast.json`) |
| `Z0_PICKLE`, `WORK_ROOT`, `THREADS`, `OVERWRITE` | 2 | paths and run control (`BUNDLE_DIR` defaults to `bundle/`) |

Finished steps are reused: resubmitting script 2 with only `SHOW_RAW` changed redraws the figures in
a few minutes. `OVERWRITE=true` redoes everything.

## What script 2 does

| Step | Tool | Output (under `$WORK_ROOT/<start>-<end>/`) |
|---|---|---|
| A land friction | `scale_and_subset.py -sl up-down -wfmt owi-ascii -wr gfs-roughness.nc -z0name z0_interp -r 3000 -sigma 1000` | `friction/RICHAMP_wind_{gdas,gfs}.nc` |
| B observations | `bundle/` pipeline (`hurricane_eval.py` stages track → stations → observations → adjust): NDBC/C-MAN, CO-OPS, GHCNh; QC; neutral log-law adjustment to 10 m | `obs/processed/hurricane/periods/<start>-<end>/` |
| C model at stations | `extract_station_wind.py`: bilinear u/v from the RICHAMP file (land friction) and from the `.wnd` (raw); local NLCD z0 | `stations/station_wind_{gdas,gfs}.csv` |
| D statistics, figures | `richamp_wind_vs_obs.py compare` | `results/{with_raw,friction_only}/` |

Results folder:

| File | Content |
|---|---|
| `stations/timeseries_<src>_<id>.png` (+ `_zoom`, `.caption.txt`) | per-station time series with the "Full-period statistics" table |
| `stations_error_by_day/error_by_day_<src>_<id>.png` | RMSE and bias by day per station |
| `error_by_day_regions.png`, `error_by_day_types.png` | RMSE and bias by day, pooled by region and by station type |
| `overview_timeseries.png`, `station_map.png` | all stations; map with the RICHAMP grid outlined (map needs cartopy) |
| `hourly_matched.csv`, `stats_by_station.csv`, `stats_by_station_day.csv`, `stats_by_region_day.csv`, `stats_by_type.csv`, `stats_by_type_day.csv` | the numbers; columns `<product>_*` = land friction, `<product>_raw_*` = raw |
| `run_info.txt` | settings of the run |

Statistics: n, bias, MAE, RMSE, r (model − observed, m/s) and direction bias/MAE (pairs with both
speeds ≥ 1 m/s), against 1-h observation means centred on each hour.

## Things to keep in mind

- The RICHAMP grid covers only Rhode Island (41.14–42.04°N, 71.90–71.11°W). Stations outside it
  (BUZM3, 44020, 44008, Nantucket, Martha's Vineyard) have no land-friction wind: they are always
  shown with the raw winds and labelled "outside RICHAMP grid".
- GHCNh airports publish no anemometer height, so their observations are not adjusted to 10 m and are
  labelled "sensor height".
- Over water the land-friction step uses the NLCD water roughness (0.003 m). Where the coarse model
  cell is land it raises the wind over water, and over rough land (z0 ≈ 0.9 m) it lowers it by up
  to about a third.
