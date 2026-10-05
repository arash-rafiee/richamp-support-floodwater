r"""Adjust observed sustained wind speed to a common height (default 10 m).

Neutral logarithmic profile
---------------------------
.. math::

    U(z_t) = U(z_s)\,\frac{\ln(z_t / z_0)}{\ln(z_s / z_0)}

with :math:`z_s` the sensor height above the surface the profile refers to,
:math:`z_t` the target height and :math:`z_0` the aerodynamic roughness
length. Only the sustained speed is adjusted; gusts are left at sensor
height because a gust factor depends on averaging time and turbulence
intensity, not only on height.

Which height is :math:`z_s`
---------------------------
* offshore (buoys): anemometer height above the site; NDBC gives the site
  as sea level, so this is height above the mean water surface.
* coastal (C-MAN, CO-OPS): with ``coastal_height_rule = "site_plus_sensor"``
  (default), site elevation above MSL plus anemometer height above the site,
  i.e. height above the water for a pier, platform or lighthouse. With
  ``"sensor_only"``, anemometer height above the site.
* land: GHCNh publishes no anemometer height, so land records are not
  adjusted (``height_unknown``) unless ``[height.sensor_height_overrides]``
  supplies a documented value.
Station elevation above MSL is never used as a sensor height on its own.

Records are left unadjusted, and flagged, when the height is missing or not
positive (``height_unknown`` / ``height_invalid``), when the reference of the
published height is not understood (``height_reference_unknown``), or when
:math:`z_s \le z_0` (``height_below_z0``).

Limitations (neutral stability)
-------------------------------
The log law assumes neutral stratification, a horizontally homogeneous
surface and a constant-flux layer. Under hurricane conditions it is used
only because nothing better is justified by the metadata available:
stability corrections need air-sea temperature differences; over the ocean
:math:`z_0` depends on wind speed and sea state (Charnock), and drag appears
to level off or fall above ~30-40 m/s; buoy anemometers at 3-5 m can be
sheltered by wave crests and tilted by hull motion in high seas; coastal
anemometers see flow distortion by structures and mixed land/water fetch.
For a 4.1 m buoy anemometer and z0 = 0.0002 m the factor is about 1.09; a
tenfold change in z0 changes it by only a few percent, which is small next
to these other uncertainties. No hurricane-specific roughness formulation is
applied.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from wind_data.hurricane.config import HurricaneConfig, station_key

METHOD = "neutral_log_law"


def log_law_factor(z_sensor, z_target, z0):
    """``ln(z_target/z0) / ln(z_sensor/z0)``; NaN wherever it is undefined."""
    zs, zt, z0 = (np.asarray(a, dtype=float) for a in (z_sensor, z_target, z0))
    with np.errstate(divide="ignore", invalid="ignore"):
        f = np.log(zt / z0) / np.log(zs / z0)
    ok = np.isfinite(zs) & np.isfinite(zt) & np.isfinite(z0) & (zs > z0) & (zt > z0) & (z0 > 0)
    return np.where(ok, f, np.nan)


def adjust_speed(speed, z_sensor, z_target, z0):
    """Speed at ``z_target`` from speed at ``z_sensor`` (NaN where undefined)."""
    return np.asarray(speed, dtype=float) * log_law_factor(z_sensor, z_target, z0)


def log_law_height(station: pd.Series | dict, cfg: HurricaneConfig) -> tuple[float, str]:
    """Height above the log-law surface for one station, and how it was obtained.

    Returns ``(height_m, rule)``; ``height_m`` is NaN when it cannot be
    determined, and ``rule`` then says why.
    """
    key = station_key(station["source"], station["station_id"])
    if key in cfg.sensor_height_overrides:
        return float(cfg.sensor_height_overrides[key]), "config override"

    h = float(station.get("sensor_height_m", np.nan))
    href = str(station.get("sensor_height_ref", "") or "").lower()
    elev = float(station.get("elevation_m", np.nan))
    eref = str(station.get("elevation_ref", "") or "").lower()
    cls = station["station_class"]

    if not np.isfinite(h):
        return np.nan, "height_unknown"
    if h <= 0:
        return np.nan, "height_invalid"
    relative_to_site = "site" in href
    relative_to_msl = "msl" in href or "mean sea level" in href
    if cls == "offshore":
        if relative_to_msl or (relative_to_site and np.isfinite(elev) and elev == 0 and "msl" in eref):
            return h, "anemometer above sea level (site = sea level)"
        if relative_to_site:
            return h, "anemometer above site (buoy deck)"
        return np.nan, "height_reference_unknown"
    if cls == "coastal":
        if relative_to_msl:
            return h, "anemometer above MSL"
        if not relative_to_site:
            return np.nan, "height_reference_unknown"
        if cfg.coastal_height_rule == "sensor_only":
            return h, "anemometer above site (sensor_only rule)"
        if np.isfinite(elev) and ("msl" in eref or "mean sea level" in eref):
            return elev + h, "site elevation above MSL + anemometer above site"
        return np.nan, "height_reference_unknown (site elevation not relative to MSL)"
    # land
    if relative_to_site or "ground" in href:
        return h, "anemometer above ground"
    return np.nan, "height_reference_unknown"


def apply(obs: pd.DataFrame, stations: pd.DataFrame, cfg: HurricaneConfig) -> pd.DataFrame:
    """Add height-adjusted speed and provenance columns to cleaned observations.

    New columns: ``z_sensor_m``, ``z_rule``, ``z0_m``, ``z_target_m``,
    ``height_factor``, ``wind_speed_adj``, ``adj_method``, ``adj_flag``.
    ``wind_speed`` (as measured) is never overwritten.
    """
    rows = []
    for _, st in stations.iterrows():
        z, rule = log_law_height(st, cfg)
        z0 = cfg.z0_for(st["source"], st["station_id"], st["station_class"])
        rows.append({"source": st["source"], "station_id": st["station_id"], "z_sensor_m": z, "z_rule": rule,
                     "z0_m": z0})
    meta = pd.DataFrame(rows, columns=["source", "station_id", "z_sensor_m", "z_rule", "z0_m"])
    out = obs.merge(meta, on=["source", "station_id"], how="left")
    out["z_target_m"] = cfg.target_height_m
    out["height_factor"] = log_law_factor(out["z_sensor_m"], out["z_target_m"], out["z0_m"])

    flag = np.where(out["z_rule"].isna(), "station_metadata_missing", "adjusted")
    rule = out["z_rule"].fillna("").to_numpy(dtype=object)
    for code in ("height_unknown", "height_invalid", "height_reference_unknown"):
        flag = np.where([r.startswith(code) for r in rule], code, flag)
    zs = out["z_sensor_m"].to_numpy(dtype=float)
    flag = np.where((flag == "adjusted") & np.isfinite(zs) & (zs <= out["z0_m"].to_numpy()), "height_below_z0", flag)
    at_target = (flag == "adjusted") & np.isclose(zs, cfg.target_height_m)
    flag = np.where(at_target, "at_target_height", flag)

    ok = np.isin(flag, ("adjusted", "at_target_height"))
    out["height_factor"] = np.where(at_target, 1.0, np.where(ok, out["height_factor"], np.nan))
    out["wind_speed_adj"] = np.where(ok, out["wind_speed"] * out["height_factor"], np.nan)
    out["adj_method"] = np.where(ok, METHOD, "none")
    out["adj_flag"] = flag
    return out
