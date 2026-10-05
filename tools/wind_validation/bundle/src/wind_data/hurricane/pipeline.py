"""Pipeline stages. Each reads the previous stage's files and writes its own,
so stages can be run (and re-run) independently from the command line.

=============  ===========================================================
stage          writes (under ``cfg.processed_dir`` unless noted)
=============  ===========================================================
track          track_fixes.csv, track_dense.csv, run_info.json
stations       stations.csv
observations   observations_parsed.csv.gz (as parsed), obs_availability.csv,
               observations_qc.csv.gz (with QC flags), qc_summary.csv
adjust         observations_final.csv.gz (height-adjusted, storm-relative);
               co-located duplicates marked in stations.csv
models         models/<source>/*.nc (cropped), model_files.csv
collocate      observations_averaged.csv.gz (window means, if configured),
               matched.csv.gz (the tidy matched dataset)
evaluate       <run_dir>/tables/*.csv
report         <run_dir>/figures/*.png, <run_dir>/report.md
=============  ===========================================================

Raw downloads live under ``cfg.raw_dir`` (observations, metadata, track)
and ``<data_dir>/raw/{gfs,gdas}`` (model GRIB2), with every request in
``cfg.manifest_path``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from wind_data.hurricane import besttrack, collocate, evaluate, height, models, observations, plots, qc, report
from wind_data.hurricane.besttrack import BestTrack
from wind_data.hurricane.config import Domain, HurricaneConfig, station_key
from wind_data.hurricane.fetch import Fetcher
from wind_data.hurricane.stations import discover_stations, mark_colocated, missing_requests

log = logging.getLogger(__name__)

STAGES = ("track", "stations", "observations", "adjust", "models", "collocate", "evaluate", "report")
_TIME_COLUMNS = {"time", "obs_time", "valid_time", "init_time", "cpa_time", "first", "last", "obs_peak_time",
                 "model_peak_time", "obs_peak_record_time"}


def write_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run the earlier pipeline stage first")
    head = pd.read_csv(path, nrows=0)
    dates = [c for c in head.columns if c in _TIME_COLUMNS]
    ids = {c: str for c in head.columns if c in ("station_id", "key", "colocated_with", "group", "record_id")}
    df = pd.read_csv(path, parse_dates=dates, dtype=ids, low_memory=False, keep_default_na=True)
    for c in ("qc_pass", "calm", "use_in_metrics", "on_track", "scored", "obs_calm"):
        if c in df and df[c].dtype == object:
            df[c] = df[c].map({"True": True, "False": False, True: True, False: False})
    for c in df.columns:  # text columns: empty stays empty, not NaN
        if df[c].dtype == object and c not in dates:
            df[c] = df[c].fillna("")
    return df


@dataclass
class Run:
    cfg: HurricaneConfig
    fetcher: Fetcher = field(init=False)

    def __post_init__(self):
        self.fetcher = Fetcher.from_config(self.cfg)

    def p(self, name: str) -> Path:
        return self.cfg.processed_dir / name

    # ---- shared state ---------------------------------------------------
    def info(self) -> dict:
        path = self.p("run_info.json")
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run the 'track' stage first")
        return json.loads(path.read_text(encoding="utf-8"))

    def track(self) -> BestTrack:
        info = self.info()
        return BestTrack(info["storm_id"], info["name"], read_csv(self.p("track_fixes.csv")), info["track_source"])

    def period(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        info = self.info()
        return pd.Timestamp(info["period_start"]), pd.Timestamp(info["period_end"])

    def domain(self) -> Domain:
        return Domain(**self.info()["domain"])

    # ---- stages ---------------------------------------------------------
    def stage_track(self, overwrite: bool = False) -> dict:
        cfg = self.cfg
        track, prov = besttrack.load_best_track(cfg, self.fetcher, overwrite=overwrite)
        period = besttrack.analysis_period(track, cfg)
        domain = cfg.domain_bounds or track.bounds(cfg.domain_margin_deg)
        write_csv(track.fixes, self.p("track_fixes.csv"))
        write_csv(track.dense(cfg.cpa_resolution_minutes), self.p("track_dense.csv"))
        issues, namesakes = prov.get("crosscheck_issues"), prov.get("namesakes")
        info = {
            "storm_id": track.storm_id, "name": track.name, "basin": cfg.basin, "track_source": track.source,
            "period_start": str(period[0]), "period_end": str(period[1]), "domain": domain,
            "lifecycle": track.lifecycle(), "atcf_file": prov.get("atcf_file"), "atcf_fixes": prov.get("atcf_fixes"),
            "crosscheck_issues": issues.astype(str).to_dict("records") if isinstance(issues, pd.DataFrame) else [],
            "namesakes": namesakes.astype(str).to_dict("records") if isinstance(namesakes, pd.DataFrame) else [],
            "config_file": str(cfg.source_file), "run_name": cfg.run_name,
        }
        self.p("run_info.json").parent.mkdir(parents=True, exist_ok=True)
        self.p("run_info.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
        log.info("%s %s: %d fixes %s..%s; period %s..%s", track.storm_id, track.name, len(track.fixes),
                 track.first_fix, track.last_fix, *period)
        return info

    def stage_stations(self) -> pd.DataFrame:
        dense = read_csv(self.p("track_dense.csv"))
        st = discover_stations(self.cfg, self.fetcher, dense, self.domain(), self.period())
        if self.cfg.max_stations_per_source:
            st = st.sort_values("cpa_distance_km").groupby("source").head(self.cfg.max_stations_per_source)
            st = st.reset_index(drop=True)
        write_csv(st, self.p("stations.csv"))
        # Grow the model domain so every selected station gets model values.
        info = self.info()
        domain = self.domain().expanded_to(st["lat"], st["lon"], self.cfg.station_margin_deg)
        info["domain"] = domain.as_dict()
        info["missing_requested_stations"] = missing_requests(self.cfg, st)
        self.p("run_info.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
        log.info("stations: %s; domain %s", st.groupby(["source", "station_set"]).size().to_dict(), info["domain"])
        if info["missing_requested_stations"]:
            log.warning("requested stations not found in any station list: %s", info["missing_requested_stations"])
        return st

    def stage_observations(self) -> pd.DataFrame:
        st = read_csv(self.p("stations.csv"))
        parsed, availability = observations.download_and_read(self.cfg, self.fetcher, st, self.period())
        write_csv(parsed, self.p("observations_parsed.csv.gz"))
        write_csv(availability, self.p("obs_availability.csv"))
        cleaned = qc.clean(parsed)
        write_csv(cleaned, self.p("observations_qc.csv.gz"))
        write_csv(qc.summary(cleaned), self.p("qc_summary.csv"))
        log.info("observations: %d records from %d stations (%d pass QC)", len(cleaned),
                 cleaned[["source", "station_id"]].drop_duplicates().shape[0], int(cleaned["qc_pass"].sum()))
        return cleaned

    def stage_adjust(self) -> pd.DataFrame:
        st = read_csv(self.p("stations.csv"))
        obs = read_csv(self.p("observations_qc.csv.gz"))
        passing = obs[obs["qc_pass"]]
        has_wind = {station_key(a, b) for a, b in zip(passing["source"], passing["station_id"])}
        st = mark_colocated(st, self.cfg.colocated_km, self.cfg.source_priority, has_wind)
        write_csv(st, self.p("stations.csv"))
        adj = height.apply(obs, st, self.cfg)
        adj = collocate.storm_relative(adj, self.track(), st, self.cfg, time_col="time")
        adj["key"] = [station_key(s, i) for s, i in zip(adj["source"], adj["station_id"])]
        write_csv(adj, self.p("observations_final.csv.gz"))
        log.info("height adjustment: %s", adj.groupby("adj_flag").size().to_dict())
        return adj

    def stage_models(self, overwrite: bool = False) -> pd.DataFrame:
        files = models.plan(self.cfg, self.period())
        log.info("models: %d files planned (%s)", len(files), files.groupby("source").size().to_dict())
        fetcher = Fetcher.from_config(self.cfg, min_interval_s=self.cfg.model_min_request_interval_s)
        result = models.fetch_all(self.cfg, fetcher, files, self.domain(), overwrite=overwrite)
        write_csv(result, self.p("model_files.csv"))
        return result

    def stage_collocate(self) -> pd.DataFrame:
        files = read_csv(self.p("model_files.csv"))
        st = read_csv(self.p("stations.csv"))
        obs = read_csv(self.p("observations_final.csv.gz"))
        if self.cfg.obs_average_minutes:
            obs = collocate.average_observations(obs, self.cfg.obs_average_minutes,
                                                 self.cfg.obs_average_min_coverage)
            write_csv(obs, self.p("observations_averaged.csv.gz"))
            log.info("observations: %d %g-min means", len(obs), self.cfg.obs_average_minutes)
        matched = collocate.collocate(files, st, obs, self.track(), self.cfg)
        write_csv(matched, self.p("matched.csv.gz"))
        log.info("collocation: %d rows, %d matched", len(matched), int((matched["match_flag"] == "matched").sum()))
        return matched

    def _evaluation(self) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
        matched = read_csv(self.p("matched.csv.gz"))
        obs = read_csv(self.p("observations_final.csv.gz"))
        st = read_csv(self.p("stations.csv"))
        return evaluate.evaluate(matched, obs, st, self.cfg, self.period()), obs, st

    def stage_evaluate(self) -> dict:
        ev, _, _ = self._evaluation()
        write_csv(ev["summary"], self.cfg.tables_dir / "summary_metrics.csv")
        for name in ("metrics", "peaks", "peak_summary", "direction_errors", "coverage"):
            write_csv(ev[name], self.cfg.tables_dir / f"{name}.csv")
        return ev

    def stage_report(self) -> Path:
        ev, obs, st = self._evaluation()
        avail = read_csv(self.p("obs_availability.csv"))
        averaged = read_csv(self.p("observations_averaged.csv.gz")) if self.cfg.obs_average_minutes else None
        figures = plots.make_all(ev, obs, st, self.track(), self.domain(), self.period(), self.cfg, avail,
                                 obs_averaged=averaged)
        path = report.write(self, ev, obs, st, figures)
        log.info("report: %s (%d figures)", path, len(figures))
        return path

    def run(self, stages=STAGES, overwrite: bool = False) -> None:
        for stage in stages:
            log.info("=== stage: %s", stage)
            fn = getattr(self, f"stage_{stage}")
            fn(overwrite=overwrite) if stage in ("track", "models") else fn()
