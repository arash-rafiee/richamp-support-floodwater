"""Markdown report: identity, data, methods, results, gaps and limitations."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from wind_data.hurricane.evaluate import GFS_COMPOSITE, gdas_series
from wind_data.hurricane.plots import SOURCE_NAMES, storm_in_period

LIMITATIONS = """\
* **Model resolution.** GFS/GDAS at 0.25 deg (~28 km) cannot resolve an eye
  wall with a radius of maximum wind of 5-10 n mi (HURDAT2). Near the core,
  large negative speed biases are expected by construction and say more
  about resolution than about forecast skill.
* **Averaging and sampling.** Model winds are instantaneous grid-box values at
  hourly valid times; between the 00/06/12/18Z cycles the GDAS
  series is a 1-5 h GDAS forecast, not an analysis; observations are 2- to 8-minute means (NDBC) or
  report-dependent means (GHCNh, CO-OPS), matched to the nearest time within
  the tolerance. Peaks are compared at the matched model times; with
  6-hourly times they usually miss the true observed peak (see
  `obs_peak_record` in peaks.csv).
* **Neutral log law.** Height adjustment assumes neutral stability, a
  homogeneous surface and a constant roughness length. In hurricanes, sea-
  surface roughness depends on wind speed and sea state, drag appears to
  saturate above ~30-40 m/s, buoy anemometers at 3-5 m can be sheltered by
  wave crests and tilted by hull motion, and coastal sensors see flow
  distortion and mixed fetch. For a 4.1 m buoy anemometer the adjustment is
  about +9 %; its uncertainty is probably comparable to the adjustment
  itself at extreme winds. No hurricane-specific roughness is applied; the
  unadjusted sensitivity results show how much the adjustment matters.
* **Gusts** are reported but never height-adjusted or verified.
* **Metadata** (sensor heights, positions) are the providers' *current*
  values, not verified for October 2025. Land (GHCNh) stations have no
  published anemometer height and are excluded from the primary metrics.
* **Instrument failure.** Stations that stopped reporting near landfall
  (e.g. power or sensor loss) bias every sample toward the weaker parts of
  the storm; coverage.csv shows where this happened.
* **Direction** errors use only pairs where both speeds exceed the configured
  minimum; direction is undefined for calm and variable winds.
"""


def _lat(v: float) -> str:
    return f"{abs(v):.1f}°{'N' if v >= 0 else 'S'}"


def _lon(v: float) -> str:
    return f"{abs(v):.1f}°{'E' if v >= 0 else 'W'}"


METRIC_DEFINITIONS = """Model minus observation, in m/s, over matched hourly pairs. **n**: number of pairs; **bias**: mean(model − obs),
positive = model too strong; **MAE**: mean |model − obs|; **RMSE**: sqrt(mean (model − obs)²); **r**: Pearson
correlation; **obs_mean / model_mean**: mean speeds. GDAS = hourly f000-f005 series; GFS = composite with the
shortest lead >= 6 h. Full tables: `tables/summary_metrics.csv` and `tables/metrics.csv`; chart:
`figures/metrics_by_station.png`.
"""


def _summary_section(ev: dict) -> str:
    s = ev.get("summary", pd.DataFrame())
    cols = ["station", "model", "n", "obs_mean", "model_mean", "bias", "mae", "rmse", "correlation"]
    return f"## Summary metrics\n\n{METRIC_DEFINITIONS}\n{_fmt(s, cols)}"


def _averaging_text(cfg) -> str:
    """How observations meet the model valid times."""
    if cfg.obs_average_minutes:
        return (f"observations (adjusted to {cfg.target_height_m:g} m first) averaged over "
                f"{cfg.obs_average_minutes:g} min centred on each valid time, windows with less than "
                f"{cfg.obs_average_min_coverage:.0%} of the expected samples dropped; direction is the "
                "speed-weighted vector mean and the gust the window maximum")
    return f"nearest quality-passing observation within +/-{cfg.time_tolerance_minutes:g} min of each valid time"


def _fmt(df: pd.DataFrame, cols: list[str], digits: int = 2) -> str:
    if df.empty:
        return "_none_\n"
    d = df[[c for c in cols if c in df.columns]].copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:.{digits}f}")
    header = "| " + " | ".join(d.columns) + " |\n|" + "---|" * len(d.columns) + "\n"
    return header + "".join("| " + " | ".join(str(v) for v in row) + " |\n" for row in d.itertuples(index=False))


def requested_table(metrics: pd.DataFrame, stations: pd.DataFrame, avail: pd.DataFrame, cfg) -> pd.DataFrame:
    """One row per requested station: where it is, what data it had, how each model did."""
    req = stations[stations["key"].isin(cfg.include)][["key", "name", "cpa_distance_km", "station_set"]]
    req = req.merge(avail[["key", "status"]], on="key", how="left")
    st = metrics[(metrics["grouping"] == "station") & (metrics["reference"] == "adjusted")]
    for label, short in ((gdas_series(cfg), "gdas"), (GFS_COMPOSITE, "gfs")):
        m = st[st["model_label"] == label][["group", "n", "bias", "rmse", "correlation"]]
        m = m.rename(columns={"group": "key", **{c: f"{short}_{c}" for c in ("n", "bias", "rmse", "correlation")}})
        req = req.merge(m, on="key", how="left")
    req["name"] = req["name"].astype(str).str.slice(0, 32)
    return req.sort_values("cpa_distance_km").rename(columns={"cpa_distance_km": "track_km"})


PERIOD_LIMITATIONS = """\
* **Model resolution.** GFS/GDAS at 0.25 deg (~28 km) smooth fronts, sea
  breezes and coastal gradients; stations near the coast see a mixed
  land/sea grid box.
* **Averaging and sampling.** Model winds are instantaneous grid-box values at
  hourly valid times; between the 00/06/12/18Z cycles the GDAS series is a
  1-5 h GDAS forecast, not an analysis; observations are 8-minute (NDBC
  buoy), 2-minute (NDBC C-MAN) or 2-minute averages reported every 6 minutes
  (CO-OPS), matched to the nearest time within the tolerance.
* **Neutral log law.** Height adjustment assumes neutral stability, a
  homogeneous surface and a constant roughness length (for a 4.1 m buoy
  anemometer about +9 %). Stable or unstable conditions change the true
  profile; the unadjusted results show how much the adjustment matters.
* **Recent NDBC observations.** Months NDBC has not archived yet come from the
  realtime files, which have had only automated quality control and, at
  many stations (e.g. 41025), report speed rounded to whole m/s. That
  rounding (up to +/-0.5 m/s) is unbiased but adds to MAE and RMSE; NDBC's
  later archived values may differ slightly.
* **Metadata** (sensor heights, positions) are the providers' current values.
* **Gusts** are reported but never height-adjusted or verified.
* **Direction** errors use only pairs where both speeds exceed the configured
  minimum; direction is undefined for calm and variable winds.
"""


def write(run, ev: dict, obs: pd.DataFrame, stations: pd.DataFrame, figures: list[Path]) -> Path:
    cfg = run.cfg
    if not storm_in_period(run.track(), run.period()):
        return write_period(run, ev, obs, stations, figures)
    info = run.info()
    life = info["lifecycle"]
    files = pd.read_csv(run.p("model_files.csv"))
    avail = pd.read_csv(run.p("obs_availability.csv"), dtype={"station_id": str})
    metrics = ev["metrics"]
    out = cfg.report_path
    out.parent.mkdir(parents=True, exist_ok=True)

    overall = metrics[(metrics["grouping"] == "overall")].sort_values(["reference", "model_label"])
    by_lead = metrics[(metrics["grouping"] == "lead") & (metrics["reference"] == "adjusted")]
    by_lead = by_lead.assign(_start=pd.to_numeric(by_lead["group"])).sort_values("_start")
    cols = ["reference", "model_label", "n", "bias", "mae", "rmse", "correlation", "obs_max", "model_max",
            "n_direction", "direction_bias", "direction_mae"]

    passing = obs[obs["qc_pass"]].merge(stations[["key", "station_class"]], on="key", how="left")
    with_data = set(passing["key"])
    adj_counts = (passing.groupby(["adj_flag", "station_class"])["key"].nunique().rename("stations with valid wind")
                  .reset_index())
    by_station = metrics[(metrics["grouping"] == "station") & (metrics["reference"] == "adjusted")
                         & metrics["model_label"].isin([gdas_series(cfg), GFS_COMPOSITE])]
    requested = requested_table(metrics, stations, avail, cfg)
    by_set = metrics[(metrics["grouping"] == "station_set") & (metrics["reference"] == "adjusted")
                     & metrics["model_label"].isin([gdas_series(cfg), GFS_COMPOSITE])]
    st_cols = ["key", "name", "station_class", "cpa_distance_km", "elevation_m", "elevation_ref", "sensor_height_m",
               "sensor_height_ref", "use_in_metrics", "colocated_with"]
    z = obs.groupby("key")[["z_sensor_m", "z_rule", "z0_m", "height_factor"]].first().reset_index()
    st_table = stations[st_cols].merge(z, on="key", how="left").sort_values("cpa_distance_km")

    landfalls = "; ".join(f"{pd.Timestamp(lf['time']):%d %b %H%MZ} ({lf['vmax_kt']:.0f} kt, {lf['mslp_hpa']:.0f} hPa, "
                          f"{lf['lat']:.1f}N {abs(lf['lon']):.1f}W)" for lf in life["landfalls"])
    smoke_flag = " --smoke" if cfg.run_name == "smoke" else ""
    missing = info.get("missing_requested_stations") or []
    missing_note = f" Not found in any station list: {', '.join(missing)}." if missing else ""
    manifest = run.fetcher.manifest.read()
    n_dl = int((manifest["cache"] == "miss").sum()) if len(manifest) else 0

    text = f"""# Hurricane {info['name'].title()} ({info['storm_id']}): GFS and GDAS 10-m winds vs observations

Run `{cfg.run_name}`, generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%MZ} from `{info['config_file']}`.

{_summary_section(ev)}
## Storm identity and lifecycle

* Identifier **{info['storm_id']}**, basin {info['basin']} (North Atlantic); source: NHC {info['track_source']},
  cross-checked against ATCF `{info.get('atcf_file')}` ({info.get('atcf_fixes')} fixes):
  {'**no disagreements**' if not info['crosscheck_issues'] else f"{len(info['crosscheck_issues'])} disagreements (see run_info.json)"}.
* Other Atlantic storms named {info['name'].title()} in HURDAT2:
  {', '.join(f"{n['storm_id']} (peak {float(n['peak_vmax_kt']):.0f} kt)" for n in info['namesakes'] if n['storm_id'] != info['storm_id'])}.
* First fix {life['first_fix']} ({life['first_status']}); hurricane from {life['first_hurricane_fix']};
  last tropical fix {life['last_tropical_fix']}; post-tropical from {life['post_tropical_from']};
  last fix {life['last_fix']} ({life['last_status']}).
* Peak {life['peak_vmax_kt']:.0f} kt at {life['peak_vmax_time']}; minimum pressure {life['min_mslp_hpa']:.0f} hPa at
  {life['min_mslp_time']}.
* Landfalls: {landfalls}.
* Analysis period (UTC): **{info['period_start']} to {info['period_end']}**; domain
  {_lat(info['domain']['lat_min'])} to {_lat(info['domain']['lat_max'])},
  {_lon(info['domain']['lon_min'])} to {_lon(info['domain']['lon_max'])}.

## Observations

Stations within {cfg.radius_km:g} km (geodesic) of the interpolated best track, from
{', '.join(cfg.obs_sources)}: **{len(stations)}** selected
({', '.join(f'{k}: {v}' for k, v in stations.groupby('source').size().items())}).
Data retrieval: {', '.join(f'{k}: {v}' for k, v in avail.groupby('status').size().items())}.
{n_dl} downloads (this and earlier runs) are logged with URL, retrieval time and SHA-256 in
`{cfg.manifest_path.name}`.

Height adjustment (neutral log law to {cfg.target_height_m:g} m; z0 offshore {cfg.z0_m['offshore']:g} m,
coastal {cfg.z0_m['coastal']:g} m, land {cfg.z0_m['land']:g} m; coastal rule `{cfg.coastal_height_rule}`):

{_fmt(adj_counts, list(adj_counts.columns))}
### Stations with data, nearest first

{_fmt(st_table[st_table['key'].isin(with_data)].head(60), st_cols[:4] + ['sensor_height_m', 'z_sensor_m', 'z_rule', 'height_factor', 'use_in_metrics', 'colocated_with'], 3)}
## Models

* Valid times every {cfg.valid_step_hours} h. GDAS window(s) starting at
  {', '.join(f'f{w:03d}' for w in cfg.gdas_lead_windows)} (f000 is the final analysis, f001-f005 short-range
  forecasts from the same cycle); GFS lead windows starting at
  {', '.join(f'f{w:03d}' for w in cfg.gfs_lead_windows)}{' (6 consecutive leads each)' if cfg.valid_step_hours < 6 else ''}.
  `pgrb2.{cfg.resolution}`, cycles {', '.join(f'{c:02d}Z' for c in cfg.cycles)}, NOAA Open Data on AWS
  (`noaa-gfs-bdp-pds`); records {', '.join(':'.join(f) for f in cfg.model_fields)} via byte ranges.
* Files: {', '.join(f'{k}: {v}' for k, v in files.groupby(['source', 'status']).size().items())}.
* GFS composite = shortest available lead >= {cfg.composite_min_lead} h per valid time; per-lead results never
  mix runs.

## Collocation

{cfg.interpolation.title()} interpolation of u10/v10 to each station; speed and direction from the interpolated
components; {_averaging_text(cfg)}; storm-relative geometry from the geodesically interpolated best track.

## Results

Errors are model minus observation (m/s). `adjusted` = observations adjusted to {cfg.target_height_m:g} m
(primary); `unadjusted` = at sensor height (sensitivity, includes land stations). Groups with
n < {cfg.min_samples} are not scored.

{_fmt(overall, cols)}
### GFS by lead window (adjusted reference)

{_fmt(by_lead, ['model_label', 'mean_forecast_hour', 'n', 'bias', 'mae', 'rmse', 'correlation'], 1)}
### Near the track vs requested distant stations (adjusted reference)

{_fmt(by_set, ['model_label', 'group', 'n', 'bias', 'mae', 'rmse', 'correlation'])}
### By station (adjusted reference)

{_fmt(by_station, ['model_label', 'group', 'n', 'bias', 'mae', 'rmse', 'correlation', 'obs_max', 'model_max'])}
### Peak wind (adjusted reference, matched model times)

{_fmt(ev['peak_summary'], list(ev['peak_summary'].columns))}
Full breakdowns (station, cycle, lead, radial band, quadrant, status, closest-approach phase, station class)
are in `tables/metrics.csv`.

## Requested stations

Every station in `[stations].include`, nearest the track first. Metrics are against observations adjusted to
{cfg.target_height_m:g} m; `status` is the data-retrieval outcome. Stations with no wind in the period are listed,
not dropped.{missing_note}

{_fmt(requested, list(requested.columns), 2)}
## Gaps and failures

Observation stations without usable data:

{_fmt(avail[avail['status'] != 'ok'], ['key', 'status', 'detail'])}
Model files not available or failed:

{_fmt(files[files['status'] != 'ok'], ['source', 'init_time', 'forecast_hour', 'status', 'detail'])}
## Limitations

{LIMITATIONS}
## Figures

{chr(10).join(f"* `{p.relative_to(cfg.run_dir).as_posix()}`" for p in figures)}

## Reproduce

```bash
python scripts/hurricane_eval.py all --config config/{Path(str(info['config_file'])).name}{smoke_flag}
```
"""
    out.write_text(text, encoding="utf-8")
    return out


def write_period(run, ev: dict, obs: pd.DataFrame, stations: pd.DataFrame, figures: list[Path]) -> Path:
    """Report for a period away from the storm: no storm identity or storm-relative sections."""
    cfg = run.cfg
    info = run.info()
    files = pd.read_csv(run.p("model_files.csv"))
    avail = pd.read_csv(run.p("obs_availability.csv"), dtype={"station_id": str}).fillna({"detail": ""})
    metrics = ev["metrics"]
    out = cfg.report_path
    out.parent.mkdir(parents=True, exist_ok=True)
    pair = [gdas_series(cfg), GFS_COMPOSITE]

    overall = metrics[(metrics["grouping"] == "overall")].sort_values(["reference", "model_label"])
    by_lead = metrics[(metrics["grouping"] == "lead") & (metrics["reference"] == "adjusted")]
    by_lead = by_lead.assign(_start=pd.to_numeric(by_lead["group"])).sort_values("_start")
    by_station = metrics[(metrics["grouping"] == "station") & (metrics["reference"] == "adjusted")
                         & metrics["model_label"].isin(pair)].sort_values(["group", "model_label"])
    cols = ["reference", "model_label", "n", "bias", "mae", "rmse", "correlation", "obs_max", "model_max",
            "n_direction", "direction_bias", "direction_mae"]

    passing = obs[obs["qc_pass"]].merge(stations[["key", "station_class"]], on="key", how="left")
    adj_counts = (passing.groupby(["adj_flag", "station_class"])["key"].nunique().rename("stations with valid wind")
                  .reset_index())
    requested = requested_table(metrics, stations, avail, cfg).drop(columns=["track_km", "station_set"])
    requested = requested.sort_values("key")
    raw_files = obs.loc[obs["qc_pass"], "raw_file"].astype(str) if "raw_file" in obs else pd.Series(dtype=str)
    n_realtime = int(raw_files[raw_files.str.endswith(".txt")].str.split("_").str[0].nunique())
    missing = info.get("missing_requested_stations") or []
    missing_note = f" Not found in any station list: {', '.join(missing)}." if missing else ""
    start, end = run.period()

    networks = ", ".join(SOURCE_NAMES.get(x, x) for x in sorted(stations["source"].unique()))
    text = f"""# GFS and GDAS 10-m winds vs {networks} observations, {start:%d %b %Y} to {end:%d %b %Y}

Run `{cfg.run_name}`, generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%MZ} from `{info['config_file']}`
with `--start {start:%Y-%m-%dT%H:%M} --end {end:%Y-%m-%dT%H:%M}`.

* Analysis period (UTC): **{start} to {end}**. No storm from the configured best track
  ({info['storm_id']}) falls in this period, so storm-relative results are not computed.
* Domain {_lat(info['domain']['lat_min'])} to {_lat(info['domain']['lat_max'])},
  {_lon(info['domain']['lon_min'])} to {_lon(info['domain']['lon_max'])}.

{_summary_section(ev)}
## Observations

The **{len(stations)}** requested stations ({networks}).
Data retrieval: {', '.join(f'{k}: {v}' for k, v in avail.groupby('status').size().items())}.
{f"{n_realtime} NDBC stations used realtime files for months not yet archived (see Limitations)." if n_realtime else ""}

Height adjustment (neutral log law to {cfg.target_height_m:g} m; z0 offshore {cfg.z0_m['offshore']:g} m,
coastal {cfg.z0_m['coastal']:g} m; coastal rule `{cfg.coastal_height_rule}`):

{_fmt(adj_counts, list(adj_counts.columns))}
## Models

* Valid times every {cfg.valid_step_hours} h. GDAS window(s) starting at
  {', '.join(f'f{w:03d}' for w in cfg.gdas_lead_windows)} (f000 is the final analysis, f001-f005 short-range
  forecasts from the same cycle); GFS lead windows starting at
  {', '.join(f'f{w:03d}' for w in cfg.gfs_lead_windows)}{' (6 consecutive leads each)' if cfg.valid_step_hours < 6 else ''}.
  `pgrb2.{cfg.resolution}`, NOAA Open Data on AWS (`noaa-gfs-bdp-pds`).
* Files: {', '.join(f'{k}: {v}' for k, v in files.groupby(['source', 'status']).size().items())}.
* GFS composite = shortest available lead >= {cfg.composite_min_lead} h per valid time.
* {cfg.interpolation.title()} interpolation of u10/v10 to each station; {_averaging_text(cfg)}.

## Results

Errors are model minus observation (m/s). `adjusted` = observations adjusted to {cfg.target_height_m:g} m
(primary); `unadjusted` = at sensor height (sensitivity). Groups with n < {cfg.min_samples} are not scored.

{_fmt(overall, cols)}
### GFS by lead window (adjusted reference)

{_fmt(by_lead, ['model_label', 'mean_forecast_hour', 'n', 'bias', 'mae', 'rmse', 'correlation'], 1)}
### By station (adjusted reference)

{_fmt(by_station, ['model_label', 'group', 'n', 'bias', 'mae', 'rmse', 'correlation', 'obs_max', 'model_max'])}
### Peak wind (adjusted reference, matched model times)

{_fmt(ev['peak_summary'], list(ev['peak_summary'].columns))}
Full breakdowns (station, cycle, lead, station class) are in `tables/metrics.csv`.

## Requested stations

Metrics against observations adjusted to {cfg.target_height_m:g} m; `status` is the data-retrieval outcome.
Stations with no wind in the period are listed, not dropped.{missing_note}

{_fmt(requested, list(requested.columns), 2)}
## Gaps and failures

Observation stations without usable data:

{_fmt(avail[avail['status'] != 'ok'], ['key', 'status', 'detail'])}
Model files not available or failed:

{_fmt(files[files['status'] != 'ok'], ['source', 'init_time', 'forecast_hour', 'status', 'detail'])}
## Limitations

{PERIOD_LIMITATIONS}
## Figures

{chr(10).join(f"* `{p.relative_to(cfg.run_dir).as_posix()}`" for p in figures)}

## Reproduce

```bash
python scripts/hurricane_eval.py all --config config/{Path(str(info['config_file'])).name} \
    --start {start:%Y-%m-%dT%H:%M} --end {end:%Y-%m-%dT%H:%M} --run-name {cfg.run_name}
```
"""
    out.write_text(text, encoding="utf-8")
    return out
