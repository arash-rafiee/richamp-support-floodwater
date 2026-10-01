"""Minimal GRIB2 decoder (numpy only) for the GDAS messages this package downloads.

Supports exactly what NCEP GDAS pgrb2.0p25 uses for UGRD/VGRD/PRMSL:
  * grid definition template 3.0 (regular lat/lon)
  * product definition template 4.0 (analysis/forecast at a point in time)
  * data representation template 5.0 (simple packing; NOMADS filter output) and
    5.3 (complex packing with spatial differencing; NCEP/AWS files)
  * no bitmap
Anything else raises ValueError. Octet numbers in comments follow the WMO GRIB2 tables.
"""
from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass

import numpy as np


@dataclass
class Message:
    discipline: int
    category: int
    number: int
    surface_type: int
    level: float
    reference_time: dt.datetime
    forecast_hours: int
    ni: int
    nj: int
    lat1: float
    lon1: float
    dlat: float
    dlon: float
    j_positive: bool
    values: np.ndarray  # shape (nj, ni), rows in file order

    @property
    def valid_time(self) -> dt.datetime:
        return self.reference_time + dt.timedelta(hours=self.forecast_hours)


def _u(b: bytes, a: int, n: int) -> int:
    """Unsigned big-endian integer at 1-based octet a of length n."""
    return int.from_bytes(b[a - 1:a - 1 + n], "big")


def _s(b: bytes, a: int, n: int) -> int:
    """GRIB2 signed integer (sign-magnitude, sign in the leading bit)."""
    v = _u(b, a, n)
    top = 1 << (8 * n - 1)
    return -(v - top) if v & top else v


def _unpack(bits: np.ndarray, offsets: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """Read unsigned integers of the given bit widths starting at the given bit offsets."""
    out = np.zeros(len(offsets), dtype=np.int64)
    maxw = int(widths.max()) if len(widths) else 0
    if maxw == 0:
        return out
    j = np.arange(maxw)
    for s in range(0, len(offsets), 262144):  # chunks keep memory small
        off, w = offsets[s:s + 262144, None], widths[s:s + 262144, None]
        valid = j[None, :] < w
        pos = np.where(valid, off + j[None, :], 0)
        if pos.max() >= len(bits):
            raise ValueError("GRIB2 data section is shorter than its packing requires")
        weight = np.where(valid, np.left_shift(1, np.clip(w - 1 - j[None, :], 0, None)), 0)
        out[s:s + 262144] = (bits[pos].astype(np.int64) * weight).sum(axis=1)
    return out


def _fixed(bits: np.ndarray, start: int, count: int, width: int) -> np.ndarray:
    offsets = start + width * np.arange(count, dtype=np.int64)
    return _unpack(bits, offsets, np.full(count, width, dtype=np.int64))


def _byte_align(pos: int) -> int:
    return (pos + 7) // 8 * 8


def _decode_simple(s5: bytes, s7: bytes, npts: int) -> np.ndarray:
    nbits = _u(s5, 20, 1)
    if nbits == 0:
        return np.zeros(npts, dtype=np.int64)
    bits = np.unpackbits(np.frombuffer(s7, dtype=np.uint8, offset=5))
    return _fixed(bits, 0, npts, nbits)


def _decode_complex_sd(s5: bytes, s7: bytes, npts: int) -> np.ndarray:
    nbits = _u(s5, 20, 1)            # bits per group reference value
    if _u(s5, 22, 1) != 1:           # 1 = general group splitting
        raise ValueError("unsupported group splitting method")
    if _u(s5, 23, 1) != 0:           # 0 = no explicit missing values
        raise ValueError("GRIB2 missing-value management is not supported")
    ng = _u(s5, 32, 4)               # number of groups
    width_ref, width_bits = _u(s5, 36, 1), _u(s5, 37, 1)
    len_ref, len_inc = _u(s5, 38, 4), _u(s5, 42, 1)
    last_len, len_bits = _u(s5, 43, 4), _u(s5, 47, 1)
    order, nocts = _u(s5, 48, 1), _u(s5, 49, 1)
    if order not in (1, 2) or nocts == 0:
        raise ValueError(f"unsupported spatial differencing (order {order}, {nocts} octets)")

    data = s7[5:]
    # Extra descriptors: first value(s) and overall minimum, sign-magnitude, nocts octets each.
    extras = [_s(data, 1 + k * nocts, nocts) for k in range(order + 1)]
    first, minsd = extras[:order], extras[order]
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    pos = 8 * nocts * (order + 1)

    refs = _fixed(bits, pos, ng, nbits)
    pos = _byte_align(pos + ng * nbits)
    widths = _fixed(bits, pos, ng, width_bits) + width_ref
    pos = _byte_align(pos + ng * width_bits)
    lengths = _fixed(bits, pos, ng, len_bits) * len_inc + len_ref
    lengths[-1] = last_len
    pos = _byte_align(pos + ng * len_bits)
    if int(lengths.sum()) != npts:
        raise ValueError(f"group lengths sum to {int(lengths.sum())}, expected {npts} points")

    w = np.repeat(widths, lengths)
    offsets = pos + np.concatenate(([0], np.cumsum(w[:-1])))
    x = _unpack(bits, offsets, w) + np.repeat(refs, lengths)

    # Undo spatial differencing: x[n] + minsd is the order-th difference for n >= order.
    d = x + minsd
    f = np.empty(npts, dtype=np.int64)
    if order == 1:
        f[0] = first[0]
        f[1:] = first[0] + np.cumsum(d[1:])
    else:
        f[0], f[1] = first
        g = (first[1] - first[0]) + np.cumsum(d[2:])   # first differences
        f[2:] = first[1] + np.cumsum(g)
    return f


def _sections(msg: bytes):
    pos = 16
    while pos < len(msg) - 4:
        length, number = _u(msg, pos + 1, 4), msg[pos + 4]
        yield number, msg[pos:pos + length]
        pos += length


def decode_message(msg: bytes) -> Message:
    if msg[:4] != b"GRIB" or msg[7] != 2:
        raise ValueError("not a GRIB2 message")
    discipline = msg[6]
    secs = {}
    for number, sec in _sections(msg):
        if number in secs:
            raise ValueError("multiple fields in one GRIB2 message are not supported")
        secs[number] = sec
    missing = {1, 3, 4, 5, 6, 7} - set(secs)
    if missing:
        raise ValueError(f"GRIB2 message lacks sections {sorted(missing)}")
    s1, s3, s4, s5, s6, s7 = (secs[k] for k in (1, 3, 4, 5, 6, 7))

    ref = dt.datetime(_u(s1, 13, 2), _u(s1, 15, 1), _u(s1, 16, 1),
                      _u(s1, 17, 1), _u(s1, 18, 1), _u(s1, 19, 1))

    if _u(s3, 13, 2) != 0:
        raise ValueError(f"grid template 3.{_u(s3, 13, 2)} is not supported (need 3.0)")
    npts = _u(s3, 7, 4)
    ni, nj = _u(s3, 31, 4), _u(s3, 35, 4)
    basic, subdiv = _u(s3, 39, 4), _u(s3, 43, 4)
    unit = 1e-6 if basic in (0, 0xFFFFFFFF) or subdiv in (0, 0xFFFFFFFF) else basic / subdiv
    lat1, lon1 = _s(s3, 47, 4) * unit, _s(s3, 51, 4) * unit
    dlon, dlat = _u(s3, 64, 4) * unit, _u(s3, 68, 4) * unit
    scan = _u(s3, 72, 1)
    if scan & 0x80 or scan & 0x20 or scan & 0x10:
        raise ValueError(f"unsupported scanning mode {scan:#04x}")
    if ni * nj != npts:
        raise ValueError("grid size does not match the number of points")

    if _u(s4, 8, 2) != 0:
        raise ValueError(f"product template 4.{_u(s4, 8, 2)} is not supported (need 4.0)")
    if _u(s4, 18, 1) != 1:
        raise ValueError("forecast time unit is not hours")
    surface = _u(s4, 23, 1)
    level = _u(s4, 25, 4) / 10 ** _s(s4, 24, 1)

    if _u(s6, 6, 1) != 255:
        raise ValueError("GRIB2 bitmaps are not supported")
    if _u(s5, 6, 4) != npts:
        raise ValueError("data section point count does not match the grid")

    drt = _u(s5, 10, 2)
    if drt == 0:
        packed = _decode_simple(s5, s7, npts)
    elif drt == 3:
        packed = _decode_complex_sd(s5, s7, npts)
    else:
        raise ValueError(f"data representation template 5.{drt} is not supported (need 5.0 or 5.3)")
    r = struct.unpack(">f", s5[11:15])[0]
    e, d = _s(s5, 16, 2), _s(s5, 18, 2)
    # Same operation order as eccodes, so results agree bit for bit.
    values = (packed * 2.0 ** e + r) * 10.0 ** -d

    return Message(discipline, _u(s4, 10, 1), _u(s4, 11, 1), surface, level, ref, _u(s4, 19, 4),
                   ni, nj, lat1, lon1, dlat, dlon, bool(scan & 0x40), values.reshape(nj, ni))


def read_messages(data: bytes):
    pos = 0
    while pos < len(data):
        if data[pos:pos + 4] != b"GRIB":
            raise ValueError(f"not GRIB data at byte {pos}")
        length = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        yield decode_message(data[pos:pos + length])
        pos += length
