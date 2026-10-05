"""Quality control of observed winds.

Nothing is deleted. Every check appends a code to ``qc_flags`` and records
that fail a *blocking* check get ``qc_pass = False``; the evaluation uses
only passing records, and the flags stay in the cleaned table so every
exclusion can be traced.

Blocking flags
    ``speed_missing``      no sustained speed
    ``speed_range``        speed < 0 or > ``MAX_SPEED`` m/s
    ``direction_range``    direction outside [0, 360]
    ``provider_flag``      the provider marked the speed suspect/erroneous
    ``duplicate``          repeated (station, time); the first is kept
Informational flags
    ``calm``, ``direction_missing``, ``gust_missing``, ``gust_lt_speed``
    (a gust below the mean speed, usually a sampling-interval mismatch)

Provider flags
--------------
CO-OPS ``f = "X,R"``: X = maximum wind speed exceeded, R = rate-of-change
tolerance exceeded (either set -> ``provider_flag``).
GHCNh quality codes (NCEI GHCNh documentation v1.1.0, Table 3): any letter
code a-z is a failed integrated QC check; legacy codes 2 (suspect),
3 (erroneous) and 5 (removed) for sources 220-223, 347, 348, and 2, 3, 6, 7
for sources 313-346, are suspect or erroneous.
NDBC historical stdmet files carry no per-record flags; NDBC's own QC has
already removed failed data (the gaps show up as missing sentinels).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAX_SPEED = 90.0  # m/s; above any credible 1-10 min sustained surface wind

_GHCNH_BAD_LEGACY = {
    "A": {"2", "3", "5"},             # sources 220, 221, 222, 223, 347, 348
    "B": {"2", "3", "6", "7"},        # sources 313, 314, 315, 322, 335, 343, 344, 346
}
_GHCNH_GROUP_A = {"220", "221", "222", "223", "347", "348"}


def _ghcnh_bad(source_qc: str) -> bool:
    fields = dict(part.split("=", 1) for part in source_qc.split(",") if "=" in part)
    code = fields.get("speed_q", "").strip()
    if not code:
        return False
    if code.isalpha() and code.islower():
        return True
    group = "A" if fields.get("src", "").strip() in _GHCNH_GROUP_A else "B"
    return code in _GHCNH_BAD_LEGACY[group]


def _coops_bad(source_qc: str) -> bool:
    flags = source_qc.partition("=")[2].split(",")
    return any(f.strip() == "1" for f in flags)


def provider_flagged(obs: pd.DataFrame) -> np.ndarray:
    bad = np.zeros(len(obs), dtype=bool)
    src = obs["source"].to_numpy()
    qc = obs["source_qc"].fillna("").astype(str).to_numpy()
    for i in range(len(obs)):
        if src[i] == "coops":
            bad[i] = _coops_bad(qc[i])
        elif src[i] == "ghcnh":
            bad[i] = _ghcnh_bad(qc[i])
    return bad


def clean(obs: pd.DataFrame) -> pd.DataFrame:
    """Sort, flag and mark pass/fail. Returns a new frame; the input is untouched."""
    df = obs.sort_values(["source", "station_id", "time"], kind="stable").reset_index(drop=True)
    flags: list[list[str]] = [[] for _ in range(len(df))]
    blocking = np.zeros(len(df), dtype=bool)

    def add(mask: np.ndarray, code: str, block: bool) -> None:
        nonlocal blocking
        for i in np.flatnonzero(mask):
            flags[i].append(code)
        if block:
            blocking |= mask

    s = df["wind_speed"].to_numpy(dtype=float)
    d = df["wind_direction"].to_numpy(dtype=float)
    g = df["wind_gust"].to_numpy(dtype=float)
    calm = df["calm"].to_numpy(dtype=bool)

    add(np.isnan(s), "speed_missing", True)
    add(np.isfinite(s) & ((s < 0) | (s > MAX_SPEED)), "speed_range", True)
    add(np.isfinite(d) & ((d < 0) | (d > 360)), "direction_range", True)
    add(provider_flagged(df), "provider_flag", True)
    add(df.duplicated(["source", "station_id", "time"], keep="first").to_numpy(), "duplicate", True)
    add(calm, "calm", False)
    add(np.isnan(d) & ~calm, "direction_missing", False)
    add(np.isnan(g), "gust_missing", False)
    add(np.isfinite(g) & np.isfinite(s) & (g < s), "gust_lt_speed", False)

    df["qc_flags"] = [";".join(f) for f in flags]
    df["qc_pass"] = ~blocking
    # 360 and 0 are the same direction; keep [0, 360).
    df["wind_direction"] = np.where(np.isfinite(d) & (d == 360), 0.0, d)
    return df


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-station record counts and flag frequencies."""
    rows = []
    for (source, sid), g in df.groupby(["source", "station_id"], sort=True):
        flags = pd.Series([f for f in ";".join(g["qc_flags"]).split(";") if f], dtype=str).value_counts()
        rows.append({"source": source, "station_id": sid, "n_records": len(g), "n_pass": int(g["qc_pass"].sum()),
                     "first": g["time"].min(), "last": g["time"].max(),
                     **{f"n_{k}": int(v) for k, v in flags.items()}})
    return pd.DataFrame(rows)
