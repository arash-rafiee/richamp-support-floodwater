"""NHC best track: retrieval, parsing, cross-checking and interpolation.

Sources
-------
HURDAT2 (primary)
    NHC's post-season, quality-controlled best-track database. Format:
    https://www.nhc.noaa.gov/data/hurdat/hurdat2-format-atl-1851-2021.pdf.
    Since 2021 each fix carries the radius of maximum wind (RMW, n mi).
ATCF b-deck (cross-check)
    The Automated Tropical Cyclone Forecast best track, one row per fix and
    wind-radii threshold. Used only to confirm the HURDAT2 record.

Both are parsed into the same table, one row per fix::

    time (UTC, naive)  record_id  status  lat  lon  vmax_kt  mslp_hpa  rmw_nmi

``record_id`` is HURDAT2's special-entry code: ``L`` landfall, ``I``
intensity peak, ``P`` pressure minimum, and so on; blank for synoptic fixes.

Interpolation
-------------
Positions are interpolated along the geodesic between neighbouring fixes, at
constant speed; intensity, pressure and RMW linearly in time; status is
carried forward from the most recent fix (a status change is only known to
have happened by the next fix). Times outside the track are left NaN, never
extrapolated.
"""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from wind_data.hurricane import geodesy
from wind_data.hurricane.fetch import Fetcher, non_empty, valid_gzip

log = logging.getLogger(__name__)

FIX_COLUMNS = ("time", "record_id", "status", "lat", "lon", "vmax_kt", "mslp_hpa", "rmw_nmi")
STATUS_NAMES = {
    "TD": "tropical depression", "TS": "tropical storm", "HU": "hurricane",
    "EX": "extratropical cyclone", "SD": "subtropical depression", "SS": "subtropical storm",
    "LO": "low", "WV": "tropical wave", "DB": "disturbance",
}
TROPICAL = frozenset({"TD", "TS", "HU"})


def _latlon(token: str) -> float:
    """``'14.0N'`` / ``'70.0W'`` (HURDAT2) or ``'140N'`` / ``'700W'`` (ATCF, tenths)."""
    token = token.strip()
    hemi, number = token[-1], token[:-1]
    value = float(number) if "." in number else float(number) / 10.0
    return -value if hemi in "SW" else value


def _missing(value: float, sentinel: float = -999) -> float:
    return np.nan if value <= sentinel else value


# ---------------------------------------------------------------------------
# HURDAT2
# ---------------------------------------------------------------------------
def parse_hurdat2(text: str, storm_id: str) -> tuple[str, pd.DataFrame]:
    """Return ``(name, fixes)`` for ``storm_id`` (e.g. ``'AL132025'``).

    Raises
    ------
    KeyError
        If the storm is not in the file.
    ValueError
        If the header's fix count does not match the lines that follow.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split(",")]
        if parts[0] != storm_id:
            continue
        name, n = parts[1], int(parts[2])
        rows = []
        for data in lines[i + 1:i + 1 + n]:
            f = [p.strip() for p in data.split(",")]
            if len(f) < 8 or not f[0].isdigit():
                raise ValueError(f"{storm_id}: malformed HURDAT2 line {data!r}")
            rows.append({
                "time": pd.Timestamp(f"{f[0]} {f[1][:2]}:{f[1][2:]}"),
                "record_id": f[2],
                "status": f[3],
                "lat": _latlon(f[4]),
                "lon": _latlon(f[5]),
                "vmax_kt": _missing(float(f[6])),
                "mslp_hpa": _missing(float(f[7])),
                "rmw_nmi": _missing(float(f[20])) if len(f) > 20 and f[20] else np.nan,
            })
        if len(rows) != n:
            raise ValueError(f"{storm_id}: header says {n} fixes, found {len(rows)}")
        return name, pd.DataFrame(rows, columns=list(FIX_COLUMNS))
    raise KeyError(f"{storm_id} not found in HURDAT2 file")


def storms_named(text: str, name: str) -> pd.DataFrame:
    """Every storm in a HURDAT2 file with this name, with its peak intensity.

    Used to show that the chosen identifier is the only plausible match.
    """
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1] == name.upper() and parts[0][:2].isalpha():
            _, fixes = parse_hurdat2(text, parts[0])
            out.append({
                "storm_id": parts[0], "name": parts[1], "n_fixes": len(fixes),
                "first_fix": fixes["time"].min(), "last_fix": fixes["time"].max(),
                "peak_vmax_kt": fixes["vmax_kt"].max(), "statuses": ",".join(sorted(fixes["status"].unique())),
            })
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# ATCF b-deck
# ---------------------------------------------------------------------------
def parse_atcf_bdeck(text: str) -> pd.DataFrame:
    """One row per fix (the 34-kt radii row, or the first row, per time)."""
    rows = []
    for line in text.splitlines():
        f = [p.strip() for p in line.split(",")]
        if len(f) < 11 or f[4] != "BEST":
            continue
        minute = int(f[3]) if f[3].isdigit() else 0
        rows.append({
            "time": pd.Timestamp(f"{f[2][:8]} {f[2][8:10]}:{minute:02d}"),
            "record_id": "",
            "status": f[10],
            "lat": _latlon(f[6]),
            "lon": _latlon(f[7]),
            "vmax_kt": _missing(float(f[8]), 0) if f[8] else np.nan,
            "mslp_hpa": _missing(float(f[9]), 0) if f[9] else np.nan,
            "rmw_nmi": _missing(float(f[19]), 0) if len(f) > 19 and f[19] else np.nan,
        })
    df = pd.DataFrame(rows, columns=list(FIX_COLUMNS))
    return df.drop_duplicates("time", keep="first").sort_values("time").reset_index(drop=True)


def crosscheck(primary: pd.DataFrame, other: pd.DataFrame, *, max_position_diff_deg: float,
               max_vmax_diff_kt: float, max_pressure_diff_hpa: float, **_) -> pd.DataFrame:
    """Compare two fix tables at common times. Returns only the disagreeing rows.

    Also reports fix times present in one source but not the other.
    """
    merged = primary.merge(other, on="time", how="outer", suffixes=("", "_other"), indicator=True)
    problems = []
    for d in merged.to_dict("records"):
        if d["_merge"] != "both":
            where = "HURDAT2 only" if d["_merge"] == "left_only" else "ATCF only"
            problems.append({"time": d["time"], "issue": f"fix in {where}"})
            continue
        checks = {
            "lat": (abs(d["lat"] - d["lat_other"]), max_position_diff_deg),
            "lon": (abs(d["lon"] - d["lon_other"]), max_position_diff_deg),
            "vmax_kt": (abs(d["vmax_kt"] - d["vmax_kt_other"]), max_vmax_diff_kt),
            "mslp_hpa": (abs(d["mslp_hpa"] - d["mslp_hpa_other"]), max_pressure_diff_hpa),
        }
        for key, (diff, tol) in checks.items():
            if np.isfinite(diff) and diff > tol + 1e-9:
                problems.append({"time": d["time"], "issue": f"{key} differs by {diff:g} (tolerance {tol:g})"})
        if d["status"] != d["status_other"]:
            problems.append({"time": d["time"], "issue": f"status {d['status']} vs {d['status_other']}"})
    return pd.DataFrame(problems, columns=["time", "issue"])


# ---------------------------------------------------------------------------
# Track object
# ---------------------------------------------------------------------------
@dataclass
class BestTrack:
    storm_id: str
    name: str
    fixes: pd.DataFrame
    source: str

    def __post_init__(self):
        self.fixes = self.fixes.sort_values("time").reset_index(drop=True)
        if self.fixes["time"].duplicated().any():
            raise ValueError(f"{self.storm_id}: duplicate fix times")

    @property
    def first_fix(self) -> pd.Timestamp:
        return pd.Timestamp(self.fixes["time"].iloc[0])

    @property
    def last_fix(self) -> pd.Timestamp:
        return pd.Timestamp(self.fixes["time"].iloc[-1])

    def lifecycle(self) -> dict[str, object]:
        """Milestones read directly from the fixes."""
        f = self.fixes
        tropical = f[f["status"].isin(TROPICAL)]
        hu = f[f["status"] == "HU"]
        peak = f.loc[f["vmax_kt"].idxmax()]
        pmin = f.loc[f["mslp_hpa"].idxmin()]
        post = f[(f["time"] > tropical["time"].max()) & ~f["status"].isin(TROPICAL)] if len(tropical) else f.iloc[0:0]
        return {
            "storm_id": self.storm_id,
            "name": self.name,
            "source": self.source,
            "first_fix": self.first_fix,
            "first_status": f["status"].iloc[0],
            "last_fix": self.last_fix,
            "last_status": f["status"].iloc[-1],
            "first_tropical_fix": tropical["time"].min() if len(tropical) else pd.NaT,
            "last_tropical_fix": tropical["time"].max() if len(tropical) else pd.NaT,
            "first_hurricane_fix": hu["time"].min() if len(hu) else pd.NaT,
            "post_tropical_from": post["time"].min() if len(post) else pd.NaT,
            "peak_vmax_kt": float(peak["vmax_kt"]),
            "peak_vmax_time": peak["time"],
            "min_mslp_hpa": float(pmin["mslp_hpa"]),
            "min_mslp_time": pmin["time"],
            "landfalls": [
                {"time": r.time, "lat": r.lat, "lon": r.lon, "vmax_kt": r.vmax_kt, "mslp_hpa": r.mslp_hpa}
                for r in f[f["record_id"] == "L"].itertuples()
            ],
        }

    def at(self, times) -> pd.DataFrame:
        """Storm centre, intensity and motion at arbitrary UTC times.

        Returns one row per input time with columns ``storm_lat``,
        ``storm_lon``, ``storm_vmax_kt``, ``storm_mslp_hpa``, ``storm_rmw_km``,
        ``storm_status``, ``storm_heading_deg``, ``storm_speed_ms`` and
        ``on_track`` (False outside the first..last fix: all NaN there).
        """
        t = pd.DatetimeIndex(pd.to_datetime(times))
        f = self.fixes
        ft = f["time"].to_numpy(dtype="datetime64[ns]")
        tt = t.to_numpy(dtype="datetime64[ns]")
        n = len(t)
        out = pd.DataFrame(index=range(n))
        out["time"] = t
        on = (tt >= ft[0]) & (tt <= ft[-1])
        out["on_track"] = on

        hi = np.clip(np.searchsorted(ft, tt, side="right"), 1, len(ft) - 1)
        lo = hi - 1
        exact = np.searchsorted(ft, tt, side="left")
        at_fix = (exact < len(ft)) & (ft[np.clip(exact, 0, len(ft) - 1)] == tt)
        span = (ft[hi] - ft[lo]).astype("timedelta64[s]").astype(float)
        frac = np.where(span > 0, (tt - ft[lo]).astype("timedelta64[s]").astype(float) / span, 0.0)
        frac = np.clip(frac, 0.0, 1.0)

        lat_lo, lon_lo = f["lat"].to_numpy()[lo], f["lon"].to_numpy()[lo]
        lat_hi, lon_hi = f["lat"].to_numpy()[hi], f["lon"].to_numpy()[hi]
        lat, lon = geodesy.interpolate_geodesic(lat_lo, lon_lo, lat_hi, lon_hi, frac)
        seg_km, heading = geodesy.inverse(lat_lo, lon_lo, lat_hi, lon_hi)

        def lin(col):
            a, b = f[col].to_numpy(dtype=float)[lo], f[col].to_numpy(dtype=float)[hi]
            return a + (b - a) * frac

        status = f["status"].to_numpy()
        # Carry the most recent status forward; at an exact fix use that fix.
        prev = np.where(at_fix, np.clip(exact, 0, len(ft) - 1), lo)
        out["storm_lat"] = np.where(on, lat, np.nan)
        out["storm_lon"] = np.where(on, lon, np.nan)
        out["storm_vmax_kt"] = np.where(on, lin("vmax_kt"), np.nan)
        out["storm_mslp_hpa"] = np.where(on, lin("mslp_hpa"), np.nan)
        out["storm_rmw_km"] = np.where(on, lin("rmw_nmi") * geodesy.NMI_TO_KM, np.nan)
        out["storm_status"] = np.where(on, status[prev], "")
        out["storm_heading_deg"] = np.where(on & (seg_km > 0), heading, np.nan)
        out["storm_speed_ms"] = np.where(on & (span > 0), seg_km * 1000.0 / np.where(span > 0, span, 1.0), np.nan)
        return out

    def dense(self, minutes: int = 10) -> pd.DataFrame:
        """The track at a fixed time step (for closest-approach searches)."""
        times = pd.date_range(self.first_fix, self.last_fix, freq=f"{minutes}min")
        return self.at(times)

    def bounds(self, margin_deg: float) -> dict[str, float]:
        f = self.fixes
        return {
            "lat_min": max(-90.0, float(f["lat"].min()) - margin_deg),
            "lat_max": min(90.0, float(f["lat"].max()) + margin_deg),
            "lon_min": max(-180.0, float(f["lon"].min()) - margin_deg),
            "lon_max": min(179.75, float(f["lon"].max()) + margin_deg),
        }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def _read_text(path: Path) -> str:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load_best_track(cfg, fetcher: Fetcher, *, overwrite: bool = False) -> tuple[BestTrack, dict[str, object]]:
    """Download (or reuse) HURDAT2 and ATCF, parse, cross-check and validate.

    Returns the HURDAT2-based :class:`BestTrack` and a provenance dict with
    the cross-check result and every Atlantic storm sharing the name.

    Raises
    ------
    RuntimeError
        If the sources disagree and ``crosscheck.on_conflict == 'stop'``, or
        the first/last fix differ from the times recorded in the config.
    """
    raw = cfg.raw_dir / "track"
    h_path = raw / Path(cfg.hurdat2_url).name
    fetcher.fetch(cfg.hurdat2_url, h_path, overwrite=overwrite, validate=non_empty)
    h_text = _read_text(h_path)
    name, fixes = parse_hurdat2(h_text, cfg.storm_id)
    track = BestTrack(cfg.storm_id, name, fixes, f"HURDAT2 ({h_path.name})")
    namesakes = storms_named(h_text, name)

    info: dict[str, object] = {"hurdat2_file": h_path.name, "namesakes": namesakes}
    if cfg.atcf_url:
        a_path = raw / Path(cfg.atcf_url).name
        fetcher.fetch(cfg.atcf_url, a_path, overwrite=overwrite,
                      validate=valid_gzip if a_path.suffix == ".gz" else non_empty)
        atcf = parse_atcf_bdeck(_read_text(a_path))
        issues = crosscheck(fixes, atcf, **cfg.crosscheck)
        info.update(atcf_file=a_path.name, atcf_fixes=len(atcf), crosscheck_issues=issues)
        if len(issues):
            msg = f"HURDAT2 and ATCF disagree for {cfg.storm_id}:\n{issues.to_string(index=False)}"
            if cfg.crosscheck.get("on_conflict", "stop") == "stop":
                raise RuntimeError(msg + "\nResolve this before continuing (see [track.crosscheck]).")
            log.warning(msg)

    for label, expected, actual in (("first", cfg.expected_first_fix, track.first_fix),
                                    ("last", cfg.expected_last_fix, track.last_fix)):
        if expected and pd.Timestamp(expected) != actual:
            raise RuntimeError(
                f"{cfg.storm_id}: {label} best-track fix is {actual}, config expects {expected}. "
                "The best track may have been revised; check it and update the config."
            )
    return track, info


def analysis_period(track: BestTrack, cfg) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First fix minus the buffer .. last fix plus the buffer (UTC).

    ``cfg.period_override`` (used by smoke tests) replaces this outright.
    """
    if cfg.period_override:
        return pd.Timestamp(cfg.period_override[0]), pd.Timestamp(cfg.period_override[1])
    return (track.first_fix - pd.Timedelta(hours=cfg.buffer_before_hours),
            track.last_fix + pd.Timedelta(hours=cfg.buffer_after_hours))
