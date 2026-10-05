# wind_data (vendored)

Unchanged copy of the parts of `wind_comparison_bundle.zip` (the GFS/GDAS wind-comparison code from
the `sea_grant` repository) that `../richamp_wind_vs_obs.py` uses:

| Path | Used for |
|---|---|
| `src/wind_data/` | observation download, QC, 10-m height adjustment, 1-h averaging, plot style |
| `scripts/hurricane_eval.py` | runs the observation stages (track → stations → observations → adjust) |
| `config/melissa.toml` | observation settings: QC limits, roughness classes, averaging window |

Keep these files identical to the bundle so the figures match the `gfs-run-vs-obs` skill. Fix bugs
in `sea_grant` first and copy the files over again.

The package needs nothing outside the `floodwater` env for this use: GRIB reading (cfgrib) is only
used by its model downloader, which is not called here, and cartopy is only needed for the station
map. Runtime data never goes here: `02_friction_compare.sh` sets `WIND_DATA_DIR` to the work folder
on scratch.
