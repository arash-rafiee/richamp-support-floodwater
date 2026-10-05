"""Compare two standardized wind datasets, typically GFS against GDAS.

Workflow
--------
1. :func:`pair` matches a *model* table to a *reference* table by valid time
   and location, giving one row per matched pair.
2. :func:`summarize` computes verification statistics for a paired table,
   optionally per group (``by="forecast_hour"``, ``by="location"``, ...).
3. :func:`compare` does both in one call and accepts gridded datasets.
4. :func:`difference_field` gives gridded model-minus-reference fields for
   maps.

Pairing rules
-------------
* Rows are matched on valid ``time`` plus location. If both tables have a
  ``location`` column (from :func:`~wind_data.processing.coordinates.extract_points`)
  location names are used, which also pairs grids of different resolution.
  Otherwise exact ``latitude``/``longitude`` are used, which requires both
  sources to be on the same grid.
* The model table may contain several runs (several ``forecast_hour`` values
  per valid time). Every run is paired with the same reference row, so a
  lead-time study is ``summarize(pairs, by="forecast_hour")``.
* Paired columns carry the suffixes ``_model`` and ``_ref``. Provenance
  (``init_time``, ``forecast_hour``, ``source``) of both sides is kept.

Direction statistics use only pairs where both speeds exceed
``min_speed_for_direction`` (default 0, i.e. only calm is excluded).
Direction errors at speeds below about 1-2 m/s are dominated by noise, so a
threshold is often appropriate; it is left to the caller so no data are
dropped silently.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd
import xarray as xr

from wind_data import schema
from wind_data.analysis import statistics as st
from wind_data.processing import coordinates
from wind_data.processing.wind import direction_difference

MODEL_SUFFIX = "_model"
REF_SUFFIX = "_ref"

_WIND_VARS = (schema.U10, schema.V10, schema.WIND_SPEED, schema.WIND_DIRECTION)
_PROVENANCE = (schema.INIT_TIME, schema.FORECAST_HOUR, schema.SOURCE)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------
def _join_keys(model: pd.DataFrame, reference: pd.DataFrame) -> list[str]:
    if coordinates.LOCATION_DIM in model and coordinates.LOCATION_DIM in reference:
        return [schema.TIME, coordinates.LOCATION_DIM]
    return [schema.TIME, schema.LATITUDE, schema.LONGITUDE]


def pair(model: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Match model rows to reference rows by valid time and location.

    Parameters
    ----------
    model, reference
        Tabular internal-format frames (see :mod:`wind_data.schema`).

    Returns
    -------
    pandas.DataFrame
        One row per matched pair with the join keys, grid coordinates, and
        ``<var>_model`` / ``<var>_ref`` columns for ``u10``, ``v10``,
        ``wind_speed``, ``wind_direction``, ``init_time``,
        ``forecast_hour`` and ``source``.

    Raises
    ------
    ValueError
        If no rows match.
    """
    schema.validate_wind_frame(model)
    schema.validate_wind_frame(reference)
    keys = _join_keys(model, reference)
    carried = [*_WIND_VARS, *_PROVENANCE]

    # Keep grid coordinates from the model side only when joining on location.
    extra = [c for c in (schema.LATITUDE, schema.LONGITUDE) if c not in keys]
    # Suffix every carried column explicitly, so names do not depend on whether
    # the other table happens to have the same column (pandas only suffixes clashes).
    left_cols = [c for c in carried if c in model]
    right_cols = [c for c in carried if c in reference]
    left = model[keys + extra + left_cols].rename(columns={c: c + MODEL_SUFFIX for c in left_cols})
    right = reference[keys + right_cols].rename(columns={c: c + REF_SUFFIX for c in right_cols})

    if right.duplicated(subset=keys).any():
        raise ValueError(
            f"reference has duplicate rows for keys {keys}; it must hold one value per time and location"
        )

    out = left.merge(right, on=keys, how="inner")
    if out.empty:
        raise ValueError(
            f"no matching rows on {keys}; check that valid times overlap and that both "
            "tables use the same grid or the same location names"
        )
    return out.sort_values(keys).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def _col(pairs: pd.DataFrame, var: str, side: str) -> np.ndarray:
    return pairs[var + side].to_numpy(dtype=float)


def pair_statistics(pairs: pd.DataFrame, *, min_speed_for_direction: float = 0.0) -> dict[str, float]:
    """All verification statistics for one set of pairs, as a flat dict."""
    spd_m, spd_r = _col(pairs, schema.WIND_SPEED, MODEL_SUFFIX), _col(pairs, schema.WIND_SPEED, REF_SUFFIX)
    out: dict[str, float] = {
        "n": st.count(spd_m, spd_r),
        "speed_mean_model": float(np.nanmean(spd_m)),
        "speed_mean_ref": float(np.nanmean(spd_r)),
        "speed_bias": st.bias(spd_m, spd_r),
        "speed_mae": st.mae(spd_m, spd_r),
        "speed_rmse": st.rmse(spd_m, spd_r),
        "speed_crmse": st.centered_rmse(spd_m, spd_r),
        "speed_corr": st.correlation(spd_m, spd_r),
        "speed_si": st.scatter_index(spd_m, spd_r),
    }
    for comp in (schema.U10, schema.V10):
        m, r = _col(pairs, comp, MODEL_SUFFIX), _col(pairs, comp, REF_SUFFIX)
        out[f"{comp}_bias"] = st.bias(m, r)
        out[f"{comp}_rmse"] = st.rmse(m, r)
        out[f"{comp}_corr"] = st.correlation(m, r)

    out["vector_rmse"] = st.vector_rmse(
        _col(pairs, schema.U10, MODEL_SUFFIX),
        _col(pairs, schema.V10, MODEL_SUFFIX),
        _col(pairs, schema.U10, REF_SUFFIX),
        _col(pairs, schema.V10, REF_SUFFIX),
    )

    windy = (spd_m > min_speed_for_direction) & (spd_r > min_speed_for_direction)
    dir_m = np.where(windy, _col(pairs, schema.WIND_DIRECTION, MODEL_SUFFIX), np.nan)
    dir_r = np.where(windy, _col(pairs, schema.WIND_DIRECTION, REF_SUFFIX), np.nan)
    out["direction_n"] = st.count(dir_m, dir_r)
    out["direction_bias"] = st.direction_bias(dir_m, dir_r)
    out["direction_mae"] = st.direction_mae(dir_m, dir_r)
    return out


def summarize(
    pairs: pd.DataFrame,
    by: str | Sequence[str] | None = None,
    *,
    min_speed_for_direction: float = 0.0,
) -> pd.DataFrame:
    """Verification statistics for paired data, overall or per group.

    Parameters
    ----------
    pairs
        Output of :func:`pair`.
    by
        Column(s) to group by. Useful choices: ``"forecast_hour_model"``
        (or its alias ``"forecast_hour"``) for error growth with lead time,
        ``"location"`` per site, ``"time"`` per valid time.
    min_speed_for_direction
        See module docstring.

    Returns
    -------
    pandas.DataFrame
        One row per group (a single row if ``by`` is None), one column per
        statistic.
    """
    if by is None:
        return pd.DataFrame([pair_statistics(pairs, min_speed_for_direction=min_speed_for_direction)])

    groups = [by] if isinstance(by, str) else list(by)
    groups = [_resolve_group_column(pairs, g) for g in groups]
    rows = []
    for key, grp in pairs.groupby(groups, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(groups, key))
        row.update(pair_statistics(grp, min_speed_for_direction=min_speed_for_direction))
        rows.append(row)
    return pd.DataFrame(rows)


def _resolve_group_column(pairs: pd.DataFrame, name: str) -> str:
    """Allow ``forecast_hour`` as shorthand for ``forecast_hour_model`` etc."""
    if name in pairs:
        return name
    if name + MODEL_SUFFIX in pairs:
        return name + MODEL_SUFFIX
    raise KeyError(f"cannot group by {name!r}; available columns: {list(pairs.columns)}")


# ---------------------------------------------------------------------------
# One-call interface
# ---------------------------------------------------------------------------
def _as_table(
    data: xr.Dataset | pd.DataFrame,
    lat: float | Sequence[float] | None,
    lon: float | Sequence[float] | None,
    names: str | Sequence[str] | None,
) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data
    if lat is not None and lon is not None:
        data = coordinates.extract_points(data, lat, lon, names=names)
    return coordinates.to_dataframe(data)


def compare(
    model: xr.Dataset | pd.DataFrame,
    reference: xr.Dataset | pd.DataFrame,
    *,
    lat: float | Sequence[float] | None = None,
    lon: float | Sequence[float] | None = None,
    names: str | Sequence[str] | None = None,
    by: str | Sequence[str] | None = None,
    min_speed_for_direction: float = 0.0,
) -> pd.DataFrame:
    """Pair ``model`` with ``reference`` and return summary statistics.

    Parameters
    ----------
    model, reference
        Gridded datasets from ``gfs.get_wind``/``gdas.get_wind`` or tabular
        frames. For datasets, pass ``lat``/``lon`` to compare at points;
        otherwise every grid point is used (subset the region first).
    lat, lon, names
        Optional locations, passed to
        :func:`~wind_data.processing.coordinates.extract_points`.
    by, min_speed_for_direction
        See :func:`summarize`.

    Examples
    --------
    >>> stats = compare(gfs_ds, gdas_ds, lat=41.5, lon=-71.4)                # doctest: +SKIP
    >>> lead = compare(gfs_runs_df, gdas_df, by="forecast_hour")             # doctest: +SKIP
    """
    pairs = pair(_as_table(model, lat, lon, names), _as_table(reference, lat, lon, names))
    return summarize(pairs, by=by, min_speed_for_direction=min_speed_for_direction)


# ---------------------------------------------------------------------------
# Gridded differences (for maps)
# ---------------------------------------------------------------------------
def difference_field(model: xr.Dataset, reference: xr.Dataset) -> xr.Dataset:
    """Gridded ``model - reference`` at the valid times both datasets share.

    Both datasets must be on the same grid (same resolution and subset).

    Returns
    -------
    xarray.Dataset
        ``u10_diff``, ``v10_diff``, ``wind_speed_diff`` (m/s),
        ``wind_direction_diff`` (degrees, shortest signed difference) and
        ``vector_error`` (magnitude of the u/v error, m/s).
    """
    for name, ds in (("model", model), ("reference", reference)):
        if schema.TIME in ds.dims and ds.indexes[schema.TIME].has_duplicates:
            raise ValueError(f"{name} has duplicate valid times; select a single run first")
    for c in (schema.LATITUDE, schema.LONGITUDE):
        if not np.array_equal(model[c].values, reference[c].values):
            raise ValueError(f"model and reference {c} grids differ; regrid or subset identically")

    m, r = xr.align(model, reference, join="inner", exclude=[schema.LATITUDE, schema.LONGITUDE])
    if schema.TIME in m.dims and m.sizes[schema.TIME] == 0:
        raise ValueError("model and reference share no valid times")

    du = m[schema.U10] - r[schema.U10]
    dv = m[schema.V10] - r[schema.V10]
    out = xr.Dataset(
        {
            "u10_diff": du,
            "v10_diff": dv,
            "wind_speed_diff": m[schema.WIND_SPEED] - r[schema.WIND_SPEED],
            "wind_direction_diff": direction_difference(
                m[schema.WIND_DIRECTION], r[schema.WIND_DIRECTION]
            ),
            "vector_error": np.hypot(du, dv),
        }
    )
    # Arithmetic drops coordinates that differ between the two sides; keep the model's provenance.
    keep = {c: m[c] for c in (schema.INIT_TIME, schema.FORECAST_HOUR) if c in m.coords}
    out = out.assign_coords(keep)
    out.attrs = {
        "model_source": model.attrs.get("source", "unknown"),
        "reference_source": reference.attrs.get("source", "unknown"),
        "definition": "model minus reference",
    }
    return out
