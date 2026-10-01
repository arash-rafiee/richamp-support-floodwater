"""Oceanweather (OWI) ASCII .wnd/.pre writer and validator.

The format is the one produced by MetGet ``--format owi-ascii`` and read by
OceanweatherTo306.py, owi2wind.py and scale_and_subset.py:

    Oceanweather WIN/PRE Format                            2022122200     2022122700
    iLat= 441iLong=1021DX=0.1000DY=0.1000SWLat= 3.00000SWLon=-98.0000DT=202212220000
     1009.7626 1009.7690 ...                      (8 values per line, F10.4)

* Values start at the south-west corner; longitude varies fastest, rows go south -> north.
* .wnd: all U values of a time slice, then all V values, each starting on a new line (m/s).
* .pre: mean sea level pressure in hPa (OceanweatherTo306.py multiplies by 100).
* Header fields sit at fixed columns (owi2wind.py / scale_and_subset.py slice them).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from grid import TargetGrid

TITLE = "Oceanweather WIN/PRE Format"
VALUES_PER_LINE = 8
WIDTH = 10
VALUE_FMT = "%10.4f"
LINE_FMT = VALUE_FMT * VALUES_PER_LINE + "\n"
HEADER_FMT = "iLat=%4diLong=%4dDX=%6.4fDY=%6.4fSWLat=%8.5fSWLon=%8.4fDT=%s\n"
HEADER_LEN = 80
TITLE_RE = re.compile(r"^Oceanweather WIN/PRE Format {28}(\d{10}) {5}(\d{10})$")

# Fixed header columns used by owi2wind.py (OwiAscii) and scale_and_subset.py.
HEADER_COLUMNS = {
    "iLat": (5, 9), "iLong": (15, 19), "DX": (22, 28), "DY": (31, 37),
    "SWLat": (43, 51), "SWLon": (57, 65), "DT": (68, 80),
}
# Regexes used by OceanweatherTo306.py.
HEADER_REGEX = {
    "iLat": r"iLat\s*=\s*(\d+)", "iLong": r"iLong\s*=\s*(\d+)", "DX": r"DX\s*=\s*([\d.]+)",
    "DY": r"DY\s*=\s*([\d.]+)", "SWLat": r"SWLat\s*=\s*([-\d.]+)",
    "SWLon": r"SWLon\s*=\s*([-\d.]+)", "DT": r"DT\s*=\s*(\d+)",
}

# Physical plausibility limits (hard failures).
LIMITS = {"u10": (-100.0, 100.0), "v10": (-100.0, 100.0), "mslp": (850.0, 1100.0)}


def title_line(start: dt.datetime, end: dt.datetime) -> str:
    return f"{TITLE:<55}{start:%Y%m%d%H}     {end:%Y%m%d%H}\n"


def block_header(grid: TargetGrid, time: dt.datetime) -> str:
    return HEADER_FMT % (grid.ny, grid.nx, grid.res, grid.res, grid.south, grid.west,
                         time.strftime("%Y%m%d%H%M"))


def format_values(values: np.ndarray) -> str:
    """Format a (ny, nx) field, row 0 = south, as OWI F10.4 lines (8 per line)."""
    flat = np.asarray(values, dtype=float).ravel()
    full = len(flat) // VALUES_PER_LINE * VALUES_PER_LINE
    text = (LINE_FMT * (full // VALUES_PER_LINE)) % tuple(flat[:full])
    if full < len(flat):
        text += (VALUE_FMT * (len(flat) - full)) % tuple(flat[full:]) + "\n"
    return text


def lines_per_field(grid: TargetGrid) -> int:
    return math.ceil(grid.nx * grid.ny / VALUES_PER_LINE)


class OwiWriter:
    """Stream time slices into a .wnd/.pre pair."""

    def __init__(self, wnd_path: Path, pre_path: Path, grid: TargetGrid,
                 start: dt.datetime, end: dt.datetime):
        self.grid = grid
        self.wnd_path, self.pre_path = Path(wnd_path), Path(pre_path)
        # newline="\n": identical files on Windows and Linux.
        self._wnd = open(self.wnd_path, "w", newline="\n")
        self._pre = open(self.pre_path, "w", newline="\n")
        self._wnd.write(title_line(start, end))
        self._pre.write(title_line(start, end))
        self.first_slice = None  # kept for the orientation self-check in validation

    def write(self, time: dt.datetime, u10, v10, mslp_hpa):
        shape = (self.grid.ny, self.grid.nx)
        for name, arr in (("u10", u10), ("v10", v10), ("mslp", mslp_hpa)):
            if arr.shape != shape:
                raise ValueError(f"{name} has shape {arr.shape}, expected {shape}")
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} has non-finite values at {time}")
        if self.first_slice is None:
            self.first_slice = {"time": time, "u10": u10.copy(), "v10": v10.copy(), "mslp": mslp_hpa.copy()}
        header = block_header(self.grid, time)
        self._wnd.write(header)
        self._wnd.write(format_values(u10))
        self._wnd.write(format_values(v10))
        self._pre.write(header)
        self._pre.write(format_values(mslp_hpa))

    def close(self):
        self._wnd.close()
        self._pre.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------- validation

@dataclass
class VarStats:
    min: float = math.inf
    max: float = -math.inf
    total: float = 0.0
    count: int = 0
    nan: int = 0

    def add(self, a: np.ndarray):
        finite = np.isfinite(a)
        self.nan += int(a.size - finite.sum())
        if finite.any():
            v = a[finite]
            self.min = min(self.min, float(v.min()))
            self.max = max(self.max, float(v.max()))
            self.total += float(v.sum())
            self.count += int(v.size)

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else math.nan


@dataclass
class ScanResult:
    path: Path
    title: str = ""
    times: list = field(default_factory=list)
    header_params: dict = field(default_factory=dict)  # last header, regex-parsed
    stats: dict = field(default_factory=dict)
    first_block: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def parse_header_fixed(line: str) -> dict:
    return {k: line[a:b] for k, (a, b) in HEADER_COLUMNS.items()}


def parse_header_regex(line: str) -> dict:
    out = {}
    for key, pat in HEADER_REGEX.items():
        m = re.search(pat, line)
        out[key] = float(m.group(1)) if m else None
    return out


def _check_header(line: str, grid: TargetGrid, errors: list, where: str):
    if len(line) != HEADER_LEN or not line.startswith("iLat="):
        errors.append(f"{where}: malformed header {line!r}")
        return None
    fixed = parse_header_fixed(line)
    rx = parse_header_regex(line)
    try:
        f = {k: (int(v) if k in ("iLat", "iLong") else (v if k == "DT" else float(v))) for k, v in fixed.items()}
        t = dt.datetime.strptime(f["DT"], "%Y%m%d%H%M")
    except ValueError:
        errors.append(f"{where}: header fields not at the fixed columns: {line!r}")
        return None
    expected = {"iLat": grid.ny, "iLong": grid.nx, "DX": grid.res, "DY": grid.res,
                "SWLat": grid.south, "SWLon": grid.west}
    for k, v in expected.items():
        if abs(f[k] - v) > 1e-6:
            errors.append(f"{where}: header {k}={f[k]} expected {v}")
        if rx[k] is None or abs(rx[k] - v) > 1e-6:
            errors.append(f"{where}: OceanweatherTo306 regex reads {k}={rx[k]}, expected {v}")
    return t, rx


def _read_field(f, grid: TargetGrid, errors: list, where: str):
    n = grid.nx * grid.ny
    nlines = lines_per_field(grid)
    rem = n % VALUES_PER_LINE
    lines = [f.readline() for _ in range(nlines)]
    for i, ln in enumerate(lines):
        body = ln.rstrip("\n")
        want = (rem if (i == nlines - 1 and rem) else VALUES_PER_LINE) * WIDTH
        if len(body) != want or not ln.endswith("\n"):
            errors.append(f"{where}: data line {i + 1} has width {len(body)}, expected {want}")
            break
    try:
        values = np.array("".join(lines).split(), dtype=float)
    except ValueError:
        errors.append(f"{where}: non-numeric data")
        return None
    if values.size != n:
        errors.append(f"{where}: {values.size} values, expected {n}")
        return None
    # Exact line widths + whitespace-separated count => every value sits in its own
    # 10-column slot, which is what owi2wind.py (OwiAscii) slices.
    return values.reshape(grid.ny, grid.nx)


def scan_owi_file(path: Path, kind: str, grid: TargetGrid) -> ScanResult:
    """Re-read a written .wnd ('wnd') or .pre ('pre') file the way the downstream tools do."""
    res = ScanResult(Path(path))
    names = ("u10", "v10") if kind == "wnd" else ("mslp",)
    res.stats = {n: VarStats() for n in names}
    with open(path, "r", newline="") as f:
        res.title = f.readline().rstrip("\n")
        while True:
            line = f.readline()
            if not line:
                break
            where = f"{Path(path).name} slice {len(res.times) + 1}"
            hdr = _check_header(line.rstrip("\n"), grid, res.errors, where)
            if hdr is None:
                break
            t, rx = hdr
            res.times.append(t)
            res.header_params = rx
            for name in names:
                arr = _read_field(f, grid, res.errors, f"{where} {name}")
                if arr is None:
                    return res
                res.stats[name].add(arr)
                if len(res.times) == 1:
                    res.first_block[name] = arr
    return res


def downstream_grid_size(grid: TargetGrid) -> tuple:
    """(ny, nx) as owi2wind.py will rebuild it from OceanweatherTo306.py's Wind_Inp.txt.

    OceanweatherTo306.py writes the bounds with one decimal and the resolution as "10."
    (i.e. always 0.1 deg); owi2wind.py then truncates (max - min) / res + 1 to an int.
    Floating point can make that one short (e.g. 41.0..41.3 -> 3 rows instead of 4).
    """
    res = 1 / float("10.")
    lat_max = grid.south + (grid.ny - 1) * grid.res
    lon_max = grid.west + (grid.nx - 1) * grid.res
    ny = int((float(f"{lat_max:.1f}") - float(f"{grid.south:.1f}")) / res + 1)
    nx = int((float(f"{lon_max:.1f}") - float(f"{grid.west:.1f}")) / res + 1)
    return ny, nx


def compare_with_reference(ours: Path, ref: Path) -> list:
    """Structural comparison against a MetGet .wnd/.pre (values are not compared)."""
    problems = []
    with open(ours) as a, open(ref) as b:
        la = [a.readline().rstrip("\n") for _ in range(5)]
        lb = [b.readline().rstrip("\n") for _ in range(5)]
    if not TITLE_RE.match(lb[0]):
        problems.append(f"reference title line has an unexpected layout: {lb[0]!r}")
    if bool(TITLE_RE.match(la[0])) != bool(TITLE_RE.match(lb[0])) or len(la[0]) != len(lb[0]):
        problems.append(f"title line layout differs:\n  ours: {la[0]!r}\n  ref:  {lb[0]!r}")
    ha, hb = parse_header_fixed(la[1]), parse_header_fixed(lb[1])
    for k in HEADER_COLUMNS:
        if k != "DT" and ha[k] != hb[k]:
            problems.append(f"header {k}: ours {ha[k]!r} vs reference {hb[k]!r}")
    if len(la[1]) != len(lb[1]):
        problems.append(f"header length: ours {len(la[1])} vs reference {len(lb[1])}")
    for i in range(2, 5):
        if len(la[i]) != len(lb[i]) or len(la[i].split()) != len(lb[i].split()):
            problems.append(f"data line {i - 1} layout differs: {la[i]!r} vs {lb[i]!r}")
    return problems
