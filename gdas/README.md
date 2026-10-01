# GDAS meteorological forcing for RICHAMP/ADCIRC

`download_gdas.py` builds hourly OWI ASCII `.wnd`/`.pre` files from NOAA/NCEP **GDAS**.
They feed the same downstream chain as the MetGet/GFS files:

```
get_metget_data.py / metget build ── GFS ──┐
                                           ├─> .wnd/.pre ─> OceanweatherTo306.py ─> fort.22 ─> owi2wind.py ─> ADCIRC
gdas/download_gdas.py ─────────── GDAS ────┘
```

`OceanweatherTo306.py`, `owi2wind.py` and the GFS path are unchanged.

## Usage

Linux / HPC (Unity), from the repository root, in the existing `floodwater` env. Nothing needs installing:

```bash
conda activate floodwater
python gdas/tests/check_grib_reader.py      # once: confirms this env decodes GDAS GRIB2 correctly

python gdas/download_gdas.py \
    --domain 0.1 -98.0 3.0 4.0 47.0 \
    --start "2025-10-28 00:00" \
    --end   "2025-11-04 00:00" \
    --timestep 3600 \
    --output Oct25 \
    --cache-dir /scratch/$USER/gdas_cache      # optional

python OceanweatherTo306.py --wind Oct25_00_00.wnd --pressure Oct25_00_00.pre --output Oct25.fort.22
python owi2wind.py Oct25.fort.22 Oct25.fort.22Wind_Inp.txt -o Oct25
```

Windows PowerShell uses the same command on one line. The code uses `pathlib` and writes `\n`
line endings on every platform.

| Option | Meaning |
|---|---|
| `--domain res x0 y0 x1 y1` | Target grid, the MetGet `--domain` without the model name. Corners are sorted like `get_metget_data.py`. |
| `--start`, `--end` | UTC, `"YYYY-MM-DD HH:MM"`, on whole hours; end is inclusive. |
| `--timestep` | Only `3600`. `OceanweatherTo306.py` hard-codes 3600 s. |
| `--output` | Writes `<output>_00_00.wnd` and `<output>_00_00.pre`. |
| `--output-dir` | Default: current directory. |
| `--cache-dir` | Downloaded GRIB2 subsets, reused across runs. Default: `gdas/cache` (git-ignored). |
| `--work-dir` | `download_gdas.log` and `failed_urls.log`. Default: `gdas/work` (git-ignored). |
| `--source auto/nomads/aws` | `auto`: NOMADS for cycles younger than 9 days, otherwise the AWS archive. |
| `--buffer` | Extra source margin around the domain in degrees (default 1.0). |
| `--reference-wnd/--reference-pre` | Compare the layout with existing MetGet files. Values are not compared. |
| `--grib-reader auto/eccodes/rasterio` | GRIB2 decoder. `auto`: eccodes if installed, otherwise rasterio (GDAL). |
| `--dry-run` | Print which GDAS cycle/forecast hour feeds every output hour. |

The exit status is 0 only when validation passes.

## Data source

| | NOMADS | NOAA Open Data on AWS (`noaa-gfs-bdp-pds`) |
|---|---|---|
| Coverage | last ~10 days | archive (0.25 deg hourly files from at least 2021) |
| Request | `filter_gdas_0p25.pl`: 3 fields, domain box only (~0.4 MB/hour) | `.idx` byte ranges: 3 global fields (~2.8 MB/hour) |
| File | `gdas.YYYYMMDD/HH/atmos/gdas.tHHz.pgrb2.0p25.fFFF` | same; pre-March-2021 runs have no `atmos/` level |

Fields, verified from the GDAS inventories and checked again in every decoded GRIB message:

* 10 m U wind: `UGRD:10 m above ground` (GRIB2 0/2/2), m/s
* 10 m V wind: `VGRD:10 m above ground` (GRIB2 0/2/3), m/s
* Mean sea level pressure: `PRMSL:mean sea level` (GRIB2 0/3/1), Pa, written as hPa

Surface pressure (`PRES:surface`) and `MSLET` are never used.

## Hourly construction

GDAS runs 4 cycles a day (00/06/12/18 UTC). Each cycle publishes the analysis (`f000`) and
hourly short forecasts `f001`-`f009`. Valid hour H uses cycle `C = floor(H/6)*6`, forecast hour `H - C`:

```
00Z cycle: f000 -> 00, f001 -> 01, ... f005 -> 05
06Z cycle: f000 -> 06, ... f005 -> 11      (same for 12Z and 18Z)
```

* **GDAS f000** is the data-assimilation analysis. GDAS waits about 6 h for observations, so it
  assimilates more data than GFS.
* **GDAS f001-f005** are 1-5 h forecasts from that analysis; f003-f009 are the next cycle's background.
* **GFS** uses the same model and assimilation system with an early data cutoff, plus long forecasts. MetGet's
  `gfs --analysis` builds from GFS cycles, not GDAS.

Why this scheme was chosen: tested on 2025-10-28 over the RICHAMP domain. The RMS hourly change at
cycle switches with f000-f005 (U 0.80, V 0.82 m/s, P 0.57 hPa) was close to the normal hour-to-hour
change (0.76 / 0.78 m/s, 0.51 hPa). It was smoother than using f001-f006 or f003-f008. The cycle-to-cycle analysis
correction was locally up to 31 m/s near Hurricane Melissa, so putting the analysis at every synoptic time matters.
There is **no time interpolation** and **no gap filling**: a missing hour stops the run and is listed
(and its URLs go to `failed_urls.log`).

## Spatial regridding

GDAS (0.25 deg) is bilinearly interpolated onto the target grid. For RICHAMP this is 0.1 deg,
NX=1021, NY=441, SW corner (-98, 3), the same as `fort.15` (`441 1021 47.0 -98.0 0.1 0.1 3600 600`).
U, V and MSLP are interpolated separately. Bilinear interpolation never overshoots its neighbours,
and the 0.25 deg fields carry no detail that a higher-order scheme could recover. A 1 deg margin
is downloaded, and the run stops if any target point is outside the source grid. There is no
extrapolation and NaNs are not allowed.

## Output format

Identical in layout to MetGet `owi-ascii` (checked against MetGet RICHAMP files):

```
Oceanweather WIN/PRE Format                            2025102800     2025110400
iLat= 441iLong=1021DX=0.1000DY=0.1000SWLat= 3.00000SWLon=-98.0000DT=202510280000
 1010.2474 1010.2266 ...          8 values per line, F10.4
```

Values start at the SW corner, longitude varies fastest, and rows go south to north. Each `.wnd` time slice
holds all U values, then all V values (m/s). `.pre` is in hPa, and `OceanweatherTo306.py` multiplies it by 100.

## Validation

After writing, both files are re-read the way the downstream tools read them:

* title line and every header, using the fixed columns (owi2wind/scale_and_subset) and the regexes (OceanweatherTo306)
* NX/NY/DX/DY/SW corner
* start/end time, 3600 s steps, no missing or duplicate times, `.wnd` and `.pre` times identical
* line widths and value counts
* orientation self-check at the corners
* no NaNs
* U/V within +-100 m/s, MSLP within 850-1100 hPa
* the grid size `owi2wind.py` will rebuild from `Wind_Inp.txt`. Its integer truncation loses a row or column
  for some small domains; RICHAMP's domain is fine.

## GRIB2 decoding

Two readers give the same result:
* **eccodes**, used when installed.
* **rasterio/GDAL**, used in the `floodwater` env. Here fields are identified by their GRIB2
  codes (discipline/category/number, surface type and level, forecast hour) rather than
  GDAL's version-dependent names.

Tested on 2025-10-28 00-06Z:
* the grids are identical on both the AWS global and the NOMADS subset layouts;
* the `.wnd` files are byte-identical;
* the `.pre` files differ by at most 0.0001 hPa, because GDAL returns float32.

`gdas/tests/check_grib_reader.py` downloads one hour and compares 7 grid points against
eccodes reference values. It needs neither pytest nor eccodes.

## Tests

```bash
python -m pytest gdas/tests -q      # needs pytest (e.g. on a workstation)
```

Offline tests cover the format against a MetGet sample, the timeline, regridding, download checks
(HTML error pages, truncated GRIB, `.idx` parsing), and a round trip through the **unmodified**
`OceanweatherTo306.py` and `owi2wind.py`, including orientation.

## Files

```
gdas/
├── download_gdas.py   command line: download -> regrid -> write -> validate
├── gdas_source.py     hourly timeline, NOMADS/AWS download with retries and cache, GRIB2 decoding
├── grid.py            target grid and bilinear interpolation
├── owi.py             OWI .wnd/.pre writer, re-reader and checks
├── requirements.txt
└── tests/
    ├── test_gdas.py           offline pytest suite
    └── check_grib_reader.py   one-hour GRIB decoding check for a new environment
```
