"""Target RICHAMP grid and bilinear regridding from the native GDAS grid."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Tolerance (degrees) for "target point lies inside the source grid" and for
# checking that the domain is an integer number of grid cells.
EPS = 1e-6


@dataclass(frozen=True)
class TargetGrid:
    """Regular lat/lon grid; row 0 is the southern edge, column 0 the western edge."""

    res: float
    west: float
    south: float
    east: float
    north: float

    @classmethod
    def from_domain(cls, res, x0, y0, x1, y1) -> "TargetGrid":
        """Build from MetGet-style ``resolution x0 y0 x1 y1`` (same rules as
        ``parse_domain_data`` in get_metget_data.py: corners are sorted and the
        resolution is made positive)."""
        res = abs(float(res))
        if res <= 0:
            raise ValueError("Domain resolution must be > 0")
        west, east = sorted((float(x0), float(x1)))
        south, north = sorted((float(y0), float(y1)))
        grid = cls(res, west, south, east, north)
        for name, span in (("longitude", east - west), ("latitude", north - south)):
            cells = span / res
            if abs(cells - round(cells)) > EPS:
                raise ValueError(f"Domain {name} span {span} is not a multiple of {res}")
        if not (-180.0 <= west and east <= 180.0 and -90.0 <= south and north <= 90.0):
            raise ValueError("Domain must lie within lon [-180, 180] and lat [-90, 90]")
        return grid

    @property
    def nx(self) -> int:
        return int(round((self.east - self.west) / self.res)) + 1

    @property
    def ny(self) -> int:
        return int(round((self.north - self.south) / self.res)) + 1

    @property
    def lons(self) -> np.ndarray:
        return self.west + self.res * np.arange(self.nx)

    @property
    def lats(self) -> np.ndarray:
        return self.south + self.res * np.arange(self.ny)


def _axis_weights(src: np.ndarray, dst: np.ndarray, name: str):
    if np.any(np.diff(src) <= 0):
        raise ValueError(f"Source {name} axis must be strictly increasing")
    if dst.min() < src[0] - EPS or dst.max() > src[-1] + EPS:
        raise ValueError(
            f"Target {name} range [{dst.min()}, {dst.max()}] is outside the source grid "
            f"[{src[0]}, {src[-1]}]; refusing to extrapolate"
        )
    i0 = np.clip(np.searchsorted(src, dst, side="right") - 1, 0, len(src) - 2)
    w = (dst - src[i0]) / (src[i0 + 1] - src[i0])
    return i0, np.clip(w, 0.0, 1.0)


class Bilinear:
    """Bilinear interpolation from a regular (lat, lon) source grid to a target grid.

    Weights are computed once and reused for every field/time with the same source grid.
    Both axes must be ascending; fields are indexed [lat, lon].
    """

    def __init__(self, src_lat, src_lon, dst_lat, dst_lon):
        self.src_shape = (len(src_lat), len(src_lon))
        self.j0, self.wy = _axis_weights(np.asarray(src_lat, float), np.asarray(dst_lat, float), "latitude")
        self.i0, self.wx = _axis_weights(np.asarray(src_lon, float), np.asarray(dst_lon, float), "longitude")

    def __call__(self, field: np.ndarray) -> np.ndarray:
        if field.shape != self.src_shape:
            raise ValueError(f"Field shape {field.shape} does not match source grid {self.src_shape}")
        j0, j1 = self.j0[:, None], self.j0[:, None] + 1
        i0, i1 = self.i0[None, :], self.i0[None, :] + 1
        wy, wx = self.wy[:, None], self.wx[None, :]
        return ((1 - wy) * ((1 - wx) * field[j0, i0] + wx * field[j0, i1])
                + wy * ((1 - wx) * field[j1, i0] + wx * field[j1, i1]))
