"""Verification statistics for GFS and GDAS against observed winds.

Model labels
------------
``GDAS f000-f005``     the hourly GDAS series: f000 final analysis at the
                       cycles, f001-f005 short-range forecasts between
``GDAS analysis``      the f000 subset only (6-hourly)
``GFS fLLL-fMMM``      one GFS lead window: every hourly valid time from the
                       one run whose lead lies in the window
``GFS composite``      per (station, valid time), the GFS run with the
                       shortest lead >= ``composite_min_lead``; used for the
                       single "GFS" series in figures.
No label ever mixes two runs at the same valid time.

References
----------
``adjusted`` (primary)  observed sustained speed adjusted to the target
                        height. Only stations whose sensor height is known
                        and that are not duplicates of another network's
                        instrument contribute.
``unadjusted``          observed speed at sensor height (sensitivity run;
                        includes land stations with unknown height).

Groups with fewer than ``min_samples`` pairs are listed with their count
and ``scored = False``; their metrics are left blank.

Errors are model minus observation. Direction errors are the shortest signed
angle (circular), computed only where both speeds are at least
``min_speed_for_direction``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from wind_data.analysis import statistics as st
from wind_data.hurricane.config import HurricaneConfig
from wind_data.hurricane.models import window_label
from wind_data.processing.wind import direction_difference

REFERENCES = {"adjusted": "obs_speed_adj", "unadjusted": "obs_speed"}
GROUPINGS = {
    "overall": [],
    "station": ["key"],
    "init_cycle": ["init_hour"],
    "lead": ["lead_window"],
    "forecast_hour": ["forecast_hour"],
    "radial_band": ["radial_band"],
    "quadrant": ["quadrant"],
    "motion_quadrant": ["motion_quadrant"],
    "storm_status": ["storm_status"],
    "cpa_phase": ["cpa_phase"],
    "station_class": ["station_class"],
    "station_set": ["station_set"],
}


GDAS_ANALYSIS = "GDAS analysis"
GFS_COMPOSITE = "GFS composite"


def gdas_series(cfg: HurricaneConfig) -> str:
    """Label of the main (first-window) GDAS series, e.g. 'GDAS f000-f005'."""
    return window_label("gdas", cfg.gdas_lead_windows[0])


def model_label(model: str, window: int) -> str:
    """Always the full window range: with 6-hourly valid times a window still
    holds whichever of its leads falls on a synoptic time (f120 for 115-120)."""
    return window_label(model, int(window))


def add_labels(matched: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """Label every row by model and lead window; append the GDAS-analysis and GFS-composite rows."""
    m = matched.copy()
    m["model_label"] = [model_label(a, b) for a, b in zip(m["model"], m["lead_window"])]
    m["init_hour"] = pd.to_datetime(m["init_time"]).dt.hour.map(lambda h: f"{h:02d}Z")
    extra = [m[(m["model"] == "gdas") & (m["forecast_hour"] == 0)].assign(model_label=GDAS_ANALYSIS)]
    gfs = m[(m["model"] == "gfs") & (m["forecast_hour"] >= cfg.composite_min_lead) & m["model_speed"].notna()]
    extra.append(gfs.sort_values("forecast_hour", kind="stable")
                    .drop_duplicates(["key", "valid_time"], keep="first")
                    .assign(model_label=GFS_COMPOSITE))
    return pd.concat([m, *extra], ignore_index=True)


def eligible(m: pd.DataFrame, reference: str) -> pd.DataFrame:
    col = REFERENCES[reference]
    ok = (m["match_flag"] == "matched") & m[col].notna() & m["model_speed"].notna() & m["use_in_metrics"].astype(bool)
    return m[ok]


def metrics(g: pd.DataFrame, obs_col: str, cfg: HurricaneConfig) -> dict[str, float]:
    o = g[obs_col].to_numpy(dtype=float)
    mdl = g["model_speed"].to_numpy(dtype=float)
    n = st.count(mdl, o)
    out: dict[str, float] = {"n": n}
    if "forecast_hour" in g:
        out["mean_forecast_hour"] = float(g["forecast_hour"].mean())
    if n < cfg.min_samples:
        out["scored"] = False
        return out
    out.update(scored=True, bias=st.bias(mdl, o), mae=st.mae(mdl, o), rmse=st.rmse(mdl, o),
               correlation=st.correlation(mdl, o), obs_mean=float(np.nanmean(o)), model_mean=float(np.nanmean(mdl)),
               obs_max=float(np.nanmax(o)), model_max=float(np.nanmax(mdl)))
    for q in cfg.speed_quantiles:
        out[f"obs_q{q:g}"] = float(np.nanquantile(o, q))
        out[f"model_q{q:g}"] = float(np.nanquantile(mdl, q))
    both = (o >= cfg.min_speed_for_direction) & (mdl >= cfg.min_speed_for_direction)
    od, md = g["obs_direction"].to_numpy(dtype=float)[both], g["model_direction"].to_numpy(dtype=float)[both]
    out["n_direction"] = st.count(md, od)
    if out["n_direction"] >= cfg.min_samples:
        out["direction_bias"] = st.direction_bias(md, od)
        out["direction_mae"] = st.direction_mae(md, od)
    return out


def grouped(m: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """Every grouping x model label x reference, one row each."""
    rows = []
    for reference, col in REFERENCES.items():
        e = eligible(m, reference)
        for gname, keys in GROUPINGS.items():
            data = e
            if gname in ("lead", "forecast_hour"):  # GFS windows only, each counted once
                data = e[(e["model"] == "gfs") & (e["model_label"] != GFS_COMPOSITE)]
            by = ["model_label"] + keys
            for key, g in data.groupby(by, sort=True, dropna=False):
                key = key if isinstance(key, tuple) else (key,)
                if any(isinstance(k, str) and k == "" for k in key[1:]):
                    continue  # e.g. outside the track, or beyond the last radial band
                rows.append({"reference": reference, "grouping": gname, "model_label": key[0],
                             "group": ", ".join(str(k) for k in key[1:]) or "all", **metrics(g, col, cfg)})
    return pd.DataFrame(rows)


def peaks(m: pd.DataFrame, obs_full: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """Peak-wind magnitude and timing, per station and model label (adjusted reference).

    ``obs_peak`` / ``model_peak`` are taken over the matched valid times, so
    both sides see the same sampling. ``obs_peak_record`` is the highest
    quality-passing adjusted value in the full observation record, for
    context: it shows what sampling at model times misses.
    """
    e = eligible(m, "adjusted")
    rows = []
    for (label, key), g in e.groupby(["model_label", "key"]):
        if len(g) < 2:
            continue
        io, im = g["obs_speed_adj"].idxmax(), g["model_speed"].idxmax()
        rows.append({"model_label": label, "key": key, "n": len(g),
                     "obs_peak": g.at[io, "obs_speed_adj"], "obs_peak_time": g.at[io, "valid_time"],
                     "model_peak": g.at[im, "model_speed"], "model_peak_time": g.at[im, "valid_time"],
                     "peak_error": g.at[im, "model_speed"] - g.at[io, "obs_speed_adj"],
                     "peak_timing_error_h": (g.at[im, "valid_time"] - g.at[io, "valid_time"]).total_seconds() / 3600})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    full = obs_full[obs_full["qc_pass"] & obs_full["wind_speed_adj"].notna()]
    rec = full.loc[full.groupby("key")["wind_speed_adj"].idxmax(), ["key", "wind_speed_adj", "time"]]
    rec = rec.rename(columns={"wind_speed_adj": "obs_peak_record", "time": "obs_peak_record_time"})
    return out.merge(rec, on="key", how="left")


def peak_summary(peak_table: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    rows = []
    for label, g in peak_table.groupby("model_label"):
        row = {"model_label": label, "n_stations": len(g)}
        if len(g) >= 3:
            row.update(mean_peak_error=g["peak_error"].mean(), median_peak_error=g["peak_error"].median(),
                       mean_abs_timing_error_h=g["peak_timing_error_h"].abs().mean())
        rows.append(row)
    return pd.DataFrame(rows)


def direction_errors(m: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """Signed circular direction error per matched pair (for histograms)."""
    e = eligible(m, "unadjusted")
    both = (e["obs_speed"] >= cfg.min_speed_for_direction) & (e["model_speed"] >= cfg.min_speed_for_direction)
    e = e[both & e["obs_direction"].notna() & e["model_direction"].notna()]
    return e.assign(direction_error=direction_difference(e["model_direction"].to_numpy(),
                                                         e["obs_direction"].to_numpy()))[
        ["model_label", "key", "valid_time", "obs_direction", "model_direction", "direction_error"]]


def coverage(m: pd.DataFrame, obs_full: pd.DataFrame, stations: pd.DataFrame,
             period: tuple[pd.Timestamp, pd.Timestamp], cfg: HurricaneConfig) -> pd.DataFrame:
    """Per station: observation record completeness and model-match coverage."""
    rows = []
    gdas_label = gdas_series(cfg)
    hours = (period[1] - period[0]).total_seconds() / 3600
    for s in stations.itertuples(index=False):
        o = obs_full[obs_full["key"] == s.key]
        g = m[(m["key"] == s.key) & (m["model_label"] == gdas_label)]
        rows.append({
            "key": s.key, "name": s.name, "station_class": s.station_class, "cpa_distance_km": s.cpa_distance_km,
            "n_obs": len(o), "n_obs_pass": int(o["qc_pass"].sum()) if len(o) else 0,
            "n_obs_adjusted": int(o["wind_speed_adj"].notna().sum()) if len(o) else 0,
            "hours_with_obs": int(o.loc[o["qc_pass"], "time"].dt.floor("h").nunique()) if len(o) else 0,
            "period_hours": int(hours),
            "model_times": len(g), "model_times_matched": int((g["match_flag"] == "matched").sum()),
            "match_fraction": float((g["match_flag"] == "matched").mean()) if len(g) else np.nan,
            "use_in_metrics": s.use_in_metrics,
        })
    return pd.DataFrame(rows)


def evaluate(matched: pd.DataFrame, obs_full: pd.DataFrame, stations: pd.DataFrame, cfg: HurricaneConfig,
             period: tuple[pd.Timestamp, pd.Timestamp]) -> dict[str, pd.DataFrame]:
    m = add_labels(matched, cfg)
    pk = peaks(m, obs_full, cfg)
    metrics = grouped(m, cfg)
    with_pairs = stations[stations["key"].isin(m.loc[m["match_flag"] == "matched", "key"])]
    return {
        "labelled": m,
        "metrics": metrics,
        "summary": summary(metrics, with_pairs, cfg),
        "peaks": pk,
        "peak_summary": peak_summary(pk, cfg) if len(pk) else pd.DataFrame(),
        "direction_errors": direction_errors(m, cfg),
        "coverage": coverage(m, obs_full, stations, period, cfg),
    }


def summary(metrics: pd.DataFrame, stations: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """One row per (station or 'All stations', model): n, bias, MAE, RMSE, r, for GDAS and the GFS composite.

    Hourly pairs against observations adjusted to the target height (the
    sensor-height reference only for a station that cannot be adjusted).
    Stations in ``stations`` order; groups below ``min_samples`` are left out.
    """
    pair = {gdas_series(cfg): "GDAS", GFS_COMPOSITE: "GFS"}
    scored = metrics[metrics["model_label"].isin(pair) & (metrics["scored"] == True)]  # noqa: E712
    rows = []
    names = dict(zip(stations["key"], stations["name"].astype(str)))
    for key in [*stations["key"], None]:
        sel = scored[(scored["grouping"] == "station") & (scored["group"] == key)] if key else \
            scored[scored["grouping"] == "overall"]
        ref = "adjusted" if (sel["reference"] == "adjusted").any() else "unadjusted"
        for r in sel[sel["reference"] == ref].itertuples(index=False):
            rows.append({"station": names.get(key, key) if key else "All stations", "key": key or "",
                         "model": pair[r.model_label], "reference": ref, "n": int(r.n), "bias": r.bias,
                         "mae": r.mae, "rmse": r.rmse, "correlation": r.correlation, "obs_mean": r.obs_mean,
                         "model_mean": r.model_mean})
    return pd.DataFrame(rows, columns=["station", "key", "model", "reference", "n", "obs_mean", "model_mean",
                                       "bias", "mae", "rmse", "correlation"])
