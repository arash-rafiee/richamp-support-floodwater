"""Download data from the Brown University NEST sensors in Rhode Island.

NEST (Network for Environmental Sensing & Technology, https://dashboard.nest.brown.edu/) runs its own
water-level, temperature, weather and air-quality sensors; the dashboard also mirrors USGS, NOAA and
airport stations, which are left out here. Brown sensors are recognised by name: RI_* (water level),
brown-* (temperature, met, wind) and Combined-* (air quality + met).

Sources
-------
ERDDAP       NERACOOS archive (https://data.neracoos.org/erddap), linked per sensor by the ERDDAPURL
             attribute. Water level in NAVD88 m with QARTOD flags; requested by calendar month.
ThingsBoard  the dashboard's backend (https://thingsboard.riddc.brown.edu), read with the dashboard's
             public login. Used for sensors without an ERDDAP archive and for the period after the
             archive's last record. Raw telemetry, every key the sensor reports; requested by week.
             History there starts around Oct 2025.

Output (in --out)
-----------------
stations.csv        one row per sensor: name, position, ERDDAP link, records per source, and
                    source_check: whether ERDDAP and ThingsBoard carry the same sensor ("same", or
                    MISMATCH; then use the two sources separately, the `source` column tells them apart)
<device>.csv        time (UTC), source (erddap / thingsboard), then the sensor's variables

Raw downloads of finished months/weeks are kept under <out>/raw/ and reused on later runs.

Usage
-----
python tools/nest/get_nest_data.py --start 2026-09-01 --end 2026-10-06 --out nest_data
python tools/nest/get_nest_data.py --start 2025-01-01 --end 2026-10-06 --sensors RI_CWR RI_BCP
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import requests

USER_AGENT = "richamp-support-floodwater/nest (python-requests)"
TB_URL = "https://thingsboard.riddc.brown.edu"
TB_PUBLIC_ID = "02bbf490-9a0d-11f0-aeab-2d9d271013d5"  # from the iframe in dashboard.nest.brown.edu
TB_GROUPS = {  # device groups the public login can read
    "water_level": "5ccb6950-131d-11f1-93ab-094013c2e961",
    "temperature": "09def7b0-131e-11f1-93ab-094013c2e961",
    "wind": "0c5af430-131e-11f1-93ab-094013c2e961",
}
TB_SKIP_KEYS = {"payload", "ts", "time"}  # payload repeats the other keys as one string
TB_CHUNK_DAYS = 7
TB_LIMIT = 200000
BROWN_NAME = re.compile(r"^(RI_|brown-|Combined-)")
RI_LAT, RI_LON = (41.1, 42.03), (-71.92, -71.1)  # Rhode Island incl. Block Island
CACHE_AFTER_H = 48  # a chunk ending at least this long ago is final and cached


class Client:
    """GET/POST with retries; a finished chunk is cached under raw/ and reused."""

    def __init__(self, raw: Path, attempts=5, backoff_s=2.0, timeout=(10.0, 300.0)):
        self.raw, self.attempts, self.backoff_s, self.timeout = raw, attempts, backoff_s, timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def request(self, method, url, **kw) -> requests.Response:
        err = None
        for attempt in range(self.attempts):
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code} for {url}")
                return r
            except requests.RequestException as e:
                err = e
                if attempt + 1 < self.attempts:
                    time.sleep(self.backoff_s * 2 ** attempt)
        raise RuntimeError(f"giving up on {url}: {err}")

    def cached_text(self, url, rel: str | None, headers=None) -> str | None:
        """Response text, or None when the server has no data (404). ``rel`` None = never cache."""
        dest = self.raw / rel if rel else None
        if dest and dest.exists():
            text = dest.read_text(encoding="utf-8")
            return None if text == "" else text
        r = self.request("GET", url, headers=headers)
        if r.status_code == 404:
            text = None
        else:
            r.raise_for_status()
            text = r.text
        if dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text or "", encoding="utf-8")  # empty file = known to have no data
        return text


def is_final(chunk_end: pd.Timestamp) -> bool:
    return chunk_end < pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(hours=CACHE_AFTER_H)


# ---------------------------------------------------------------- sensors
def tb_login(client: Client) -> dict:
    r = client.request("POST", f"{TB_URL}/api/auth/login/public", json={"publicId": TB_PUBLIC_ID})
    r.raise_for_status()
    return {"X-Authorization": f"Bearer {r.json()['token']}"}


def brown_sensors(client: Client, auth: dict, names=None, all_states=False) -> pd.DataFrame:
    devices = {}
    for group, gid in TB_GROUPS.items():
        r = client.request("GET", f"{TB_URL}/api/entityGroup/{gid}/entities",
                           params={"pageSize": 1000, "page": 0}, headers=auth)
        r.raise_for_status()
        for e in r.json()["data"]:
            if BROWN_NAME.match(e["name"]) and (not names or e["name"] in names):
                devices.setdefault(e["id"]["id"], {"device": e["name"], "groups": []})["groups"].append(group)
    rows = []
    for dev_id, d in devices.items():
        r = client.request("GET", f"{TB_URL}/api/plugins/telemetry/DEVICE/{dev_id}/values/attributes", headers=auth)
        r.raise_for_status()
        a = {x["key"]: x["value"] for x in r.json()}
        lat, lon = a.get("latitude"), a.get("longitude")
        in_ri = (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                 and RI_LAT[0] <= lat <= RI_LAT[1] and RI_LON[0] <= lon <= RI_LON[1])
        if not (all_states or d["device"].startswith("RI_") or in_ri):
            continue
        rows.append({"device": d["device"], "name": a.get("displayName", ""), "groups": "+".join(d["groups"]),
                     "lat": lat, "lon": lon, "erddap_url": a.get("ERDDAPURL", ""), "tb_id": dev_id})
    return pd.DataFrame(rows).sort_values("device", ignore_index=True)


# ---------------------------------------------------------------- ERDDAP
def fetch_erddap(client: Client, url: str, start, end) -> pd.DataFrame:
    m = re.match(r"(https?://.+/erddap)/(?:tabledap|info)/([^/.]+)", url or "")
    if not m:
        return pd.DataFrame()
    root, ds = m.groups()
    frames = []
    for month in pd.period_range(start, end, freq="M"):
        t0, t1 = max(start, month.start_time), min(end, month.end_time.floor("s"))
        q = urllib.parse.quote(f"&time>={t0:%Y-%m-%dT%H:%M:%SZ}&time<={t1:%Y-%m-%dT%H:%M:%SZ}", safe="&=")
        rel = f"erddap/{ds}/{month}.csv" if t1 == month.end_time.floor("s") and is_final(t1) else None
        text = client.cached_text(f"{root}/tabledap/{ds}.csv?{q}", rel)
        if text:
            frames.append(pd.read_csv(io.StringIO(text), skiprows=[1], low_memory=False))  # row 2 = units
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop(columns=["latitude", "longitude"], errors="ignore")
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df


# ---------------------------------------------------------------- ThingsBoard
def fetch_thingsboard(client: Client, auth: dict, dev_id: str, start, end) -> pd.DataFrame:
    base = f"{TB_URL}/api/plugins/telemetry/DEVICE/{dev_id}"
    r = client.request("GET", f"{base}/keys/timeseries", headers=auth)
    r.raise_for_status()
    keys = [k for k in r.json() if k not in TB_SKIP_KEYS and not k.isdigit()]
    if not keys:
        return pd.DataFrame()
    keys_q = urllib.parse.quote(",".join(keys), safe=",")
    series, t0 = {}, start
    while t0 <= end:
        t1 = min(t0 + pd.Timedelta(days=TB_CHUNK_DAYS), end)
        s_ms, e_ms = int(t0.tz_localize("UTC").timestamp() * 1000), int(t1.tz_localize("UTC").timestamp() * 1000)
        url = f"{base}/values/timeseries?keys={keys_q}&startTs={s_ms}&endTs={e_ms}&limit={TB_LIMIT}&agg=NONE"
        rel = f"thingsboard/{dev_id}/{t0:%Y%m%d%H%M}_{t1:%Y%m%d%H%M}.json" if is_final(t1) else None
        text = client.cached_text(url, rel, headers=auth)
        for k, values in (json.loads(text) if text else {}).items():
            if len(values) >= TB_LIMIT:
                print(f"    warning: {k} hit the {TB_LIMIT}-record limit in {t0:%Y-%m-%d}..{t1:%Y-%m-%d}")
            series.setdefault(k, []).extend((v["ts"], v["value"]) for v in values)
        t0 = t1 + pd.Timedelta(milliseconds=1)
    cols = []
    for k, pairs in series.items():
        s = pd.DataFrame(pairs, columns=["ts", k]).drop_duplicates("ts").set_index("ts")[k]
        num = pd.to_numeric(s, errors="coerce")
        cols.append(num if num.notna().sum() >= s.notna().sum() * 0.9 else s)  # keep text keys as text
    if not cols:
        return pd.DataFrame()
    df = pd.concat(cols, axis=1).sort_index().reset_index()
    df.insert(0, "time", pd.to_datetime(df.pop("ts"), unit="ms"))
    return df


def check_sources(client: Client, auth: dict, dev_id: str, er: pd.DataFrame, start) -> str:
    """Do ERDDAP and ThingsBoard carry the same sensor? Compared over the last day of the ERDDAP data.

    The same sensor gives the same timestamps (to the second) and values in both. RI_CWR, for one, does
    not: its ERDDAP archive comes from another instrument, 0.3-0.8 m off.
    """
    if er.empty or "navd88_meters" not in er:
        return ""
    t1 = er["time"].max()
    t0 = max(start, t1 - pd.Timedelta(days=1))
    tb = fetch_thingsboard(client, auth, dev_id, t0, t1)
    if tb.empty or "navd88_meters" not in tb:
        return "not checked (no navd88_meters in ThingsBoard)"
    a = er[er["time"] >= t0].assign(time=lambda d: d["time"].dt.floor("s")).set_index("time")["navd88_meters"]
    b = tb.assign(time=lambda d: d["time"].dt.floor("s")).set_index("time")["navd88_meters"]
    both = pd.concat([a[~a.index.duplicated()], b[~b.index.duplicated()]], axis=1, join="inner").dropna()
    if len(both) < 0.5 * min(a.notna().sum(), b.notna().sum()):
        return f"MISMATCH: only {len(both)} of {a.notna().sum()} ERDDAP times also in ThingsBoard (different sensor?)"
    diff = (both.iloc[:, 0] - both.iloc[:, 1]).abs().max()
    return "same" if diff < 0.001 else f"MISMATCH: values differ by up to {diff:.3f} m"


# ---------------------------------------------------------------- one sensor, for the water graphs
_sessions: dict = {}


def water_level(device: str, start, end, raw: Path) -> pd.DataFrame:
    """NAVD88 water level of one RI_* sensor between start and end (naive UTC pandas Timestamps).

    Columns: time (naive UTC), navd88_meters, qc (the QARTOD roll-up: 1 pass, 2 not evaluated,
    3 suspect, 4 fail). ERDDAP first and ThingsBoard after the archive's last record, as
    download() does; finished chunks are cached under raw/. Used by GetBuoyWater for the
    stations whose source is "NEST". The login and sensor list are reused within a process.
    """
    key = str(raw)
    if key not in _sessions:
        client = Client(raw)
        auth = tb_login(client)
        _sessions[key] = (client, auth, brown_sensors(client, auth))
    client, auth, sensors = _sessions[key]
    match = sensors[sensors["device"] == device]
    if match.empty:
        raise ValueError(f"NEST has no sensor named {device}")
    st = match.iloc[0]
    er = fetch_erddap(client, st["erddap_url"], start, end)
    tb_start = start if er.empty else max(start, er["time"].max() + pd.Timedelta(seconds=1))
    tb = fetch_thingsboard(client, auth, st["tb_id"], tb_start, end) if tb_start <= end else pd.DataFrame()
    parts = [df for df in (er, tb) if not df.empty and "navd88_meters" in df]
    if not parts:
        return pd.DataFrame({"time": pd.Series(dtype="datetime64[ns]"), "navd88_meters": pd.Series(dtype=float), "qc": pd.Series(dtype=float)})
    df = pd.concat(parts, ignore_index=True).sort_values("time", kind="stable")
    qc = pd.to_numeric(df["qartod_qc_rollup"], errors="coerce") if "qartod_qc_rollup" in df else pd.Series(2.0, index=df.index)
    return pd.DataFrame({"time": df["time"].to_numpy(), "navd88_meters": pd.to_numeric(df["navd88_meters"], errors="coerce").to_numpy(),
                         "qc": qc.fillna(2).to_numpy()})


# ---------------------------------------------------------------- all together
def download(start, end, out: Path, names=None, all_states=False) -> None:
    client = Client(out / "raw")
    auth = tb_login(client)
    sensors = brown_sensors(client, auth, names, all_states)
    print(f"{len(sensors)} Brown NEST sensors; {start} .. {end} UTC -> {out}")
    out.mkdir(parents=True, exist_ok=True)
    stats = []
    for _, st in sensors.iterrows():
        print(f"  {st['device']:<22} {st['name']}", flush=True)
        row = {"n_erddap": 0, "n_thingsboard": 0, "first": "", "last": "", "source_check": "", "error": ""}
        try:
            er = fetch_erddap(client, st["erddap_url"], start, end)
            row["source_check"] = check_sources(client, auth, st["tb_id"], er, start)
            if row["source_check"].startswith("MISMATCH"):
                print(f"    {row['source_check']}")
            tb_start = start
            if not er.empty:
                tb_start = max(start, er["time"].max() + pd.Timedelta(seconds=1))
            tb = fetch_thingsboard(client, auth, st["tb_id"], tb_start, end) if tb_start <= end else pd.DataFrame()
            parts = [df.assign(source=src) for src, df in (("erddap", er), ("thingsboard", tb)) if not df.empty]
            if parts:
                df = pd.concat(parts, ignore_index=True).sort_values("time", kind="stable")
                df = df[["time", "source"] + [c for c in df.columns if c not in ("time", "source")]]
                df.to_csv(out / f"{st['device']}.csv", index=False)
                row |= {"n_erddap": len(er), "n_thingsboard": len(tb),
                        "first": f"{df['time'].min():%Y-%m-%d %H:%M}", "last": f"{df['time'].max():%Y-%m-%d %H:%M}"}
            print(f"    erddap {row['n_erddap']}, thingsboard {row['n_thingsboard']} records")
        except Exception as err:  # noqa: BLE001 - one sensor must not stop the others
            row["error"] = str(err)[:200]
            print(f"    failed: {err}")
        stats.append(row)
    sensors = pd.concat([sensors, pd.DataFrame(stats)], axis=1)
    sensors.to_csv(out / "stations.csv", index=False)
    print(f"done: {(sensors['n_erddap'] + sensors['n_thingsboard'] > 0).sum()} of {len(sensors)} sensors have data")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--start", required=True, help="UTC start, e.g. 2026-09-01")
    p.add_argument("--end", default=None, help="UTC end (default: now)")
    p.add_argument("--out", type=Path, default=Path("nest_data"))
    p.add_argument("--sensors", nargs="*", help="device names, e.g. RI_CWR brown-met-001 (default: all)")
    p.add_argument("--all-states", action="store_true", help="also Brown sensors outside Rhode Island")
    a = p.parse_args()
    start = pd.Timestamp(a.start)
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp.now(tz="UTC").tz_localize(None).floor("min")
    if len(a.end or "") == 10:  # a bare date means the whole day
        end += pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    download(start, end, a.out, a.sensors, a.all_states)


if __name__ == "__main__":
    main()
