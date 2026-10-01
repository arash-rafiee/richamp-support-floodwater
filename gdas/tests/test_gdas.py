"""Offline tests for the GDAS -> OWI path.  Run from the repository root:

    python -m pytest gdas/tests -q
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

GDAS_DIR = Path(__file__).resolve().parents[1]
REPO = GDAS_DIR.parent
sys.path[:0] = [str(GDAS_DIR), str(REPO)]

from gdas_source import Downloader, HourSource, check_grib_bytes, hourly_timeline  # noqa: E402
from grid import Bilinear, TargetGrid  # noqa: E402
from owi import (OwiWriter, block_header, compare_with_reference, downstream_grid_size, format_values,  # noqa: E402
                 scan_owi_file, title_line)

# First lines of a real MetGet (GFS, owi-ascii) .pre used by RICHAMP.
METGET_PRE = """\
Oceanweather WIN/PRE Format                            2022122200     2022122700
iLat= 441iLong=1021DX=0.1000DY=0.1000SWLat= 3.00000SWLon=-98.0000DT=202212220000
 1009.7626 1009.7690 1009.7754 1009.7810 1009.7859 1009.7907 1009.8163 1009.8419
 1009.8611 1009.8739 1009.8867 1009.8707 1009.8547 1009.8451 1009.8418 1009.8386
 1009.8370 1009.8354 1009.8363 1009.8395 1009.8427 1009.8235 1009.8043 1009.7875
"""
RICHAMP = TargetGrid.from_domain(0.1, -98.0, 3.0, 4.0, 47.0)


# --------------------------------------------------------------------------- format

def test_title_and_header_match_metget():
    lines = METGET_PRE.splitlines()
    assert title_line(dt.datetime(2022, 12, 22), dt.datetime(2022, 12, 27)).rstrip("\n") == lines[0]
    assert block_header(RICHAMP, dt.datetime(2022, 12, 22)).rstrip("\n") == lines[1]


def test_values_formatted_like_metget():
    lines = METGET_PRE.splitlines()[2:]
    values = np.array(" ".join(lines).split(), dtype=float)
    assert format_values(values).splitlines() == lines


def test_partial_last_line():
    text = format_values(np.arange(11, dtype=float))
    rows = text.splitlines()
    assert [len(r) for r in rows] == [80, 30]


def test_reference_comparison(tmp_path):
    ref = tmp_path / "ref.pre"
    ref.write_text(METGET_PRE)
    ours = tmp_path / "ours.pre"
    ours.write_text(METGET_PRE.replace("2022122200", "2025102800"))
    assert compare_with_reference(ours, ref) == []
    ours.write_text(METGET_PRE.replace("DX=0.1000", "DX=0.2500"))
    assert compare_with_reference(ours, ref)


# --------------------------------------------------------------------------- timeline

def test_hourly_timeline_uses_analysis_plus_f001_f005():
    hours = hourly_timeline(dt.datetime(2025, 10, 28, 16), dt.datetime(2025, 10, 29, 1))
    got = [(h.valid.hour, h.cycle.strftime("%d%H"), h.fhour) for h in hours]
    assert got == [(16, "2812", 4), (17, "2812", 5), (18, "2818", 0), (19, "2818", 1),
                   (20, "2818", 2), (21, "2818", 3), (22, "2818", 4), (23, "2818", 5),
                   (0, "2900", 0), (1, "2900", 1)]
    assert all(0 <= h.fhour <= 5 for h in hours)


def test_week_is_complete_and_unique():
    hours = hourly_timeline(dt.datetime(2025, 10, 28), dt.datetime(2025, 11, 4))
    assert len(hours) == 7 * 24 + 1
    assert len({h.valid for h in hours}) == len(hours)
    assert all(b.valid - a.valid == dt.timedelta(hours=1) for a, b in zip(hours, hours[1:]))
    assert hours[-1].filename == "gdas.t00z.pgrb2.0p25.f000"


def test_timeline_rejects_partial_hours():
    with pytest.raises(ValueError):
        hourly_timeline(dt.datetime(2025, 10, 28, 0, 30), dt.datetime(2025, 10, 28, 3))


# --------------------------------------------------------------------------- grid

def test_richamp_grid_matches_fort15():
    # fort.15: NWLAT=441 NWLON=1021 WLATMAX=47.0 WLONMIN=-98.0 WLATINC=WLONINC=0.1
    g = RICHAMP
    assert (g.ny, g.nx) == (441, 1021)
    assert g.lats[-1] == pytest.approx(47.0) and g.lons[0] == -98.0 and g.lons[-1] == pytest.approx(4.0)


def test_downstream_grid_size_check():
    assert downstream_grid_size(RICHAMP) == (441, 1021)
    # owi2wind.py's int() truncation loses a row here; validation must catch it.
    assert downstream_grid_size(TargetGrid.from_domain(0.1, -72.0, 41.0, -71.5, 41.3)) == (3, 6)


def test_domain_corners_sorted_like_metget():
    assert TargetGrid.from_domain(-0.1, 4.0, 47.0, -98.0, 3.0) == RICHAMP
    with pytest.raises(ValueError):
        TargetGrid.from_domain(0.1, -98.0, 3.0, 4.05, 47.0)


def test_bilinear_exact_for_linear_fields():
    src_lat = np.arange(2.0, 48.01, 0.25)
    src_lon = np.arange(-99.0, 5.01, 0.25)
    field = 3.0 * src_lat[:, None] - 2.0 * src_lon[None, :] + 1.0
    out = Bilinear(src_lat, src_lon, RICHAMP.lats, RICHAMP.lons)(field)
    expect = 3.0 * RICHAMP.lats[:, None] - 2.0 * RICHAMP.lons[None, :] + 1.0
    np.testing.assert_allclose(out, expect, atol=1e-9)


def test_bilinear_refuses_to_extrapolate():
    src = np.arange(5.0, 40.0, 0.25)
    with pytest.raises(ValueError, match="extrapolate"):
        Bilinear(src, np.arange(-99.0, 5.01, 0.25), RICHAMP.lats, RICHAMP.lons)


# --------------------------------------------------------------------------- download checks

def _grib2_message(payload=b"x" * 10):
    length = 16 + len(payload) + 4
    return b"GRIB\x00\x00\x00\x02" + length.to_bytes(8, "big") + payload + b"7777"


def test_grib_byte_checks():
    good = _grib2_message() * 3
    check_grib_bytes(good, 3)
    for bad in (b"<!doctype html><html>Request for Old Data</html>", good[:-1], good[: len(good) // 3 * 2]):
        with pytest.raises(ValueError):
            check_grib_bytes(bad, 3)


def test_idx_byte_ranges_and_labels():
    idx = "\n".join([
        "1:0:d=2025102800:PRMSL:mean sea level:1 hour fcst:",
        "2:100:d=2025102800:CLMR:1 hybrid level:1 hour fcst:",
        "588:5000:d=2025102800:UGRD:10 m above ground:1 hour fcst:",
        "589:6000:d=2025102800:VGRD:10 m above ground:1 hour fcst:",
        "590:7000:d=2025102800:TMP:2 m above ground:1 hour fcst:",
    ])
    h = HourSource(dt.datetime(2025, 10, 28, 1), dt.datetime(2025, 10, 28), 1)
    assert Downloader._byte_ranges(idx, h, "u") == [(5000, 5999), (6000, 6999), (0, 99)]
    h_wrong = HourSource(dt.datetime(2025, 10, 28, 2), dt.datetime(2025, 10, 28), 2)
    with pytest.raises(ValueError):
        Downloader._byte_ranges(idx, h_wrong, "u")
    with pytest.raises(ValueError):
        Downloader._byte_ranges("<html>", h, "u")


# --------------------------------------------------------------------------- end to end

def _write_small_case(tmp_path):
    """6 x 6 grid (36 points: last data line partial), 3 hourly slices with known values."""
    g = TargetGrid.from_domain(0.1, -72.0, 41.0, -71.5, 41.5)
    assert downstream_grid_size(g) == (g.ny, g.nx)
    lon2, lat2 = np.meshgrid(g.lons, g.lats)
    times = [dt.datetime(2025, 10, 28, h) for h in range(3)]
    fields = [(lon2 + 80.0 + k, lat2 - 40.0 - k, 1000.0 + 10 * lat2 + lon2 / 10 + k) for k in range(3)]
    wnd, pre = tmp_path / "t_00_00.wnd", tmp_path / "t_00_00.pre"
    with OwiWriter(wnd, pre, g, times[0], times[-1]) as w:
        for t, (u, v, p) in zip(times, fields):
            w.write(t, u, v, p)
    return g, times, fields, wnd, pre


def test_written_files_rescan_cleanly(tmp_path):
    g, times, _, wnd, pre = _write_small_case(tmp_path)
    sw, sp = scan_owi_file(wnd, "wnd", g), scan_owi_file(pre, "pre", g)
    assert sw.errors == [] and sp.errors == []
    assert sw.times == times == sp.times
    assert sw.header_params == sp.header_params


def test_oceanweatherto306_and_owi2wind_unmodified(tmp_path):
    netCDF4 = pytest.importorskip("netCDF4")
    import OceanweatherTo306

    g, times, fields, wnd, pre = _write_small_case(tmp_path)
    out = tmp_path / "test_gdas.fort.22"
    OceanweatherTo306.convert_to_306(str(wnd), str(pre), str(out))

    rows = [list(map(float, ln.split())) for ln in out.read_text().splitlines()]
    assert len(rows) == len(times) * g.nx * g.ny
    # fort.22 starts at the north-west corner (north row first, west to east).
    u, v, p = fields[0]
    assert rows[0] == [round(u[-1, 0], 1), round(v[-1, 0], 1), round(p[-1, 0] * 100)]
    assert rows[g.nx - 1][0] == round(u[-1, -1], 1)
    inp = (tmp_path / "test_gdas.fort.22Wind_Inp.txt").read_text().splitlines()
    assert inp[2] == "2025 10 28 00 00 00" and int(inp[4]) == len(times)

    subprocess.run([sys.executable, str(REPO / "owi2wind.py"), out.name, out.name + "Wind_Inp.txt",
                    "-o", "test_gdas"], cwd=tmp_path, check=True, capture_output=True)
    with netCDF4.Dataset(tmp_path / "test_gdas.nc") as nc:
        np.testing.assert_allclose(nc["lat"][:], g.lats, atol=1e-6)
        np.testing.assert_allclose(nc["lon"][:], g.lons, atol=1e-6)
        for k, (u, v, p) in enumerate(fields):
            np.testing.assert_allclose(nc["wind_u"][k], u, atol=0.051)
            np.testing.assert_allclose(nc["wind_v"][k], v, atol=0.051)
            np.testing.assert_allclose(nc["PSFC"][k], p * 100, atol=0.51)
