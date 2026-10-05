"""Load the hurricane-evaluation configuration (``config/melissa.toml``).

Relative paths are resolved against the project root, the parent of the
folder holding the file. ``WIND_DATA_DIR`` overrides ``paths.data_dir``, as in
:mod:`wind_data.config`.

Station keys used in overrides are ``"source:id"``, for example
``"ndbc:42058"``, so the same identifier from two networks cannot collide.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wind_data.config import DATA_DIR_ENV

DEFAULT_PATH = Path("config") / "melissa.toml"
STATION_CLASSES = ("offshore", "coastal", "land")
OBS_SOURCES = ("ndbc", "coops", "ghcnh")


def station_key(source: str, station_id: str) -> str:
    """``"ndbc:42058"``; the key used in every per-station override."""
    return f"{source}:{station_id}"


@dataclass(frozen=True)
class Domain:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def expanded_to(self, lat, lon, margin: float) -> "Domain":
        """This domain grown to include every (lat, lon) plus ``margin`` degrees."""
        lat, lon = list(lat), list(lon)
        if not lat:
            return self
        return Domain(
            lat_min=max(-90.0, min(self.lat_min, min(lat) - margin)),
            lat_max=min(90.0, max(self.lat_max, max(lat) + margin)),
            lon_min=max(-180.0, min(self.lon_min, min(lon) - margin)),
            lon_max=min(179.75, max(self.lon_max, max(lon) + margin)),
        )

    def as_dict(self) -> dict[str, float]:
        return {"lat_min": self.lat_min, "lat_max": self.lat_max, "lon_min": self.lon_min, "lon_max": self.lon_max}

    def contains(self, lat, lon):
        return (lat >= self.lat_min) & (lat <= self.lat_max) & (lon >= self.lon_min) & (lon <= self.lon_max)


@dataclass(frozen=True)
class HurricaneConfig:
    """Everything the pipeline reads from the TOML file."""

    root: Path
    data_dir: Path
    results_dir: Path
    source_file: Path | None

    storm_id: str
    storm_name: str
    basin: str
    expected_first_fix: str | None
    expected_last_fix: str | None

    hurdat2_url: str
    atcf_url: str
    cpa_resolution_minutes: int
    crosscheck: dict[str, Any]

    buffer_before_hours: float
    buffer_after_hours: float
    domain_margin_deg: float
    station_margin_deg: float
    domain_bounds: dict[str, float] | None

    radius_km: float
    obs_sources: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    colocated_km: float
    source_priority: tuple[str, ...]
    class_overrides: dict[str, str]
    source_settings: dict[str, dict[str, Any]]
    download: dict[str, float]

    target_height_m: float
    coastal_height_rule: str
    z0_m: dict[str, float]
    z0_overrides: dict[str, float]
    sensor_height_overrides: dict[str, float]

    resolution: str
    cycles: tuple[int, ...]
    gfs_lead_windows: tuple[int, ...]
    gdas_lead_windows: tuple[int, ...]
    valid_step_hours: int
    model_fields: tuple[tuple[str, str], ...]
    composite_min_lead: int
    download_workers: int
    model_min_request_interval_s: float

    time_tolerance_minutes: float
    interpolation: str
    min_speed_for_direction: float

    radial_bands_km: tuple[float, ...]
    cpa_window_hours: float
    min_samples: int
    speed_quantiles: tuple[float, ...] = field(default=(0.1, 0.5, 0.9, 0.99))

    # ---- run scoping (the CLI's --smoke sets these) -----------------------
    run_name: str = "full"
    """Sub-folder for processed data and results, so a smoke test never
    overwrites a full run. Raw downloads are shared (they are a cache)."""
    period_override: tuple[str, str] | None = None
    """Restrict the analysis period (UTC) instead of track +/- buffers."""
    max_stations_per_source: int | None = None
    """Keep only the N stations nearest the track from each network."""
    obs_average_minutes: int = 0
    """Average observations over this window centred on each model valid
    time before matching; 0 matches the single nearest sample."""
    obs_average_min_coverage: float = 0.5
    """Fraction of the window's expected samples needed for a mean."""
    map_highlight: tuple[str, ...] = ()
    """Stations ("source:id") ringed and labelled in bold on the station map."""
    map_region_name: str | None = None
    """Name for the station map's detail panel, e.g. "Narragansett Bay"."""
    output_group: str | None = None
    """Folder for processed data and results instead of the storm id, for a
    run whose period is not about the storm (e.g. ``periods``). Raw
    downloads stay under the storm id: they are a shared cache."""

    @property
    def _group(self) -> str:
        return self.output_group or self.storm_id.lower()

    # ---- derived paths --------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        """Unmodified downloads, one sub-folder per source."""
        return self.data_dir / "raw" / "hurricane" / self.storm_id.lower()

    @property
    def processed_dir(self) -> Path:
        """Cleaned tables and cropped model fields."""
        return self.data_dir / "processed" / "hurricane" / self._group / self.run_name

    @property
    def model_raw_dir(self) -> Path:
        """GRIB2 subsets; shared with wind_data's own GFS/GDAS cache layout."""
        return self.data_dir

    @property
    def manifest_path(self) -> Path:
        return self.raw_dir / "manifest.jsonl"

    @property
    def run_dir(self) -> Path:
        return self.results_dir / self._group / self.run_name

    @property
    def figures_dir(self) -> Path:
        return self.run_dir / "figures"

    @property
    def tables_dir(self) -> Path:
        return self.run_dir / "tables"

    @property
    def report_path(self) -> Path:
        return self.run_dir / "report.md"

    def z0_for(self, source: str, station_id: str, station_class: str) -> float:
        return float(self.z0_overrides.get(station_key(source, station_id), self.z0_m[station_class]))


def _resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else root / p


def _parse_field(spec: str) -> tuple[str, str]:
    variable, sep, level = spec.partition(":")
    if not sep or not variable or not level:
        raise ValueError(f"model field {spec!r} must look like 'UGRD:10 m above ground'")
    return variable.strip(), level.strip()


def config_from_dict(raw: dict[str, Any], root: Path = Path("."), source_file: Path | None = None) -> HurricaneConfig:
    """Build and validate a :class:`HurricaneConfig` from parsed TOML."""
    paths = raw.get("paths", {})
    storm = raw["storm"]
    track = raw.get("track", {})
    period = raw.get("period", {})
    domain = raw.get("domain", {})
    stations = raw.get("stations", {})
    height = raw.get("height", {})
    models = raw.get("models", {})
    colloc = raw.get("collocation", {})
    rel = raw.get("storm_relative", {})
    evaluation = raw.get("evaluation", {})

    data_dir = _resolve(root, paths.get("data_dir", "data"))
    if os.environ.get(DATA_DIR_ENV):
        data_dir = Path(os.environ[DATA_DIR_ENV]).expanduser()

    bound_keys = ("lat_min", "lat_max", "lon_min", "lon_max")
    given = [k for k in bound_keys if k in domain]
    if given and len(given) != 4:
        raise ValueError(f"[domain] needs all of {bound_keys} or none, got {given}")

    z0 = {k: float(v) for k, v in height.get("z0_m", {}).items()}
    missing = [c for c in STATION_CLASSES if c not in z0]
    if missing:
        raise ValueError(f"[height.z0_m] is missing {missing}")
    if any(v <= 0 for v in z0.values()):
        raise ValueError("[height.z0_m] roughness lengths must be positive")

    class_overrides = dict(stations.get("class_overrides", {}))
    bad = {k: v for k, v in class_overrides.items() if v not in STATION_CLASSES}
    if bad:
        raise ValueError(f"[stations.class_overrides] values must be one of {STATION_CLASSES}: {bad}")

    obs_sources = tuple(stations.get("sources", OBS_SOURCES))
    unknown = set(obs_sources) - set(OBS_SOURCES)
    if unknown:
        raise ValueError(f"unknown observation sources {sorted(unknown)}; known: {OBS_SOURCES}")

    rule = height.get("coastal_height_rule", "site_plus_sensor")
    if rule not in ("site_plus_sensor", "sensor_only"):
        raise ValueError(f"coastal_height_rule must be 'site_plus_sensor' or 'sensor_only', got {rule!r}")

    interpolation = colloc.get("interpolation", "bilinear")
    if interpolation not in ("bilinear", "nearest"):
        raise ValueError(f"interpolation must be 'bilinear' or 'nearest', got {interpolation!r}")

    bands = tuple(float(b) for b in rel.get("radial_bands_km", (0, 50, 100, 200, 500)))
    if list(bands) != sorted(bands) or len(bands) < 2:
        raise ValueError("radial_bands_km must be at least two increasing edges")

    cycles = tuple(int(c) for c in models.get("cycles", (0, 6, 12, 18)))
    if any(c not in (0, 6, 12, 18) for c in cycles):
        raise ValueError(f"cycles must be among 0, 6, 12, 18, got {cycles}")

    valid_step = int(models.get("valid_step_hours", 6))
    if valid_step not in (1, 2, 3, 6):
        raise ValueError(f"valid_step_hours must divide 6 (1, 2, 3 or 6), got {valid_step}")

    average = int(colloc.get("obs_average_minutes", 0))
    if average < 0:
        raise ValueError(f"obs_average_minutes must be >= 0, got {average}")
    coverage = float(colloc.get("obs_average_min_coverage", 0.5))
    if not 0 < coverage <= 1:
        raise ValueError(f"obs_average_min_coverage must be in (0, 1], got {coverage}")

    dl = raw.get("download", {})
    return HurricaneConfig(
        root=root,
        data_dir=data_dir,
        results_dir=_resolve(root, paths.get("results_dir", "outputs/hurricane")),
        source_file=source_file,
        storm_id=str(storm["id"]).upper(),
        storm_name=str(storm.get("name", "")).upper(),
        basin=str(storm.get("basin", str(storm["id"])[:2])).upper(),
        expected_first_fix=storm.get("expected_first_fix"),
        expected_last_fix=storm.get("expected_last_fix"),
        hurdat2_url=track["hurdat2_url"],
        atcf_url=track.get("atcf_url", ""),
        cpa_resolution_minutes=int(track.get("cpa_resolution_minutes", 10)),
        crosscheck={
            "max_position_diff_deg": 0.11,
            "max_vmax_diff_kt": 0,
            "max_pressure_diff_hpa": 0,
            "on_conflict": "stop",
            **track.get("crosscheck", {}),
        },
        buffer_before_hours=float(period.get("buffer_before_hours", 24)),
        buffer_after_hours=float(period.get("buffer_after_hours", 24)),
        domain_margin_deg=float(domain.get("margin_deg", 7.0)),
        station_margin_deg=float(domain.get("station_margin_deg", 1.0)),
        domain_bounds={k: float(domain[k]) for k in bound_keys} if given else None,
        radius_km=float(stations.get("radius_km", 500.0)),
        obs_sources=obs_sources,
        include=tuple(stations.get("include", ())),
        exclude=tuple(stations.get("exclude", ())),
        colocated_km=float(stations.get("colocated_km", 1.0)),
        source_priority=tuple(stations.get("source_priority", OBS_SOURCES)),
        class_overrides=class_overrides,
        source_settings={k: dict(v) for k, v in raw.get("sources", {}).items()},
        download={
            "timeout_connect_s": float(dl.get("timeout_connect_s", 10.0)),
            "timeout_read_s": float(dl.get("timeout_read_s", 180.0)),
            "max_attempts": int(dl.get("max_attempts", 6)),
            "backoff_s": float(dl.get("backoff_s", 2.0)),
            "min_request_interval_s": float(dl.get("min_request_interval_s", 0.25)),
        },
        target_height_m=float(height.get("target_m", 10.0)),
        coastal_height_rule=rule,
        z0_m=z0,
        z0_overrides={k: float(v) for k, v in height.get("z0_overrides", {}).items()},
        sensor_height_overrides={k: float(v) for k, v in height.get("sensor_height_overrides", {}).items()},
        resolution=str(models.get("resolution", "0p25")),
        cycles=cycles,
        # gfs_leads/gdas_leads are the older names (synoptic-only design).
        gfs_lead_windows=tuple(int(h) for h in models.get("gfs_lead_windows", models.get("gfs_leads", (0, 6, 12, 24)))),
        gdas_lead_windows=tuple(int(h) for h in models.get("gdas_lead_windows", models.get("gdas_leads", (0,)))),
        valid_step_hours=valid_step,
        model_fields=tuple(_parse_field(f) for f in models.get(
            "fields", ("UGRD:10 m above ground", "VGRD:10 m above ground", "PRMSL:mean sea level"))),
        composite_min_lead=int(models.get("composite_min_lead", 6)),
        download_workers=max(1, int(models.get("download_workers", 4))),
        model_min_request_interval_s=float(models.get("min_request_interval_s", 0.0)),
        time_tolerance_minutes=float(colloc.get("time_tolerance_minutes", 30)),
        interpolation=interpolation,
        min_speed_for_direction=float(colloc.get("min_speed_for_direction", 1.0)),
        radial_bands_km=bands,
        cpa_window_hours=float(rel.get("cpa_window_hours", 3.0)),
        min_samples=int(evaluation.get("min_samples", 10)),
        speed_quantiles=tuple(float(q) for q in evaluation.get("speed_quantiles", (0.1, 0.5, 0.9, 0.99))),
        obs_average_minutes=average,
        obs_average_min_coverage=coverage,
    )


def load_config(path: str | Path | None = None) -> HurricaneConfig:
    """Read the TOML file (default ``config/melissa.toml``)."""
    candidate = Path(path) if path else DEFAULT_PATH
    if not candidate.is_file():
        raise FileNotFoundError(f"hurricane config not found: {candidate}")
    with candidate.open("rb") as fh:
        raw = tomllib.load(fh)
    resolved = candidate.resolve()
    return config_from_dict(raw, resolved.parent.parent, resolved)
