"""Load project settings from a TOML file.

Lookup order for the settings file:

1. the ``path`` argument (scripts pass ``--config``)
2. the ``WIND_DATA_CONFIG`` environment variable
3. ``config/settings.toml`` in the current working directory
4. built-in defaults, if no file is found

Relative paths inside the file are resolved against the project root, taken
to be the parent of the folder holding the file (``config/..``). The
``WIND_DATA_DIR`` environment variable overrides ``paths.data_dir``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ENV = "WIND_DATA_CONFIG"
DATA_DIR_ENV = "WIND_DATA_DIR"
DEFAULT_CONFIG_PATH = Path("config") / "settings.toml"


@dataclass(frozen=True)
class Location:
    """A named point for time series and statistics."""

    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Region:
    """A latitude-longitude box (see ``processing.coordinates.subset_region``)."""

    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def bounds(self) -> dict[str, float]:
        """Keyword arguments for ``subset_region``."""
        return {"lat_min": self.lat_min, "lat_max": self.lat_max,
                "lon_min": self.lon_min, "lon_max": self.lon_max}


@dataclass(frozen=True)
class Settings:
    """All settings used by the scripts."""

    data_dir: Path = Path("data")
    results_dir: Path = Path("results")
    resolution: str = "0p25"
    lead_hours: tuple[int, ...] = (0, 6, 12, 24)
    min_speed_for_direction: float = 1.0
    region: Region | None = None
    locations: tuple[Location, ...] = field(default_factory=tuple)
    source_file: Path | None = None

    @property
    def figures_dir(self) -> Path:
        return self.results_dir / "figures"

    @property
    def tables_dir(self) -> Path:
        return self.results_dir / "tables"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


def _resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p)


def settings_from_dict(raw: dict[str, Any], root: Path = Path(".")) -> Settings:
    """Build :class:`Settings` from parsed TOML, resolving paths against ``root``."""
    paths = raw.get("paths", {})
    download = raw.get("download", {})
    comparison = raw.get("comparison", {})
    defaults = Settings()

    data_dir = _resolve(root, paths.get("data_dir", str(defaults.data_dir)))
    if os.environ.get(DATA_DIR_ENV):
        data_dir = Path(os.environ[DATA_DIR_ENV]).expanduser()

    region = raw.get("region")
    return Settings(
        data_dir=data_dir,
        results_dir=_resolve(root, paths.get("results_dir", str(defaults.results_dir))),
        resolution=download.get("resolution", defaults.resolution),
        lead_hours=tuple(int(h) for h in comparison.get("lead_hours", defaults.lead_hours)),
        min_speed_for_direction=float(
            comparison.get("min_speed_for_direction", defaults.min_speed_for_direction)
        ),
        region=Region(**{"name": "region", **region}) if region else None,
        locations=tuple(Location(**loc) for loc in raw.get("locations", [])),
    )


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings; see the module docstring for the lookup order.

    Raises
    ------
    FileNotFoundError
        If ``path`` (or ``$WIND_DATA_CONFIG``) is given but does not exist.
    """
    explicit = path or os.environ.get(CONFIG_ENV)
    candidate = Path(explicit) if explicit else DEFAULT_CONFIG_PATH
    if not candidate.is_file():
        if explicit:
            raise FileNotFoundError(f"config file not found: {candidate}")
        return settings_from_dict({})

    with candidate.open("rb") as fh:
        raw = tomllib.load(fh)
    root = candidate.resolve().parent.parent
    settings = settings_from_dict(raw, root)
    return Settings(**{**settings.__dict__, "source_file": candidate.resolve()})
