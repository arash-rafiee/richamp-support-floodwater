"""Geodesic distances, bearings and storm-relative positions.

All distances are geodesics on the WGS84 ellipsoid (Karney's algorithm via
:class:`pyproj.Geod`), not a spherical or Cartesian approximation.

Conventions
-----------
* Bearings are azimuths in degrees clockwise from true north, in [0, 360).
* ``bearing_from_center`` is the azimuth *from the storm centre to the
  station*: 90 means the station is east of the centre.
* Earth-relative quadrants (NE, SE, SW, NW) follow the NHC wind-radii
  convention: NE is bearings [0, 90), SE [90, 180), and so on.
* Motion-relative quadrants use the storm heading (direction of travel):
  front-right is [0, 90) degrees clockwise from the heading, rear-right
  [90, 180), rear-left [180, 270), front-left [270, 360). In the Northern
  Hemisphere the strongest winds are usually front-right.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pyproj import Geod

GEOD = Geod(ellps="WGS84")


def _geod(method, *arrays):
    """Call ``GEOD.inv``/``GEOD.fwd`` on 1-d arrays, always returning arrays.

    pyproj routes single-element arrays through its scalar path, which trips a
    NumPy deprecation; plain floats avoid that.
    """
    if arrays[0].size == 1:
        return tuple(np.atleast_1d(np.asarray(r, dtype=float)) for r in method(*(float(a[0]) for a in arrays)))
    return tuple(np.asarray(r, dtype=float) for r in method(*arrays))


EARTH_QUADRANTS = ("NE", "SE", "SW", "NW")
MOTION_QUADRANTS = ("front-right", "rear-right", "rear-left", "front-left")
NMI_TO_KM = 1.852
KT_TO_MS = 0.514444


def inverse(lat1, lon1, lat2, lon2) -> tuple[np.ndarray, np.ndarray]:
    """Geodesic ``(distance_km, azimuth_deg)`` from point 1 to point 2.

    Inputs broadcast. NaN inputs give NaN outputs.
    """
    arrays = np.broadcast_arrays(*(np.asarray(a, dtype=float) for a in (lat1, lon1, lat2, lon2)))
    shape = arrays[0].shape
    lat1, lon1, lat2, lon2 = (a.ravel() for a in arrays)  # pyproj wants 1-d arrays, not 0-d
    dist = np.full(lat1.shape, np.nan)
    az = np.full(lat1.shape, np.nan)
    ok = np.isfinite(lat1) & np.isfinite(lon1) & np.isfinite(lat2) & np.isfinite(lon2)
    if ok.any():
        fwd, _, meters = _geod(GEOD.inv, lon1[ok], lat1[ok], lon2[ok], lat2[ok])
        dist[ok] = np.asarray(meters) / 1000.0
        az[ok] = np.mod(np.asarray(fwd), 360.0)
    return dist.reshape(shape), az.reshape(shape)


def distance_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    return inverse(lat1, lon1, lat2, lon2)[0]


def interpolate_geodesic(lat1, lon1, lat2, lon2, fraction) -> tuple[np.ndarray, np.ndarray]:
    """Point a ``fraction`` (0..1) of the way along the geodesic from 1 to 2."""
    arrays = np.broadcast_arrays(*(np.asarray(a, dtype=float) for a in (lat1, lon1, lat2, lon2, fraction)))
    shape = arrays[0].shape
    lat1, lon1, lat2, lon2, fraction = (a.ravel() for a in arrays)
    fwd, _, meters = _geod(GEOD.inv, lon1, lat1, lon2, lat2)
    lon, lat, _ = _geod(GEOD.fwd, lon1, lat1, fwd, meters * fraction)
    lat = np.asarray(lat, dtype=float).reshape(shape)
    return lat, ((np.asarray(lon, dtype=float) + 180.0) % 360.0 - 180.0).reshape(shape)


def earth_quadrant(bearing_deg) -> np.ndarray:
    """NE/SE/SW/NW from the azimuth centre -> station; '' where NaN."""
    b = np.asarray(bearing_deg, dtype=float)
    out = np.full(b.shape, "", dtype=object)
    ok = np.isfinite(b)
    out[ok] = np.array(EARTH_QUADRANTS, dtype=object)[(np.mod(b[ok], 360.0) // 90).astype(int)]
    return out


def motion_quadrant(bearing_deg, heading_deg) -> np.ndarray:
    """Motion-relative quadrant; '' where either angle is NaN (e.g. stationary storm)."""
    b = np.asarray(bearing_deg, dtype=float)
    h = np.asarray(heading_deg, dtype=float)
    rel = np.mod(b - h, 360.0)
    out = np.full(rel.shape, "", dtype=object)
    ok = np.isfinite(rel)
    out[ok] = np.array(MOTION_QUADRANTS, dtype=object)[(rel[ok] // 90).astype(int)]
    return out


def radial_band(distance, edges) -> np.ndarray:
    """Label such as '50-100 km' for each distance; '' outside the edges or NaN.

    Bands are closed on the left and open on the right, except the last,
    which includes its upper edge.
    """
    d = np.asarray(distance, dtype=float)
    edges = np.asarray(edges, dtype=float)
    labels = np.array([f"{edges[i]:g}-{edges[i + 1]:g} km" for i in range(len(edges) - 1)], dtype=object)
    idx = np.searchsorted(edges, d, side="right") - 1
    idx = np.where(d == edges[-1], len(edges) - 2, idx)
    out = np.full(d.shape, "", dtype=object)
    ok = np.isfinite(d) & (idx >= 0) & (idx < len(labels))
    out[ok] = labels[idx[ok]]
    return out


def band_labels(edges) -> list[str]:
    return [f"{edges[i]:g}-{edges[i + 1]:g} km" for i in range(len(edges) - 1)]


def closest_approach(track: pd.DataFrame, lat: float, lon: float) -> dict[str, object]:
    """Closest approach of a (densely interpolated) track to one point.

    ``track`` needs ``time``, ``storm_lat``, ``storm_lon``. The track should
    already be at fine time resolution; this picks the nearest sample.
    """
    d = distance_km(track["storm_lat"].to_numpy(), track["storm_lon"].to_numpy(), lat, lon)
    if not np.isfinite(d).any():
        return {"cpa_time": pd.NaT, "cpa_distance_km": np.nan}
    i = int(np.nanargmin(d))
    return {"cpa_time": pd.Timestamp(track["time"].iloc[i]), "cpa_distance_km": float(d[i])}


def cpa_phase(dt_hours, window_hours: float) -> np.ndarray:
    """'before' / 'during' / 'after' closest approach; '' where NaN."""
    dt = np.asarray(dt_hours, dtype=float)
    out = np.full(dt.shape, "", dtype=object)
    out[dt < -window_hours] = "before"
    out[np.abs(dt) <= window_hours] = "during"
    out[dt > window_hours] = "after"
    return out
