"""Storm-relative geometry, and collocation of model fields with observations.

Storm-relative columns (:func:`storm_relative`)
-----------------------------------------------
``storm_*``            best-track centre, intensity, status, RMW and motion at
                       the row's time (NaN / '' outside the track)
``dist_center_km``     geodesic distance station <-> storm centre
``bearing_from_center``azimuth centre -> station (deg from north)
``quadrant``           NE / SE / SW / NW of the centre
``motion_quadrant``    front-right / rear-right / rear-left / front-left
``radial_band``        configured distance band ('' beyond the last edge)
``r_over_rmw``         distance / HURDAT2 radius of maximum wind
``cpa_time``, ``cpa_distance_km``  station's closest approach to the track
``hours_from_cpa``     (time - cpa_time) in hours; negative = before
``cpa_phase``          before / during / after (``cpa_window_hours``)

Collocation (:func:`collocate`)
-------------------------------
1. **Space.** u10, v10 and PRMSL are interpolated to each station from the
   cropped model grid, bilinearly by default (``interpolation =
   "nearest"`` is the alternative). Speed and direction are computed from
   the *interpolated components*, never by interpolating speed. Station and
   grid longitudes are both in [-180, 180). ``grid_distance_km`` is the
   distance to the nearest grid node.
2. **Time.** With ``obs_average_minutes`` set, observations are first
   averaged over that window centred on each valid time
   (:func:`average_observations`). For every model valid time the
   quality-passing (averaged) observation closest in time is then taken, if
   within ``time_tolerance_minutes``;
   ``obs_time`` and ``time_offset_min`` record the match. Otherwise the row
   is kept with ``match_flag = "no_obs_within_tolerance"``.
3. **Provenance.** Every row keeps ``source``, ``init_time``,
   ``forecast_hour``, ``valid_time`` and ``field_type``, so fields from
   different runs are never confused.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import xarray as xr

from wind_data import schema
from wind_data.hurricane import geodesy
from wind_data.hurricane.besttrack import BestTrack
from wind_data.hurricane.config import HurricaneConfig
from wind_data.hurricane.models import PRMSL
from wind_data.processing.wind import wind_direction, wind_speed

log = logging.getLogger(__name__)

STATION_KEYS = ["source", "station_id"]


def storm_relative(df: pd.DataFrame, track: BestTrack, stations: pd.DataFrame, cfg: HurricaneConfig,
                   *, time_col: str = "time") -> pd.DataFrame:
    """Add storm-relative columns to rows that carry ``source``/``station_id``/time."""
    st = stations[STATION_KEYS + ["lat", "lon", "cpa_time", "cpa_distance_km"]]
    out = df.drop(columns=[c for c in ("lat", "lon", "cpa_time", "cpa_distance_km") if c in df], errors="ignore")
    out = out.merge(st, on=STATION_KEYS, how="left")
    times = pd.to_datetime(out[time_col])
    uniq = pd.DatetimeIndex(times.unique())
    center = track.at(uniq).set_index("time")
    center = center.reindex(times.to_numpy()).reset_index(drop=True)
    for col in center.columns:
        out[col] = center[col].to_numpy()

    dist, bearing = geodesy.inverse(out["storm_lat"], out["storm_lon"], out["lat"], out["lon"])
    out["dist_center_km"] = dist
    out["bearing_from_center"] = bearing
    out["quadrant"] = geodesy.earth_quadrant(bearing)
    out["motion_quadrant"] = geodesy.motion_quadrant(bearing, out["storm_heading_deg"])
    out["radial_band"] = geodesy.radial_band(dist, cfg.radial_bands_km)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["r_over_rmw"] = np.where(out["storm_rmw_km"] > 0, dist / out["storm_rmw_km"], np.nan)
    cpa = pd.to_datetime(out["cpa_time"])
    out["hours_from_cpa"] = (times - cpa).dt.total_seconds() / 3600.0
    if len(out) and not out["on_track"].astype(bool).any():
        out["hours_from_cpa"] = np.nan  # a period away from the storm: "hours from its closest approach" is meaningless
    out["cpa_phase"] = geodesy.cpa_phase(out["hours_from_cpa"], cfg.cpa_window_hours)
    return out


def interpolate_to_stations(ds: xr.Dataset, lat: np.ndarray, lon: np.ndarray, method: str) -> pd.DataFrame:
    """Model values at station points (one time slice)."""
    lon = (np.asarray(lon, dtype=float) + 180.0) % 360.0 - 180.0
    pts = {schema.LATITUDE: xr.DataArray(lat, dims="station"), schema.LONGITUDE: xr.DataArray(lon, dims="station")}
    variables = [v for v in (schema.U10, schema.V10, PRMSL) if v in ds]
    sub = ds[variables].isel(time=0) if "time" in ds[variables].dims else ds[variables]
    if method == "nearest":
        got = sub.sel(pts, method="nearest")
    else:
        got = sub.interp(pts, method="linear")
    out = pd.DataFrame({v: got[v].values.astype(float) for v in variables})
    if PRMSL not in out:
        out[PRMSL] = np.nan
    out[schema.WIND_SPEED] = wind_speed(out[schema.U10].to_numpy(), out[schema.V10].to_numpy())
    out[schema.WIND_DIRECTION] = wind_direction(out[schema.U10].to_numpy(), out[schema.V10].to_numpy())

    glat, glon = ds[schema.LATITUDE].values, ds[schema.LONGITUDE].values
    ilat = np.abs(glat[:, None] - np.asarray(lat)[None, :]).argmin(axis=0)
    ilon = np.abs(glon[:, None] - lon[None, :]).argmin(axis=0)
    out["grid_distance_km"] = geodesy.distance_km(glat[ilat], glon[ilon], lat, lon)
    inside = ((lat >= glat.min()) & (lat <= glat.max()) & (lon >= glon.min()) & (lon <= glon.max()))
    out.loc[~inside, [schema.U10, schema.V10, PRMSL, schema.WIND_SPEED, schema.WIND_DIRECTION]] = np.nan
    out["model_flag"] = np.where(~inside, "outside_model_domain",
                                 np.where(out[schema.WIND_SPEED].isna(), "model_value_missing", "ok"))
    return out


def model_at_stations(files: pd.DataFrame, stations: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """Interpolate every available model file to every station."""
    ok = files[files["status"] == "ok"]
    lat = stations["lat"].to_numpy(dtype=float)
    lon = stations["lon"].to_numpy(dtype=float)
    frames = []
    for row in ok.itertuples(index=False):
        with xr.open_dataset(row.processed_file, decode_timedelta=False) as ds:
            vals = interpolate_to_stations(ds.load(), lat, lon, cfg.interpolation)
        vals.insert(0, "station_id", stations["station_id"].to_numpy())
        vals.insert(0, "source", stations["source"].to_numpy())
        vals = vals.rename(columns={schema.U10: "model_u10", schema.V10: "model_v10", PRMSL: "model_prmsl_hpa",
                                    schema.WIND_SPEED: "model_speed", schema.WIND_DIRECTION: "model_direction"})
        vals.insert(2, "model", row.source)
        vals.insert(3, "lead_window", int(row.lead_window))
        vals.insert(4, "init_time", pd.Timestamp(row.init_time))
        vals.insert(5, "forecast_hour", int(row.forecast_hour))
        vals.insert(6, "valid_time", pd.Timestamp(row.valid_time))
        vals.insert(7, "field_type", row.field_type)
        frames.append(vals)
    if not frames:
        raise RuntimeError("no model files available to collocate; run the models stage first")
    out = pd.concat(frames, ignore_index=True)
    out["interp_method"] = cfg.interpolation
    return out


def average_observations(obs: pd.DataFrame, minutes: int, min_coverage: float = 0.5) -> pd.DataFrame:
    """Means of quality-passing observations over ``minutes`` centred on each whole interval.

    Labelled at the window centre (e.g. 12:00 for 11:30 <= t < 12:30), so a
    mean lines up with an instantaneous model value at the same valid time.
    Speed (sensor height and 10 m) is a scalar mean; direction is the
    speed-weighted vector mean; the gust is the window maximum. A window
    needs ``min_coverage`` of the samples the station's own sampling interval
    implies (median spacing), otherwise it is dropped.
    """
    good = obs[obs["qc_pass"]].sort_values(["source", "station_id", "time"]).copy()
    if good.empty:
        return good
    freq = pd.Timedelta(minutes=minutes)
    good["_bin"] = (good["time"] + freq / 2).dt.floor(freq)  # [t - w/2, t + w/2) -> t
    spd = good["wind_speed"].to_numpy(dtype=float)
    rad = np.deg2rad(good["wind_direction"].to_numpy(dtype=float))
    good["_u"], good["_v"] = -spd * np.sin(rad), -spd * np.cos(rad)  # NaN for calm / missing direction
    step = good.groupby(["source", "station_id"])["time"].diff()
    good["_expected"] = freq / step.groupby([good["source"], good["station_id"]]).transform("median")

    g = good.groupby(["source", "station_id", "_bin"], sort=True)
    out = g.agg(wind_speed=("wind_speed", "mean"), wind_speed_adj=("wind_speed_adj", "mean"),
                wind_gust=("wind_gust", "max"), _u=("_u", "mean"), _v=("_v", "mean"),
                n_samples=("wind_speed", "count"), _expected=("_expected", "first"),
                first_sample=("time", "min"), last_sample=("time", "max"))
    meta = [c for c in ("averaging", "z_sensor_m", "z_rule", "z0_m", "z_target_m", "height_factor", "adj_method",
                        "adj_flag", "station_class") if c in good.columns]
    out = out.join(g[meta].first()).reset_index().rename(columns={"_bin": "time"})
    out["coverage"] = out["n_samples"] / out["_expected"]
    out = out[out["coverage"] >= min_coverage].copy()
    out["wind_direction"] = np.where(np.hypot(out["_u"], out["_v"]) > 0,
                                     np.rad2deg(np.arctan2(-out["_u"], -out["_v"])) % 360.0, np.nan)
    out["averaging"] = [f"{minutes:g}-min mean of {n} x {a}" for n, a in zip(out["n_samples"], out["averaging"])]
    out["calm"] = out["wind_speed"] == 0
    out["qc_pass"] = True
    out["qc_flags"] = out["source_qc"] = out["missing"] = ""
    return out.drop(columns=["_u", "_v", "_expected"]).reset_index(drop=True)


def match_observations(model: pd.DataFrame, obs: pd.DataFrame, tolerance_minutes: float) -> pd.DataFrame:
    """Attach the nearest quality-passing observation to each model row."""
    good = obs[obs["qc_pass"]].copy()
    good = good.rename(columns={"time": "obs_time"})
    good["_t"] = good["obs_time"]
    keep = ["source", "station_id", "_t", "obs_time", "wind_speed", "wind_speed_adj", "wind_direction", "wind_gust",
            "averaging", "qc_flags", "source_qc", "missing", "calm", "z_sensor_m", "z_rule", "z0_m", "z_target_m",
            "height_factor", "adj_method", "adj_flag"]
    good = good[[c for c in keep if c in good.columns]]
    good = good.rename(columns={"wind_speed": "obs_speed", "wind_speed_adj": "obs_speed_adj",
                                "wind_direction": "obs_direction", "wind_gust": "obs_gust",
                                "qc_flags": "obs_qc_flags", "source_qc": "obs_source_qc", "missing": "obs_missing",
                                "calm": "obs_calm"})
    m = model.copy()
    m["_t"] = m["valid_time"]
    m["_key"] = m["source"] + ":" + m["station_id"]
    good["_key"] = good["source"] + ":" + good["station_id"]
    m = m.sort_values("_t", kind="stable")
    good = good.sort_values("_t", kind="stable").drop(columns=["source", "station_id"])
    merged = pd.merge_asof(m, good, on="_t", by="_key", direction="nearest",
                           tolerance=pd.Timedelta(minutes=tolerance_minutes))
    merged["time_offset_min"] = (merged["obs_time"] - merged["valid_time"]).dt.total_seconds() / 60.0
    merged["match_flag"] = np.where(merged["obs_time"].isna(), "no_obs_within_tolerance", "matched")
    return merged.drop(columns=["_t", "_key"])


def collocate(files: pd.DataFrame, stations: pd.DataFrame, obs: pd.DataFrame, track: BestTrack,
              cfg: HurricaneConfig) -> pd.DataFrame:
    """The tidy matched dataset: one row per (model field, station)."""
    model = model_at_stations(files, stations, cfg)
    matched = match_observations(model, obs, cfg.time_tolerance_minutes)
    matched = storm_relative(matched, track, stations, cfg, time_col="valid_time")
    meta = stations[STATION_KEYS + ["key", "name", "station_class", "platform_type", "elevation_m", "elevation_ref",
                                    "sensor_height_m", "sensor_height_ref", "use_in_metrics", "colocated_with",
                                    "station_set", "selected_by"]]
    matched = matched.merge(meta, on=STATION_KEYS, how="left")
    matched = matched.rename(columns={"source": "obs_source"})
    return matched.sort_values(["key", "model", "forecast_hour", "valid_time"]).reset_index(drop=True)
