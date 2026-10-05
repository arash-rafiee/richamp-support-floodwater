"""Paired verification statistics for wind.

Every function takes a ``model`` and a ``reference`` array of equal length
(e.g. GFS forecast and GDAS analysis) and ignores any pair in which either
value is NaN. Errors are defined as ``model - reference``, so a positive bias
means the model is too high.

Scalar statistics (speed, u, v)
    :func:`bias`, :func:`mae`, :func:`rmse`, :func:`centered_rmse`,
    :func:`correlation`, :func:`scatter_index`
Direction (circular) statistics
    :func:`direction_bias`, :func:`direction_mae` use the shortest signed
    angular difference, so 350 vs 10 degrees is an error of -20, not 340.
Vector statistics
    :func:`vector_rmse` combines u and v errors, which avoids the problems
    of direction at low speeds.

Only NumPy is used; no SciPy dependency is needed for these definitions.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike

from wind_data.processing.wind import direction_difference


def _paired(*arrays: ArrayLike) -> tuple[np.ndarray, ...]:
    """Float arrays with every position that is NaN in any input removed."""
    arrs = [np.asarray(a, dtype=float).ravel() for a in arrays]
    if len({a.size for a in arrs}) != 1:
        raise ValueError(f"inputs must have equal length, got {[a.size for a in arrs]}")
    ok = np.logical_and.reduce([np.isfinite(a) for a in arrs])
    return tuple(a[ok] for a in arrs)


def count(model: ArrayLike, reference: ArrayLike) -> int:
    """Number of valid (both non-NaN) pairs."""
    return int(_paired(model, reference)[0].size)


def bias(model: ArrayLike, reference: ArrayLike) -> float:
    """Mean error ``mean(model - reference)``."""
    m, r = _paired(model, reference)
    return float(np.mean(m - r)) if m.size else np.nan


def mae(model: ArrayLike, reference: ArrayLike) -> float:
    """Mean absolute error."""
    m, r = _paired(model, reference)
    return float(np.mean(np.abs(m - r))) if m.size else np.nan


def rmse(model: ArrayLike, reference: ArrayLike) -> float:
    """Root-mean-square error."""
    m, r = _paired(model, reference)
    return float(np.sqrt(np.mean((m - r) ** 2))) if m.size else np.nan


def centered_rmse(model: ArrayLike, reference: ArrayLike) -> float:
    """RMSE after removing the bias: ``sqrt(rmse**2 - bias**2)``.

    Separates random error from systematic error.
    """
    m, r = _paired(model, reference)
    if not m.size:
        return np.nan
    e = m - r
    return float(np.sqrt(np.mean((e - e.mean()) ** 2)))


def correlation(model: ArrayLike, reference: ArrayLike) -> float:
    """Pearson correlation coefficient.

    NaN if fewer than two pairs or if either series is constant.
    """
    m, r = _paired(model, reference)
    if m.size < 2:
        return np.nan
    dm, dr = m - m.mean(), r - r.mean()
    denom = np.sqrt(np.sum(dm**2) * np.sum(dr**2))
    return float(np.sum(dm * dr) / denom) if denom > 0 else np.nan


def scatter_index(model: ArrayLike, reference: ArrayLike) -> float:
    """``rmse / mean(reference)``, a dimensionless relative error.

    Only meaningful for positive quantities such as wind speed.
    """
    m, r = _paired(model, reference)
    if not m.size or np.mean(r) == 0:
        return np.nan
    return rmse(m, r) / float(np.mean(r))


def circular_mean(angles_deg: ArrayLike) -> float:
    """Mean direction of angles in degrees, in [0, 360). NaNs are ignored."""
    (a,) = _paired(angles_deg)
    if not a.size:
        return np.nan
    rad = np.radians(a)
    return float(np.degrees(np.arctan2(np.mean(np.sin(rad)), np.mean(np.cos(rad)))) % 360.0)


def direction_bias(model_deg: ArrayLike, reference_deg: ArrayLike) -> float:
    """Circular mean of the signed direction error, in [-180, 180).

    Positive means the model direction is rotated clockwise (veered)
    relative to the reference.
    """
    m, r = _paired(model_deg, reference_deg)
    if not m.size:
        return np.nan
    mean = circular_mean(direction_difference(m, r))
    return float(direction_difference(mean, 0.0))


def direction_mae(model_deg: ArrayLike, reference_deg: ArrayLike) -> float:
    """Mean absolute shortest angular difference, in [0, 180]."""
    m, r = _paired(model_deg, reference_deg)
    return float(np.mean(np.abs(direction_difference(m, r)))) if m.size else np.nan


def vector_rmse(
    u_model: ArrayLike, v_model: ArrayLike, u_reference: ArrayLike, v_reference: ArrayLike
) -> float:
    """RMS magnitude of the vector error ``sqrt(mean(du**2 + dv**2))``."""
    um, vm, ur, vr = _paired(u_model, v_model, u_reference, v_reference)
    if not um.size:
        return np.nan
    return float(np.sqrt(np.mean((um - ur) ** 2 + (vm - vr) ** 2)))
