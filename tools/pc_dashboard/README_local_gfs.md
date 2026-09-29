# Local RICHAMP products from GFS (no cluster, no MetGet)

This is the workflow for producing `RICHAMP_wind.nc` and `RICHAMP_fort63.nc` on a
workstation, straight from NOAA NOMADS, when the ASGS/MetGet pipeline on the cluster is
not available. It reuses the repo's own writer classes so the files have exactly the
structure the dashboard expects. The operational pipeline is described in `README.md`.

## Requirements

Python 3.12+ with: `requests numpy netCDF4 xarray cfgrib eccodes scipy pyproj pandas matplotlib pillow`
(all present in the Anaconda environment). `ffmpeg` on PATH for MP4 output. `cartopy` only
for the optional wide-area comparison maps.

## Quick start

```bash
# 1. Download GFS 10 m wind (latest complete run, 4 days, hourly) for the roughness box
python get_gfs_wind.py --region 43 40 -73 -70 --step 1         # -> gfs_wind/<YYYYMMDD>_<HH>z/*.grb2

# 2. Merge the GRIB files into one generic NetCDF (the format scale_and_subset.py reads)
python gfs_grib_to_wind_nc.py gfs_wind/<YYYYMMDD>_<HH>z -o gfs_wind.nc

# 3a. Raw GFS in RICHAMP_wind.nc layout (no roughness scaling)
python gfs_to_richamp_wind.py gfs_wind.nc -o RICHAMP_wind --grid NLCD_z0_RICHAMP_Reg_Grid.nc

# 3b. Or the roughness-scaled product (needs a z0 interpolant pickle, see below)
python scale_and_subset.py -o RICHAMP_wind -sl up-down -hr NLCD_z0_RICHAMP_Reg_Grid.nc \
    -w gfs_wind.nc -wfmt generic-netcdf -wr gfs-roughness.nc -z0name z0_interp -r 3000 -sigma 1000 -t 3 -wasync

# 4. Water level placeholder over the same time span (zeta = 0 on the real mesh)
python make_placeholder_fort63.py --template <a real RICHAMP_fort63.nc> --wind RICHAMP_wind.nc -o RICHAMP_fort63.nc

# 5. Optional: animation and snapshots
python wind_video.py RICHAMP_wind.nc -o RICHAMP_wind_RI.mp4
```

Steps 1 to 3a take about 5 minutes. Use `--step 6` in step 1 for a 6-hourly file.
The region `43 40 -73 -70` matches `gfs-roughness.nc` exactly; keep it.

## Scripts

| Script | What it does |
|---|---|
| `get_gfs_wind.py` | Downloads GFS 0.25° U/V at 10 m from NOMADS (grib filter, subregion, retries). Finds the latest run whose last forecast hour is published. |
| `gfs_grib_to_wind_nc.py` | Merges the GRIB files into `gfs_wind.nc`: dims `time, lat, lon`; vars `time` (minutes since first step), `lon`, `lat`, `wind_u`, `wind_v`. Converts longitude to -180..180 and sorts both axes ascending, which `scale_and_subset.py` requires. |
| `gfs_to_richamp_wind.py` | Writes `gfs_wind.nc` in the `RICHAMP_wind.nc` layout using `NetcdfOutput` from `scale_and_subset.py`. Default keeps the GFS grid; `--grid <file>` bilinearly resamples onto that file's lon/lat (use the NLCD grid to match the operational product). |
| `build_point_z0_interp.py` | Builds a non-directional roughness interpolant pickle in seconds (each point's own NLCD z0 for all 12 directions). Lets `scale_and_subset.py` run without the hours-long directional build. |
| `subset_fort63_richamp.py` | Python port of `subset_fort63_richamp.m`: cuts the RICHAMP box out of a full-domain `fort.63.nc`. Verified to give the same nodes and elements as the MATLAB output. |
| `make_placeholder_fort63.py` | Copies mesh and attributes from an existing `RICHAMP_fort63.nc` and writes a constant water level (default 0) on a new time axis, e.g. the span of a `RICHAMP_wind.nc`. |
| `wind_video.py` | Animates any `RICHAMP_wind.nc` over the Rhode Island map: speed shading, direction arrows, GFS grid as mesh (or an ADCIRC mesh with `--fort14`). |

## Output formats

`RICHAMP_wind.nc`: group `Main` with dims `time` (unlimited), `longitude`, `latitude`; vars
`time` (f4, minutes since 1990-01-01), `time_unix` (i8), `lon`, `lat` (f8), `spd` (m/s) and
`dir` (degrees, meteorological, direction the wind comes from), both `(time, latitude, longitude)`.
The RICHAMP grid is 3337 x 2196 points, 71.9 to 71.108 W and 41.142 to 42.042 N.

`RICHAMP_fort63.nc`: dims `time` (unlimited), `node`, `nele`, `nvertex`; vars `time`
(seconds since `base_date`), `x`, `y`, `element` (1-based, `(nele, 3)`), `depth`, `zeta`
`(time, node)` with fill -99999, `time_unix`. The RICHAMP subset of mesh `ricv1_noriv_nopump`
has 608,560 nodes and 1,204,614 elements.

## What the operational product does that the raw file does not

`scale_and_subset.py` with `-sl up-down` lifts the coarse 10 m wind to 80 m using the coarse
roughness (`gfs-roughness.nc`), interpolates onto the 30 m NLCD grid, then brings it back to
10 m with a *directional* roughness: for each point and each of 12 wind directions, a
distance-weighted average of NLCD z0 over an upwind cone (30°, 3 km). Those cone averages
live in `z0_interp.pickle`, built once with `-z0sv`.

- The cluster has `z0_interp.pickle`; it is not in git (`*.pickle` ignored). Copy it into the
  repo root if you can, then use `-z0name z0_interp`.
- Building it locally takes roughly 9 to 10 hours: the loop in
  `generate_directional_z0_interpolant` is pure Python on one core (7.3 M points x 12 cones,
  Python `sum` on masked arrays). Parallelising the rows or vectorising the sums would cut it
  to minutes; not done yet.
- `build_point_z0_interp.py` gives `z0_interp_point.pickle` in seconds as a stopgap
  (no upwind averaging, so winds are noisier at roughness transitions).

The operational MetGet wind also stitches analysis hours from earlier cycles with the latest
forecast (`--multiple-forecasts`) and includes pressure; the NOMADS download is one forecast
cycle, wind only.

## Validation done (run 2026-09-23 12Z)

- `gfs_grib_to_wind_nc.py` output read back with the repo's `GenericNetcdf` class: grid, times and values correct.
- `RICHAMP_wind.nc` structure compared variable by variable with a `scale_and_subset.py` output: identical.
- Wind field compared with Tropical Tidbits GFS maps at 0/24/48/72/96 h by decoding their
  colours at 697 points: mean difference -0.6 kt, std 1.8 kt; 95 % of points above 20 kt
  within 4 kt. CyclonicWx shows ~10 kt more in the core band and is the outlier.
- `subset_fort63_richamp.py` output has identical x, y, element and depth to the operational
  `RICHAMP_fort63.nc` from August 2021.
- Comparison images: `comparison_gfs_2026092312/` (with its own README). Snapshots: `snapshots/`.

## Caveats

- The repo's map PNGs (e.g. `RhodeIslandChamp.png`) are stored south-up; `Grapher.py`
  compensates with a swapped extent, and `wind_video.py` flips them by default.
- `RICHAMP_wind.nc`, `RICHAMP_fort63*.nc`, pickles, `gfs_wind/`, `comparison_gfs_*/`,
  `snapshots/`, `*.mp4` and `*.gif` are git-ignored. Note `.gitignore` has `RICHAMP*`, which
  on Windows also matches lowercase names like `richamp_*.py`; that is why the video script is
  `wind_video.py`.
- A placeholder `RICHAMP_fort63.nc` has zeta = 0 everywhere and says so in its global
  `description` and `comments` attributes. Do not present it as a model result.
