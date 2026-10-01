"""NOAA/NCEP GDAS: hourly timeline, download (NOMADS filter or AWS archive) and GRIB2 decoding.

Hourly scheme (see README): valid hour H comes from the GDAS cycle C = floor(H/6)*6,
forecast hour H - C (0..5). f000 is the GDAS analysis; f001-f005 are hourly
short forecasts from that same cycle.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

log = logging.getLogger("gdas")

NOMADS_FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gdas_0p25.pl"
AWS_BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
# NOMADS keeps about 10 days of GDAS; only try it for cycles younger than this.
NOMADS_MAX_AGE = dt.timedelta(days=9)
NATIVE_RES = 0.25
USER_AGENT = "richamp-support-floodwater/gdas (python-requests)"


@dataclass(frozen=True)
class Field:
    name: str        # key used in this package
    idx_key: str     # "VAR:level" as written in the NCEP .idx inventory
    discipline: int  # GRIB2 parameter identity
    category: int
    number: int
    level: int       # value of the first fixed surface (10 m, or 0 for MSL)
    surface: int     # GRIB2 type of first fixed surface (103 height above ground, 101 MSL)
    units: str       # units eccodes must report


FIELDS = (
    Field("u10", "UGRD:10 m above ground", 0, 2, 2, 10, 103, "m s**-1"),
    Field("v10", "VGRD:10 m above ground", 0, 2, 3, 10, 103, "m s**-1"),
    Field("mslp", "PRMSL:mean sea level", 0, 3, 1, 0, 101, "Pa"),
)


@dataclass(frozen=True)
class HourSource:
    valid: dt.datetime
    cycle: dt.datetime
    fhour: int

    @property
    def filename(self) -> str:
        return f"gdas.t{self.cycle:%H}z.pgrb2.0p25.f{self.fhour:03d}"

    @property
    def label(self) -> str:
        return f"{self.valid:%Y-%m-%d %H:%M} <- gdas.{self.cycle:%Y%m%d}/{self.cycle:%H} f{self.fhour:03d}"


def hourly_timeline(start: dt.datetime, end: dt.datetime) -> list:
    if start.minute or start.second or end.minute or end.second:
        raise ValueError("Start and end must be on whole hours")
    if end < start:
        raise ValueError("End is before start")
    hours, t = [], start
    while t <= end:
        cycle = t.replace(hour=t.hour // 6 * 6)
        hours.append(HourSource(t, cycle, t.hour - cycle.hour))
        t += dt.timedelta(hours=1)
    return hours


# --------------------------------------------------------------------------- download

class PermanentError(Exception):
    """The file does not exist at this source (no point retrying)."""


def check_grib_bytes(data: bytes, n_messages: int) -> None:
    """Walk GRIB2 section-0 lengths; rejects HTML/error pages and truncated files."""
    pos = 0
    count = 0
    while pos < len(data):
        if data[pos:pos + 4] != b"GRIB":
            head = data[pos:pos + 80]
            raise ValueError(f"not GRIB data at byte {pos}: {head!r}")
        if len(data) < pos + 16 or data[pos + 7] != 2:
            raise ValueError("not a GRIB edition 2 message")
        length = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        if pos + length > len(data) or data[pos + length - 4:pos + length] != b"7777":
            raise ValueError("truncated GRIB message")
        pos += length
        count += 1
    if count != n_messages:
        raise ValueError(f"expected {n_messages} GRIB messages, found {count}")


class Downloader:
    def __init__(self, cache_dir: Path, source: str, bounds: tuple, retries: int = 5,
                 timeout: float = 120.0, failed_log: Path | None = None, now: dt.datetime | None = None):
        self.cache_dir = Path(cache_dir)
        self.source = source
        self.bounds = bounds  # (west, south, east, north) incl. buffer, for the NOMADS filter
        self.retries = retries
        self.timeout = timeout
        self.failed_log = failed_log
        self.now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        self._local = threading.local()
        self._nomads_gate = threading.Semaphore(2)  # be polite to NOMADS
        self._log_lock = threading.Lock()
        self.used = {}  # HourSource -> "cache" | "nomads" | "aws"

    def cache_path(self, h: HourSource) -> Path:
        return self.cache_dir / f"gdas.{h.cycle:%Y%m%d}" / f"{h.cycle:%H}" / f"{h.filename}.u10v10prmsl.grib2"

    def sources_for(self, h: HourSource) -> list:
        if self.source != "auto":
            return [self.source]
        if self.now - h.cycle < NOMADS_MAX_AGE:
            return ["nomads", "aws"]
        return ["aws"]

    @property
    def session(self) -> requests.Session:
        if not hasattr(self._local, "s"):
            self._local.s = requests.Session()
            self._local.s.headers["User-Agent"] = USER_AGENT
        return self._local.s

    def _record_failure(self, url: str, reason: str):
        if self.failed_log:
            with self._log_lock, open(self.failed_log, "a") as f:
                f.write(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S}Z\t{url}\t{reason}\n")

    def _get(self, url: str, params=None, headers=None) -> bytes:
        last = ""
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = repr(e)
            else:
                if r.status_code in (200, 206):
                    return r.content
                if r.status_code in (403, 404):
                    self._record_failure(r.url, f"HTTP {r.status_code}")
                    raise PermanentError(f"HTTP {r.status_code}: {r.url}")
                last = f"HTTP {r.status_code}"
            wait = min(60, 2 ** attempt)
            log.warning("Attempt %d/%d failed for %s (%s); retrying in %ds",
                        attempt, self.retries, url, last, wait)
            time.sleep(wait)
        self._record_failure(url, last)
        raise RuntimeError(f"Giving up on {url}: {last}")

    def is_cached(self, h: HourSource) -> bool:
        p = self.cache_path(h)
        if not p.is_file():
            return False
        try:
            check_grib_bytes(p.read_bytes(), len(FIELDS))
            return True
        except ValueError as e:
            log.warning("Discarding invalid cached file %s (%s)", p, e)
            p.unlink()
            return False

    def fetch(self, h: HourSource) -> Path:
        """Download one hour (3 fields) unless a valid copy is already cached."""
        path = self.cache_path(h)
        if self.is_cached(h):
            self.used.setdefault(h, "cache")
            return path
        errors = []
        for src in self.sources_for(h):
            try:
                if src == "nomads":
                    with self._nomads_gate:
                        data = self._fetch_nomads(h)
                else:
                    data = self._fetch_aws(h)
                check_grib_bytes(data, len(FIELDS))
            except (PermanentError, ValueError) as e:
                errors.append(f"{src}: {e}")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)  # atomic: an interrupted run never leaves a half file
            log.info("Downloaded %s from %s (%.0f kB)", h.label, src, len(data) / 1024)
            self.used[h] = src
            return path
        raise RuntimeError("; ".join(errors))

    def _fetch_nomads(self, h: HourSource) -> bytes:
        west, south, east, north = self.bounds
        params = {
            "dir": f"/gdas.{h.cycle:%Y%m%d}/{h.cycle:%H}/atmos",
            "file": h.filename,
            "var_UGRD": "on", "var_VGRD": "on", "var_PRMSL": "on",
            "lev_10_m_above_ground": "on", "lev_mean_sea_level": "on",
            "subregion": "", "leftlon": west, "rightlon": east, "toplat": north, "bottomlat": south,
        }
        return self._get(NOMADS_FILTER, params=params)

    def _fetch_aws(self, h: HourSource) -> bytes:
        # GFS v16 (March 2021) added the "atmos/" level; try it first, then the older layout.
        last = None
        for sub in ("atmos/", ""):
            url = f"{AWS_BUCKET}/gdas.{h.cycle:%Y%m%d}/{h.cycle:%H}/{sub}{h.filename}"
            try:
                idx = self._get(url + ".idx").decode("ascii", "replace")
            except PermanentError as e:
                last = e
                continue
            ranges = self._byte_ranges(idx, h, url)
            parts = []
            for start, end in ranges:
                rng = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
                part = self._get(url, headers={"Range": rng})
                if end is not None and len(part) != end - start + 1:
                    raise ValueError(f"short read for {url} {rng}: {len(part)} bytes")
                parts.append(part)
            return b"".join(parts)
        raise PermanentError(str(last))

    @staticmethod
    def _byte_ranges(idx_text: str, h: HourSource, url: str) -> list:
        """Locate the three fields in the .idx inventory and check date/forecast labels."""
        lines = [ln for ln in idx_text.splitlines() if ln.strip()]
        if not lines or not lines[0][0].isdigit():
            raise ValueError(f"{url}.idx is not a GRIB inventory")
        entries = [ln.split(":") for ln in lines]
        want_fcst = {"anl", "0 hour fcst"} if h.fhour == 0 else {f"{h.fhour} hour fcst"}
        ranges = []
        for fld in FIELDS:
            hits = [i for i, e in enumerate(entries) if f"{e[3]}:{e[4]}" == fld.idx_key]
            if len(hits) != 1:
                raise ValueError(f"{fld.idx_key} found {len(hits)} times in {url}.idx")
            e = entries[hits[0]]
            if e[2] != f"d={h.cycle:%Y%m%d%H}" or e[5] not in want_fcst:
                raise ValueError(f"unexpected inventory entry in {url}.idx: {':'.join(e)}")
            start = int(e[1])
            end = int(entries[hits[0] + 1][1]) - 1 if hits[0] + 1 < len(entries) else None
            ranges.append((start, end))
        return ranges


# --------------------------------------------------------------------------- GRIB decoding

@dataclass
class NativeHour:
    lat: np.ndarray   # ascending
    lon: np.ndarray   # ascending, in [-180, 180)
    data: dict        # name -> (nlat, nlon) array; mslp in Pa


def grib_reader(choice: str = "auto") -> str:
    """Pick the GRIB2 decoder: eccodes if importable, otherwise GDAL through rasterio."""
    if choice != "auto":
        return choice
    try:
        import eccodes  # noqa: F401
        return "eccodes"
    except ImportError:
        pass
    try:
        import rasterio  # noqa: F401
        return "rasterio"
    except ImportError:
        raise RuntimeError("No GRIB2 reader found: install eccodes, or use an env with rasterio (GDAL)")


def read_hour(path: Path, h: HourSource, reader: str = "eccodes") -> NativeHour:
    """Decode the three fields, verify identity/time, and normalise orientation.

    Neither reader is used from several threads; call from one thread only.
    """
    found, lat, lon = (_read_eccodes if reader == "eccodes" else _read_rasterio)(Path(path), h)
    missing = [x.name for x in FIELDS if x.name not in found]
    if missing:
        raise ValueError(f"{path} lacks {missing}")
    lon = (lon + 180.0) % 360.0 - 180.0
    lat, lon = np.round(lat, 6), np.round(lon, 6)
    jorder, iorder = np.argsort(lat), np.argsort(lon)
    data = {k: v[jorder][:, iorder] for k, v in found.items()}
    return NativeHour(lat[jorder], lon[iorder], data)


def _check_identity(found: dict, fld, path: Path, ref, valid, h: HourSource):
    if fld is None:
        raise ValueError(f"unexpected GRIB field in {path}")
    if fld.name in found:
        raise ValueError(f"duplicate {fld.name} in {path}")
    if ref != h.cycle or valid != h.valid:
        raise ValueError(f"{fld.name} in {path} is {ref} valid {valid}, expected {h.cycle} valid {h.valid}")


def _read_eccodes(path: Path, h: HourSource):
    import eccodes

    found = {}
    grid_sig = None
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                g = lambda k: eccodes.codes_get(gid, k)
                ident = (g("discipline"), g("parameterCategory"), g("parameterNumber"), g("level"))
                fld = next((x for x in FIELDS
                            if (x.discipline, x.category, x.number, x.level) == ident), None)
                ref = dt.datetime.strptime(f"{g('dataDate')}{g('dataTime'):04d}", "%Y%m%d%H%M")
                valid = dt.datetime.strptime(f"{g('validityDate')}{g('validityTime'):04d}", "%Y%m%d%H%M")
                _check_identity(found, fld, path, ref, valid, h)
                if g("units") != fld.units:
                    raise ValueError(f"{fld.name} units {g('units')!r}, expected {fld.units!r}")
                if g("gridType") != "regular_ll" or g("iScansNegatively") or g("jPointsAreConsecutive"):
                    raise ValueError(f"unsupported grid layout in {path}")
                ni, nj = g("Ni"), g("Nj")
                dlon, dlat = g("iDirectionIncrementInDegrees"), g("jDirectionIncrementInDegrees")
                lon0, lat0 = g("longitudeOfFirstGridPointInDegrees"), g("latitudeOfFirstGridPointInDegrees")
                jpos = g("jScansPositively")
                sig = (ni, nj, dlon, dlat, lon0, lat0, jpos)
                if grid_sig is not None and sig != grid_sig:
                    raise ValueError(f"fields in {path} are on different grids")
                grid_sig = sig
                values = eccodes.codes_get_values(gid).reshape(nj, ni)
                if g("bitmapPresent"):
                    values = np.where(values == g("missingValue"), np.nan, values)
                found[fld.name] = values
            finally:
                eccodes.codes_release(gid)
    if grid_sig is None:
        return found, np.array([]), np.array([])
    ni, nj, dlon, dlat, lon0, lat0, jpos = grid_sig
    lat = lat0 + (1 if jpos else -1) * dlat * np.arange(nj)
    lon = lon0 + dlon * np.arange(ni)
    return found, lat, lon


def _epoch(tag: str) -> dt.datetime:
    seconds = int(tag.split()[0])
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).replace(tzinfo=None)


def _read_rasterio(path: Path, h: HourSource):
    """GDAL GRIB driver via rasterio.

    Fields are identified by their GRIB2 codes from the product definition template,
    because GDAL's parameter names/units depend on its version and data files. For
    these codes WMO fixes the units: 0/2/2 and 0/2/3 in m/s, 0/3/1 in Pa.
    """
    import rasterio

    found = {}
    with rasterio.open(path) as ds:
        if ds.driver != "GRIB":
            raise ValueError(f"{path} is not read as GRIB by GDAL ({ds.driver})")
        t = ds.transform
        if t.b != 0 or t.d != 0:
            raise ValueError(f"rotated grid in {path}")
        # GDAL reports pixel corners; GRIB values sit at the centres.
        lon = t.c + (np.arange(ds.width) + 0.5) * t.a
        lat = t.f + (np.arange(ds.height) + 0.5) * t.e
        for band in range(1, ds.count + 1):
            tags = ds.tags(band)
            if tags.get("GRIB_PDS_PDTN") != "0":
                raise ValueError(f"unexpected product template {tags.get('GRIB_PDS_PDTN')} in {path}")
            # Template 4.0, one number per octet: [0] category, [1] number, [8] time unit,
            # [9:13] forecast time, [13] first surface type, [14] scale, [15:19] scaled value.
            pds = [int(x) for x in tags["GRIB_PDS_TEMPLATE_NUMBERS"].split()]
            discipline = int(tags["GRIB_DISCIPLINE"].split("(")[0])
            level = int.from_bytes(bytes(pds[15:19]), "big") / 10 ** pds[14]
            fld = next((x for x in FIELDS
                        if (x.discipline, x.category, x.number, x.level, x.surface)
                        == (discipline, pds[0], pds[1], level, pds[13])), None)
            if pds[8] != 1 or int.from_bytes(bytes(pds[9:13]), "big") != h.fhour:
                raise ValueError(f"band {band} of {path} is not forecast hour {h.fhour}")
            _check_identity(found, fld, path, _epoch(tags["GRIB_REF_TIME"]), _epoch(tags["GRIB_VALID_TIME"]), h)
            found[fld.name] = ds.read(band, masked=True).astype(float).filled(np.nan)
    return found, lat, lon
