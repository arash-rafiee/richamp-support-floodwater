"""Station discovery and metadata for NDBC, NOAA CO-OPS and NCEI GHCNh.

Stations are selected by their minimum geodesic distance to the interpolated
best track (see :func:`select_stations`), plus any listed in
``[stations].include``. Every metadata value comes from the provider; where a
provider publishes nothing (GHCNh anemometer height), the field is left NaN
and the reason recorded, never filled in.

Height fields, as published
---------------------------
``elevation_m`` / ``elevation_ref``
    Station (site) elevation and what it is relative to, e.g. ``0.0`` /
    ``"MSL (NDBC: 'sea level')"``.
``sensor_height_m`` / ``sensor_height_ref``
    Anemometer height and its reference, e.g. ``4.1`` / ``"above site
    elevation"``. This is the height above the ground or deck, not above MSL.
The height used by the log law is derived from these in
:mod:`wind_data.hurricane.height`, according to the configured rule.

Metadata caveat: NDBC and CO-OPS publish *current* station metadata. If a
sensor was moved after the storm, the published height may not be the one in
use in October 2025; this is stated in the report.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from wind_data.hurricane import geodesy
from wind_data.hurricane.config import HurricaneConfig, station_key
from wind_data.hurricane.fetch import Fetcher, non_empty, text_starting_with, valid_json

log = logging.getLogger(__name__)

FT_TO_M = 0.3048

STATION_COLUMNS = (
    "source", "station_id", "key", "name", "lat", "lon", "station_class", "platform_type", "owner",
    "elevation_m", "elevation_ref", "sensor_height_m", "sensor_height_ref", "metadata_source",
    "metadata_note",
)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(STATION_COLUMNS))


# ---------------------------------------------------------------------------
# NDBC
# ---------------------------------------------------------------------------
_NDBC_LOC = re.compile(r"(-?\d+(?:\.\d+)?)\s*([NS])\s+(-?\d+(?:\.\d+)?)\s*([EW])")


def parse_ndbc_station_table(text: str) -> pd.DataFrame:
    """``station_table.txt`` -> id, owner, type, name, lat, lon."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("|")
        if len(f) < 7:
            continue
        m = _NDBC_LOC.search(html.unescape(f[6]))
        if not m:
            continue
        lat = float(m.group(1)) * (1 if m.group(2) == "N" else -1)
        lon = float(m.group(3)) * (1 if m.group(4) == "E" else -1)
        rows.append({"station_id": f[0].strip().upper(), "owner": f[1].strip(), "platform_type": f[2].strip(),
                     "name": html.unescape(f[4].strip()), "lat": lat, "lon": lon})
    return pd.DataFrame(rows)


def parse_ndbc_historical_index(text: str, years) -> set[str]:
    """Station ids with an annual stdmet file for every one of ``years``."""
    found: dict[str, set[int]] = {}
    for sid, year in re.findall(r"([0-9a-zA-Z]+)h(\d{4})\.txt\.gz", text):
        found.setdefault(sid.upper(), set()).add(int(year))
    need = set(int(y) for y in years)
    return {sid for sid, ys in found.items() if need <= ys}


def _page_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", "\n", raw_html)
    return "\n".join(line.strip() for line in html.unescape(text).splitlines() if line.strip())


def parse_ndbc_station_page(raw_html: str) -> dict[str, object]:
    """Site elevation and anemometer height from an NDBC station page.

    The page states them as e.g. ``Site elevation: sea level`` or
    ``Site elevation: 2.7 m above mean sea level`` and ``Anemometer height:
    4.1 m above site elevation``. Anything not stated is returned as NaN.
    """
    text = _page_text(raw_html)
    out: dict[str, object] = {"elevation_m": np.nan, "elevation_ref": "", "sensor_height_m": np.nan,
                              "sensor_height_ref": ""}
    m = re.search(r"Site elevation:\s*\n?\s*([^\n]+)", text)
    if m:
        value = m.group(1).strip()
        if value.lower().startswith("sea level"):
            out["elevation_m"], out["elevation_ref"] = 0.0, "MSL (NDBC: 'sea level')"
        else:
            mm = re.match(r"(-?\d+(?:\.\d+)?)\s*m\s+(.*)", value)
            if mm:
                out["elevation_m"], out["elevation_ref"] = float(mm.group(1)), mm.group(2).strip()
    m = re.search(r"Anemometer height:\s*\n?\s*(-?\d+(?:\.\d+)?)\s*m\s+([^\n]+)", text)
    if m:
        out["sensor_height_m"], out["sensor_height_ref"] = float(m.group(1)), m.group(2).strip()
    return out


def _unstated(meta: dict[str, object]) -> list[str]:
    """Height fields the provider did not state."""
    return [k for k in ("elevation_m", "sensor_height_m") if not np.isfinite(float(meta[k]))]  # type: ignore[arg-type]


def ndbc_class(platform_type: str) -> str:
    return "offshore" if "buoy" in platform_type.lower() else "coastal"


def ndbc_candidates(cfg: HurricaneConfig, fetcher: Fetcher, years) -> pd.DataFrame:
    s = cfg.source_settings.get("ndbc", {})
    raw = cfg.raw_dir / "stations" / "ndbc"
    table = fetcher.fetch(s["station_table_url"], raw / "station_table.txt", validate=text_starting_with("#"))
    index = fetcher.fetch(s["historical_index_url"], raw / "historical_stdmet_index.html", validate=non_empty)
    df = parse_ndbc_station_table(table.path.read_text(encoding="utf-8", errors="replace"))
    with_data = parse_ndbc_historical_index(index.path.read_text(encoding="utf-8", errors="replace"), years)
    # Requested stations are kept even without an annual file: the observation
    # stage then tries NDBC's monthly files and logs the outcome.
    requested = {k.split(":", 1)[1].upper() for k in cfg.include if k.startswith("ndbc:")}
    df = df[df["station_id"].isin(with_data | requested)].copy()
    df["annual_file"] = df["station_id"].isin(with_data)
    df["source"] = "ndbc"
    df["station_class"] = df["platform_type"].map(ndbc_class)
    df["metadata_source"] = s["station_table_url"]
    return df


def ndbc_metadata(cfg: HurricaneConfig, fetcher: Fetcher, station_id: str) -> dict[str, object]:
    s = cfg.source_settings.get("ndbc", {})
    url = s["station_page_url"].format(id=station_id.lower())
    dest = cfg.raw_dir / "stations" / "ndbc" / "pages" / f"{station_id.lower()}.html"
    try:
        res = fetcher.fetch(url, dest, validate=non_empty)
    except (FileNotFoundError, RuntimeError, ValueError) as err:
        return {"metadata_note": f"station page unavailable: {err}"}
    meta = parse_ndbc_station_page(res.path.read_text(encoding="utf-8", errors="replace"))
    meta["metadata_source"] = url
    missing = _unstated(meta)
    meta["metadata_note"] = ("current NDBC station page; " + (f"not stated: {missing}" if missing else "complete"))
    return meta


# ---------------------------------------------------------------------------
# CO-OPS
# ---------------------------------------------------------------------------
def coops_candidates(cfg: HurricaneConfig, fetcher: Fetcher) -> pd.DataFrame:
    s = cfg.source_settings.get("coops", {})
    res = fetcher.fetch(s["stations_url"], cfg.raw_dir / "stations" / "coops" / "stations_met.json",
                        validate=valid_json)
    data = json.loads(res.path.read_text(encoding="utf-8"))
    rows = [{"station_id": str(st["id"]), "name": st.get("name", ""), "lat": float(st["lat"]),
             "lon": float(st["lng"]), "owner": "NOAA CO-OPS", "platform_type": "CO-OPS met station"}
            for st in data.get("stations", []) if st.get("lat") is not None and st.get("lng") is not None]
    df = pd.DataFrame(rows)
    df["source"] = "coops"
    df["station_class"] = "coastal"
    df["metadata_source"] = s["stations_url"]
    return df


def parse_coops_sensors(data: dict) -> dict[str, object]:
    """Wind-sensor height and site elevation from ``sensors.json``.

    CO-OPS gives the wind sensor ``elevation`` relative to ``refdatum``
    (usually "Site Elevation") and the ``site`` pseudo-sensor relative to
    MSL. Units are in the top-level ``units`` field (feet by default).
    """
    factor = FT_TO_M if str(data.get("units", "feet")).lower().startswith("f") else 1.0
    out: dict[str, object] = {"elevation_m": np.nan, "elevation_ref": "", "sensor_height_m": np.nan,
                              "sensor_height_ref": ""}
    for s in data.get("sensors", []):
        elev = s.get("elevation")
        if elev is None:
            continue
        name = str(s.get("name", "")).lower()
        if s.get("sensorID") == "site" or name == "site":
            out["elevation_m"] = float(elev) * factor
            out["elevation_ref"] = str(s.get("refdatum", ""))
        elif name == "wind":
            out["sensor_height_m"] = float(elev) * factor
            ref = str(s.get("refdatum", ""))
            out["sensor_height_ref"] = f"above {ref.lower()}" if ref else ""
    return out


def coops_metadata(cfg: HurricaneConfig, fetcher: Fetcher, station_id: str) -> dict[str, object]:
    s = cfg.source_settings.get("coops", {})
    url = s["sensors_url"].format(id=station_id)
    dest = cfg.raw_dir / "stations" / "coops" / "sensors" / f"{station_id}.json"
    try:
        res = fetcher.fetch(url, dest, validate=valid_json)
    except (FileNotFoundError, RuntimeError, ValueError) as err:
        return {"metadata_note": f"sensors.json unavailable: {err}"}
    meta = parse_coops_sensors(json.loads(res.path.read_text(encoding="utf-8")))
    meta["metadata_source"] = url
    missing = _unstated(meta)
    meta["metadata_note"] = ("current CO-OPS sensor metadata; " + (f"not stated: {missing}" if missing else "complete"))
    return meta


# ---------------------------------------------------------------------------
# GHCNh
# ---------------------------------------------------------------------------
def parse_ghcnh_station_list(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    elev = pd.to_numeric(df["ELEVATION"], errors="coerce")
    return pd.DataFrame({
        "station_id": df["GHCN_ID"].str.strip(),
        "name": df["NAME"].str.strip(),
        "lat": pd.to_numeric(df["LATITUDE"], errors="coerce"),
        "lon": pd.to_numeric(df["LONGITUDE"], errors="coerce"),
        "elevation_m": elev.where(elev > -999),  # -999.9 = missing
        "owner": "NCEI GHCNh",
        "platform_type": np.where(df["ICAO"].str.strip() != "", "surface (ICAO " + df["ICAO"].str.strip() + ")",
                                  "surface"),
    }).dropna(subset=["lat", "lon"])


def ghcnh_candidates(cfg: HurricaneConfig, fetcher: Fetcher) -> pd.DataFrame:
    s = cfg.source_settings.get("ghcnh", {})
    res = fetcher.fetch(s["station_list_url"], cfg.raw_dir / "stations" / "ghcnh" / "ghcnh-station-list.csv",
                        validate=text_starting_with("GHCN_ID"))
    df = parse_ghcnh_station_list(res.path)
    df["source"] = "ghcnh"
    df["station_class"] = "land"
    df["elevation_ref"] = np.where(df["elevation_m"].notna(), "MSL (GHCNh station list)", "")
    df["sensor_height_m"] = np.nan
    df["sensor_height_ref"] = ""
    df["metadata_source"] = s["station_list_url"]
    df["metadata_note"] = "GHCNh publishes no anemometer height"
    return df


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def min_track_distance(dense_track: pd.DataFrame, lat: np.ndarray, lon: np.ndarray) -> pd.DataFrame:
    """Closest approach of the track to each station: distance (km) and time."""
    tl = dense_track["storm_lat"].to_numpy()
    tn = dense_track["storm_lon"].to_numpy()
    times = dense_track["time"].to_numpy()
    dist = np.full(len(lat), np.nan)
    when = np.full(len(lat), np.datetime64("NaT"), dtype="datetime64[ns]")
    for i, (a, b) in enumerate(zip(lat, lon)):
        d = geodesy.distance_km(tl, tn, a, b)
        if np.isfinite(d).any():
            j = int(np.nanargmin(d))
            dist[i], when[i] = d[j], times[j]
    return pd.DataFrame({"cpa_distance_km": dist, "cpa_time": when})


def select_stations(candidates: pd.DataFrame, dense_track: pd.DataFrame, cfg: HurricaneConfig,
                    domain) -> pd.DataFrame:
    """Stations within ``radius_km`` of the track, plus manual includes, minus excludes.

    Candidates outside the (generous) analysis domain are dropped before the
    geodesic search to keep it fast; manual includes bypass both filters.
    """
    if candidates.empty:
        return candidates.assign(key=[], cpa_distance_km=[], cpa_time=[], selected_by=[])
    c = candidates.copy()
    c["lon"] = (c["lon"] + 180.0) % 360.0 - 180.0
    c["key"] = [station_key(s, i) for s, i in zip(c["source"], c["station_id"])]
    inside = domain.contains(c["lat"], c["lon"]) | c["key"].isin(cfg.include)
    c = c[inside].reset_index(drop=True)
    c = pd.concat([c, min_track_distance(dense_track, c["lat"].to_numpy(), c["lon"].to_numpy())], axis=1)
    near = c["cpa_distance_km"] <= cfg.radius_km
    manual = c["key"].isin(cfg.include)
    keep = (near | manual) & ~c["key"].isin(cfg.exclude)
    c = c[keep].copy()
    c["selected_by"] = np.where(c["key"].isin(cfg.include), "manual include", f"within {cfg.radius_km:g} km")
    c["station_set"] = np.where(c["cpa_distance_km"] <= cfg.radius_km, f"within {cfg.radius_km:g} km of track",
                                f"beyond {cfg.radius_km:g} km (requested)")
    for key, cls in cfg.class_overrides.items():
        c.loc[c["key"] == key, "station_class"] = cls
    return c.sort_values("cpa_distance_km").reset_index(drop=True)


def mark_colocated(stations: pd.DataFrame, colocated_km: float, priority, has_wind=None) -> pd.DataFrame:
    """Flag stations from different networks that are the same instrument.

    Two stations count as one instrument only if they are within
    ``colocated_km`` of each other *and* both report wind (``has_wind``,
    a set of keys; all stations if None). Distance alone is not enough: a
    water-level gauge can sit metres from an unrelated met station.
    Only the highest-priority source of each group is used in metrics; the
    others get ``use_in_metrics = False`` and name the station they duplicate
    in ``colocated_with``. Nothing is deleted.
    """
    s = stations.reset_index(drop=True).copy()
    s["colocated_with"] = ""
    s["use_in_metrics"] = True
    rank = {src: i for i, src in enumerate(priority)}
    candidates = [i for i in range(len(s)) if has_wind is None or s.at[i, "key"] in has_wind]
    order = sorted(candidates, key=lambda i: rank.get(s.at[i, "source"], len(rank)))
    kept: list[int] = []
    for i in order:
        match = None
        for j in kept:
            if s.at[i, "source"] != s.at[j, "source"]:
                d = geodesy.distance_km(s.at[i, "lat"], s.at[i, "lon"], s.at[j, "lat"], s.at[j, "lon"])
                if float(d) <= colocated_km:
                    match = j
                    break
        if match is None:
            kept.append(i)
        else:
            s.at[i, "colocated_with"] = s.at[match, "key"]
            s.at[i, "use_in_metrics"] = False
    return s


def missing_requests(cfg: HurricaneConfig, selected: pd.DataFrame) -> list[str]:
    """Requested station keys that no network's station list contains."""
    return sorted(set(cfg.include) - set(selected["key"]))


def discover_stations(cfg: HurricaneConfig, fetcher: Fetcher, dense_track: pd.DataFrame, domain,
                      period: tuple[pd.Timestamp, pd.Timestamp]) -> pd.DataFrame:
    """Candidates from every configured network, selection, then metadata.

    Requested stations are not limited by the domain; the caller grows the
    domain to cover whatever was selected.
    """
    years = sorted({period[0].year, period[1].year})
    frames = []
    for source, get in (("ndbc", lambda: ndbc_candidates(cfg, fetcher, years)),
                        ("coops", lambda: coops_candidates(cfg, fetcher)),
                        ("ghcnh", lambda: ghcnh_candidates(cfg, fetcher))):
        if source not in cfg.obs_sources:
            continue
        try:
            frames.append(get())
        except Exception as err:  # one network being down should not stop the others
            log.error("%s station list unavailable: %s", source, err)
    candidates = pd.concat(frames, ignore_index=True) if frames else _empty()
    selected = select_stations(candidates, dense_track, cfg, domain)

    for i, row in selected.iterrows():
        if row["source"] == "ndbc":
            meta = ndbc_metadata(cfg, fetcher, row["station_id"])
        elif row["source"] == "coops":
            meta = coops_metadata(cfg, fetcher, row["station_id"])
        else:
            continue
        for k, v in meta.items():
            selected.at[i, k] = v
    for col in STATION_COLUMNS:
        if col not in selected:
            selected[col] = np.nan if col.endswith("_m") else ""
    # Co-location is decided once the data are in (pipeline 'adjust' stage).
    selected["colocated_with"] = ""
    selected["use_in_metrics"] = True
    return selected.reset_index(drop=True)
