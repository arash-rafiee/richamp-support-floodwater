"""Cached, resumable, polite HTTP downloads with a manifest.

Every file is saved exactly as the server sent it (a ``.gz`` stays gzipped),
so raw downloads are never modified. Parsing always reads from these files.

* **Cache**: an existing destination file is reused unless ``overwrite``.
* **Resume**: bytes go to ``<dest>.part``; a later attempt asks for the rest
  with a ``Range`` header and appends if the server answers 206.
* **Retries**: connection errors, 429 and 5xx are retried with exponential
  backoff (``backoff_s * 2**attempt``), honouring ``Retry-After``. 404 is not
  retried: it raises :class:`FileNotFoundError` so callers can record a gap.
* **Rate limit**: at least ``min_interval_s`` between requests to one host.
* **Validation**: an optional callback checks the complete file before it is
  moved into place, so a truncated or error-page download is never cached.
* **Manifest**: one JSON line per request or cache hit, with URL, query
  parameters, destination, UTC retrieval time, HTTP status, size and SHA-256.

The session deliberately has no urllib3 retry adapter, so retries are not
multiplied and each attempt is visible in the log.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

import pandas as pd
import requests

from wind_data.download.common import USER_AGENT

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
Validator = Callable[[Path], None]


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def utc_now_iso() -> str:
    return pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def non_empty(path: Path) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"{path.name} is empty")


def valid_gzip(path: Path) -> None:
    """Reads the whole stream, so a truncated archive fails here."""
    non_empty(path)
    with gzip.open(path, "rb") as fh:
        while fh.read(1 << 20):
            pass


def valid_json(path: Path) -> None:
    non_empty(path)
    with path.open("rb") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "error" in data:
        raise ValueError(f"service returned an error: {data['error']}")


def text_starting_with(prefix: str) -> Validator:
    """File must start with ``prefix`` (catches HTML error pages)."""

    def check(path: Path) -> None:
        non_empty(path)
        with path.open("rb") as fh:
            head = fh.read(len(prefix.encode()))
        if head.decode("utf-8", "replace") != prefix:
            raise ValueError(f"{path.name} does not start with {prefix!r}")

    return check


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
class Manifest:
    """Append-only JSON-lines log of downloads."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()

    def write(self, **entry: Any) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry.setdefault("logged_utc", utc_now_iso())
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def read(self) -> pd.DataFrame:
        if self.path is None or not self.path.exists():
            return pd.DataFrame()
        return pd.read_json(self.path, lines=True)


@dataclass
class FetchResult:
    path: Path
    url: str
    cached: bool
    status: int | None
    bytes: int
    sha256: str
    attempts: int


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class Fetcher:
    """Downloader shared by every stage (track, stations, observations, models)."""

    def __init__(
        self,
        manifest: Manifest | None = None,
        *,
        root: Path | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 180.0),
        max_attempts: int = 6,
        backoff_s: float = 2.0,
        min_interval_s: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.manifest = manifest or Manifest(None)
        self.root = root
        self.session = session or self._new_session()
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_s = backoff_s
        self.min_interval_s = min_interval_s
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._throttle_lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg, **overrides) -> "Fetcher":
        d = cfg.download
        kwargs = {
            "root": cfg.root,
            "timeout": (d["timeout_connect_s"], d["timeout_read_s"]),
            "max_attempts": int(d["max_attempts"]),
            "backoff_s": d["backoff_s"],
            "min_interval_s": d["min_request_interval_s"],
            **overrides,
        }
        return cls(Manifest(cfg.manifest_path), **kwargs)

    @staticmethod
    def _new_session() -> requests.Session:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        return s

    def _rel(self, path: Path) -> str:
        if self.root is not None:
            try:
                return str(path.resolve().relative_to(self.root.resolve())).replace("\\", "/")
            except ValueError:
                pass
        return str(path)

    def _throttle(self, url: str) -> None:
        """Space requests to one host by ``min_interval_s`` (thread-safe)."""
        if self.min_interval_s <= 0:
            return
        host = urlparse(url).netloc
        with self._throttle_lock:
            now = time.monotonic()
            slot = max(now, self._last_request.get(host, 0.0) + self.min_interval_s)
            self._last_request[host] = slot
        if slot > now:
            self._sleep(slot - now)

    def _delay(self, attempt: int, resp: requests.Response | None) -> float:
        delay = self.backoff_s * (2**attempt)
        if resp is not None and resp.headers.get("Retry-After", "").strip().isdigit():
            delay = max(delay, float(resp.headers["Retry-After"]))
        return delay

    def _request(self, url: str, *, params=None, headers=None, stream=False) -> tuple[requests.Response, int]:
        """GET with retries. Returns (response, attempts). 404 -> FileNotFoundError."""
        last_err: Exception | None = None
        for attempt in range(self.max_attempts):
            self._throttle(url)
            resp = None
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout, stream=stream)
                if resp.status_code == 404:
                    resp.close()
                    raise FileNotFoundError(f"404 Not Found: {url}")
                if resp.status_code in RETRY_STATUS:
                    last_err = requests.HTTPError(f"HTTP {resp.status_code} for {url}")
                    resp.close()
                else:
                    resp.raise_for_status()
                    return resp, attempt + 1
            except FileNotFoundError:
                raise
            except (requests.ConnectionError, requests.Timeout) as err:
                last_err = err
            if attempt + 1 < self.max_attempts:
                delay = self._delay(attempt, resp)
                log.warning("retry %d/%d in %.0fs: %s (%s)", attempt + 1, self.max_attempts - 1, delay, url, last_err)
                self._sleep(delay)
        raise RuntimeError(f"giving up on {url} after {self.max_attempts} attempts: {last_err}")

    def fetch(
        self,
        url: str,
        dest: Path,
        *,
        params: dict[str, Any] | None = None,
        overwrite: bool = False,
        validate: Validator | None = None,
    ) -> FetchResult:
        """Download ``url`` to ``dest`` (see the module docstring)."""
        full_url = url + ("?" + urlencode(params) if params else "")
        if dest.exists() and not overwrite:
            digest = sha256_of(dest)
            self.manifest.write(url=full_url, params=params, dest=self._rel(dest), cache="hit",
                                bytes=dest.stat().st_size, sha256=digest)
            return FetchResult(dest, full_url, True, None, dest.stat().st_size, digest, 0)

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        attempts_total = 0
        status: int | None = None
        for _ in range(2):  # second pass only if a resumed download fails validation
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else None
            try:
                resp, attempts = self._request(url, params=params, headers=headers, stream=True)
            except FileNotFoundError:
                self.manifest.write(url=full_url, params=params, dest=self._rel(dest), cache="miss",
                                    retrieved_utc=utc_now_iso(), http_status=404)
                raise
            attempts_total += attempts
            status = resp.status_code
            mode = "ab" if (offset and status == 206) else "wb"
            if offset and status != 206:
                log.info("server ignored Range for %s; restarting", url)
            with resp, part.open(mode) as fh:
                for block in resp.iter_content(1 << 16):
                    fh.write(block)
            try:
                if validate:
                    validate(part)
                break
            except Exception as err:
                part.unlink(missing_ok=True)
                if mode == "ab":
                    log.warning("resumed download of %s failed validation (%s); retrying from scratch", url, err)
                    continue
                self.manifest.write(url=full_url, params=params, dest=self._rel(dest), cache="miss",
                                    retrieved_utc=utc_now_iso(), http_status=status, error=str(err))
                raise ValueError(f"download from {full_url} failed validation: {err}") from err
        os.replace(part, dest)
        digest = sha256_of(dest)
        size = dest.stat().st_size
        self.manifest.write(url=full_url, params=params, dest=self._rel(dest), cache="miss",
                            retrieved_utc=utc_now_iso(), http_status=status, bytes=size, sha256=digest,
                            attempts=attempts_total)
        log.info("downloaded %s (%d bytes)", self._rel(dest), size)
        return FetchResult(dest, full_url, False, status, size, digest, attempts_total)

    def get_bytes(self, url: str, *, headers: dict[str, str] | None = None) -> tuple[bytes, int]:
        """Small in-memory GET with the same retry policy (used for byte ranges)."""
        resp, _ = self._request(url, headers=headers)
        with resp:
            return resp.content, resp.status_code
