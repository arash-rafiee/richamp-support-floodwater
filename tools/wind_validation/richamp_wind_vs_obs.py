#!/usr/bin/env python3
"""Land-friction GDAS/GFS winds vs observed 10-m wind at stations: metrics and figures.

Two subcommands, both run by 02_friction_compare.sh:

  observations  fetch the observed winds of the stations in a preset, quality-control them and adjust
                them to 10 m (wind_obs.py)
  compare       join the model winds at the stations (extract_station_wind.py CSVs) with the 1-h mean
                observations, compute statistics and draw the figures in the format of the gfs-run-vs-obs
                skill (station time series with the "Full-period statistics" table, error-by-day bars,
                overview grid, station map)

Model series: "GDAS" and "GFS" are the land-friction RICHAMP winds (scale_and_subset.py). With
--show-raw, "GDAS raw" and "GFS raw" (the MetGet winds before land friction) are added as thin dashed
lines and table rows. Stations outside the RICHAMP grid have raw winds only; those are always drawn
and labelled so.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

import plot_style as ps  # noqa: E402
import wind_obs  # noqa: E402

PRODUCTS = ("gdas", "gfs")
NAMES = {"gdas": "GDAS", "gfs": "GFS"}
INK, INK2, GRID, OBS_C = "#0b0b0b", "#52514e", "#e4e3df", "#0b0b0b"
REGION_COLORS = ["#1baf7a", "#4a3aa7", "#eda100", "#e87ba4", "#008300", "#e34948"]  # not GFS blue / GDAS orange
TYPE_MARKERS = {"ndbc": ("^", "NDBC buoy / C-MAN"), "coops": ("s", "NOAA CO-OPS"),
                "ghcnh": ("D", "Airport (GHCNh, not height-adjusted)")}
RICHAMP_BOX = (-71.9, -71.10833, 41.14167, 42.04167)  # lon0, lon1, lat0, lat1 of NLCD_z0_RICHAMP_Reg_Grid.nc
OUTSIDE_NOTE = "outside RICHAMP grid, no land friction"  # short: the subtitle must clear the statistics table
GDAS_C, GFS_C = ps.GDAS, ps.GFS
TS_RAW = {p: {**ps.TS_STYLE[p], "linewidth": 1.4, "linestyle": (0, (2.5, 1.8)), "alpha": 0.9, "zorder": 3}
          for p in PRODUCTS}


def load_preset(path: Path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    regions = {r: [s["key"] for s in ss] for r, ss in spec["regions"].items()}
    names = {s["key"]: s["name"] for ss in spec["regions"].values() for s in ss if s.get("name")}
    return regions, names


# ---------------------------------------------------------------- subcommand: observations
def cmd_observations(args):
    regions, _ = load_preset(args.stations_file)
    keys = [k for ss in regions.values() for k in ss]
    wind_obs.fetch_observations(keys, pd.Timestamp(args.start), pd.Timestamp(args.end), args.obs_dir)
    print(pd.read_csv(args.obs_dir / "obs_availability.csv").to_string(index=False))


# ---------------------------------------------------------------- statistics
def score(g: pd.DataFrame, series, min_dir: float) -> dict:
    """n, bias, MAE, RMSE, r and direction bias/MAE of each model series against obs_speed/obs_dir."""
    out = {}
    for s in series:
        sp, di = g[f"{s}_speed"], g[f"{s}_dir"]
        ok = g["obs_speed"].notna() & sp.notna()
        e = sp[ok] - g.loc[ok, "obs_speed"]
        dm = ok & (g["obs_speed"] >= min_dir) & (sp >= min_dir) & g["obs_dir"].notna() & di.notna()
        de = (di[dm] - g.loc[dm, "obs_dir"] + 180) % 360 - 180
        with np.errstate(all="ignore"):
            r = np.corrcoef(sp[ok], g.loc[ok, "obs_speed"])[0, 1] if ok.sum() > 2 else np.nan
        out |= {f"{s}_n": int(ok.sum()), f"{s}_bias": e.mean() if len(e) else np.nan,
                f"{s}_mae": e.abs().mean() if len(e) else np.nan,
                f"{s}_rmse": float(np.sqrt((e ** 2).mean())) if len(e) else np.nan, f"{s}_r": r,
                f"{s}_dir_bias": de.mean() if len(de) else np.nan, f"{s}_dir_mae": de.abs().mean() if len(de) else np.nan}
    return out


def grouped(df: pd.DataFrame, by: list[str], series, min_dir: float) -> pd.DataFrame:
    rows = []
    for k, g in df.groupby(by, sort=False, dropna=False):  # keep stations outside the grid (z0 NaN)
        k = k if isinstance(k, tuple) else (k,)
        rows.append({**dict(zip(by, k)), **score(g, series, min_dir)})
    return pd.DataFrame(rows)


def station_type(s) -> str:
    if s.source == "coops":
        return "CO-OPS"
    if s.source == "ghcnh":
        return "Airport (GHCNh)"
    return "NDBC buoy" if "buoy" in str(getattr(s, "platform_type", "")).lower() else "C-MAN"


# ---------------------------------------------------------------- figures: station time series
def ts_legend(fig, obs_height: str, minutes: int, products, friction: bool, raw: bool) -> None:
    """The skill's legend row, plus the raw (no land friction) lines when drawn."""
    mean = f"{minutes // 60:g}-h" if minutes % 60 == 0 else f"{minutes:g}-min"
    entries = [(ps.TS_STYLE["obs_raw"], f"Observed ({obs_height}, unaveraged)"),
               (ps.TS_STYLE["obs"], f"Observed ({obs_height}, {mean} mean)")]
    if friction:
        entries += [(ps.TS_STYLE[p], f"{NAMES[p]} (with land friction)") for p in products]
    if raw:
        entries += [(TS_RAW[p], f"{NAMES[p]} raw (no land friction)") for p in products]
    handles = [Line2D([], [], **{k: v for k, v in st.items() if k != "zorder"}, label=lab) for st, lab in entries]
    ncol = len(handles) if len(handles) <= 4 else math.ceil(len(handles) / 2)  # columns fill top-down: pairs
    fig.legend(handles=handles, loc="outside lower center", ncol=ncol, frameon=False,
               fontsize=ps.TS_FONT["legend"], handlelength=3.0, handletextpad=0.6, columnspacing=2.4,
               borderaxespad=0.2, labelcolor=ps.TS_INK)


def caption(s, o, xlim, zoom, products, show_raw, inside, notes) -> str:
    fmt = "%d %b %H:%M"
    where = ps.name_parts(s)[1]
    loc = f"{abs(s.lat):.2f}°{'N' if s.lat >= 0 else 'S'}, {abs(s.lon):.2f}°{'E' if s.lon >= 0 else 'W'}" + (
        f"; {where}" if where else "")
    parts = [f"Wind speed at {ps.SOURCE_NAMES.get(s.source, s.source)} {s.station_id} ({loc}), "
             f"{xlim[0]:{fmt}} – {xlim[1]:{fmt}} UTC {xlim[1]:%Y}."]
    if zoom is not None:
        parts.append(f"(a) Full analysis period; (b) {zoom[0]:{fmt}} – {zoom[1]:{fmt}} UTC, centred on the "
                     "observed peak (shaded interval in a).")
    z, z0 = o["z_sensor_m"].dropna(), o["z0_m"].dropna()
    if len(z) and o["wind_speed_adj"].notna().any():
        height_txt = (f"observed sustained speed adjusted from the {z.iloc[0]:.1f}-m sensor height to "
                      f"{wind_obs.TARGET_HEIGHT_M:g} m with the neutral logarithmic wind profile"
                      + (f" (z0 = {z0.iloc[0]:g} m)" if len(z0) else ""))
    else:
        height_txt = "observed sustained speed at sensor height (height unknown, not adjusted)"
    parts.append(f"Observations: {height_txt}, then averaged. Dashed black: {wind_obs.AVERAGE_MINUTES:g}-min means "
                 f"centred on each model hour (windows with less than {wind_obs.AVERAGE_MIN_COVERAGE:.0%} of the "
                 "expected samples omitted); gray: the same observations before averaging. Model–observation "
                 "statistics use the means.")
    if len(o) and o["time"].max() < xlim[1] - pd.Timedelta(hours=3):
        parts.append(f"No quality-controlled observations after {o['time'].max():{fmt}} UTC.")
    src = "; ".join(f"{NAMES[p]} = {notes[p]}" for p in products if notes.get(p))
    if inside:
        parts.append(f"Models ({src}): MetGet 0.1° 10-m winds put through RICHAMP land friction (scale_and_subset.py "
                     "up-down: lifted to 80 m with the coarse roughness, interpolated to the 30 m NLCD grid, brought "
                     "back to 10 m with the upwind directional roughness), then bilinear to the station."
                     + (" Thin dashed: the same MetGet winds before land friction (raw)." if show_raw else ""))
    else:
        parts.append(f"Models ({src}): MetGet 0.1° 10-m winds, bilinear to the station, without land friction "
                     "(the station is outside the RICHAMP grid).")
    return " ".join(parts + ["Statistics: the whole period, model vs the 1-h mean observations."])


def fig_station_timeseries(s, name, o, oh, h, start, end, products, show_raw, out, zoom_too, notes):
    xlim = (start, end)
    inside = bool(h["in_richamp"].any())
    draw_raw = show_raw or not inside
    g = h.sort_values("valid")
    models = [(g["valid"], g[f"{p}_speed"], ps.TS_STYLE[p]) for p in products] if inside else []
    if draw_raw:
        models += [(g["valid"], g[f"{p}_raw_speed"], TS_RAW[p]) for p in products]
    model_t = pd.concat([m[0] for m in models], ignore_index=True)
    model_v = pd.concat([m[1] for m in models], ignore_index=True)
    obs_col, obs_label = ps.obs_column(o)
    _, subtitle = ps.station_text(s, adjusted=obs_col == "wind_speed_adj")
    title = f"Wind-speed comparison at {name}"
    if not inside:
        subtitle += f" · {OUTSIDE_NOTE}"
    ob = oh.set_index("time")[obs_col]
    sc = {}
    rows = ([(f"{NAMES[p]} modified", f"{p}_speed") for p in products] if inside else []) + \
           ([(f"{NAMES[p]} raw", f"{p}_raw_speed") for p in products] if draw_raw else [])
    for nm, col in rows:
        j = pd.concat([h.set_index("valid")[col].rename("m"), ob.rename("o")], axis=1, join="inner").dropna()
        e = j.m - j.o
        sc[nm] = {"n": len(j), "bias": e.mean(), "mae": e.abs().mean(), "rmse": np.sqrt((e ** 2).mean()),
                  "correlation": j.m.corr(j.o) if len(j) > 2 else np.nan}
    sc = pd.DataFrame(sc).T
    ytop = max(ps.TS_YMAX, math.ceil(np.nanmax(np.r_[o[obs_col].to_numpy(float), model_v.to_numpy(float), 0.0]) + 1))
    base = f"timeseries_{s.source}_{s.station_id}"

    def panel(ax, window, top, zoom):
        ps.speed_panel(ax, o, oh, models, window, top, zoom)
        if not zoom and (end - start) <= pd.Timedelta(days=8):
            ax.xaxis.set_major_locator(mdates.DayLocator())

    fig, ax = plt.subplots(figsize=(14, 6), layout="constrained")
    fig.patch.set_facecolor("white")
    fig.get_layout_engine().set(h_pad=0.03, w_pad=0.04)
    panel(ax, xlim, ytop, False)
    ps.header(ax, title, subtitle)
    ps.stats_table(ax, sc)
    ts_legend(fig, obs_label, wind_obs.AVERAGE_MINUTES, products, inside, draw_raw)
    p = out / f"{base}.png"
    fig.savefig(p, dpi=300, facecolor="white")
    plt.close(fig)
    ps.write_caption(p, caption(s, o, xlim, None, products, show_raw, inside, notes))
    if not zoom_too:
        return
    zoom = ps.zoom_window(oh if len(oh) else o, model_t, model_v, xlim)
    fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(14, 10.5), layout="constrained")
    fig.patch.set_facecolor("white")
    fig.get_layout_engine().set(h_pad=0.03, w_pad=0.04, hspace=0.06)
    panel(ax_a, xlim, ytop, False)
    zv = np.r_[o.loc[o.time.between(*zoom), obs_col].to_numpy(float),
               model_v[model_t.between(*zoom)].to_numpy(float), 0.0]
    panel(ax_b, zoom, max(ps.TS_ZOOM_YMAX, math.ceil(np.nanmax(zv) + 1)), True)
    ax_a.axvspan(*zoom, **ps.TS_ZOOM_SHADE)
    ax_a.annotate("Panel (b) interval", xy=(zoom[0], 1), xycoords=("data", "axes fraction"), xytext=(4, -4),
                  textcoords="offset points", ha="left", va="top", fontsize=ps.TS_FONT["note"] - 1,
                  color=ps.TS_INK_MUTED)
    ps.header(ax_a, title, subtitle, tag=fr"$\bf{{(a)}}$ {start:%d %b %H}Z – {end:%d %b %H}Z")
    ps.stats_table(ax_a, sc)
    ps.header(ax_b, None, None, tag=fr"$\bf{{(b)}}$ Detailed comparison: {zoom[0]:%b %d %H:%M}–{zoom[1]:%b %d %H:%M} UTC")
    ts_legend(fig, obs_label, wind_obs.AVERAGE_MINUTES, products, inside, draw_raw)
    p = out / f"{base}_zoom.png"
    fig.savefig(p, dpi=300, facecolor="white")
    plt.close(fig)
    ps.write_caption(p, caption(s, o, xlim, zoom, products, show_raw, inside, notes))


# ---------------------------------------------------------------- figures: error by day, overview, map
def bar_series(products, has_friction: bool, show_raw: bool):
    """(column prefix, label, colour, hatch) of the bars to draw for one panel."""
    out = [(p, NAMES[p], {"gdas": GDAS_C, "gfs": GFS_C}[p], None) for p in products] if has_friction else []
    if show_raw or not has_friction:
        out += [(f"{p}_raw", f"{NAMES[p]} raw (no land friction)", {"gdas": GDAS_C, "gfs": GFS_C}[p], "///")
                for p in products]
    return out


def bars(ax, x, groups, g, stat, fmt):
    """The skill's bar_pair, for any number of series; raw series hatched and lighter."""
    width = 0.72 / len(groups)
    for k, (pre, lab, color, hatch) in enumerate(groups):
        off = (k - (len(groups) - 1) / 2) * width
        vals = g[f"{pre}_{stat}"].to_numpy(float)
        ax.bar(x + off, vals, width * 0.95, color=color, alpha=0.45 if hatch else 1.0, hatch=hatch,
               edgecolor=color if hatch else None, label=lab)
        crowded = len(groups) > 2  # vertical value labels so neighbouring bars' labels do not overlap
        for xx, v in zip(x + off, vals):
            if np.isfinite(v):
                ax.annotate(fmt.format(v), (xx, v), xytext=(0, 3 if v >= 0 else -3), textcoords="offset points",
                            ha="center", va="bottom" if v >= 0 else "top", fontsize=7.5 if crowded else 8.5,
                            rotation=90 if crowded else 0, color=INK)
    ax.axhline(0, color=ps.TS_AXIS, lw=.8)
    ax.grid(True, axis="y", color=GRID, lw=.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    lo, hi = ax.get_ylim()
    pad = .08 * (hi - lo)
    ax.set_ylim(min(lo, 0) - (pad if lo < 0 else 0), hi + pad)


def day_labels(start, ndays):
    def span(d):
        a, b = start + pd.Timedelta(days=d - 1), start + pd.Timedelta(days=d)
        return f"{a:%d}–{b:%d %b}" if a.month == b.month else f"{a:%d %b}–{b:%d %b}"
    return [f"Day {d}\n{span(d)}" for d in range(1, ndays + 1)]


def has_friction(g: pd.DataFrame, products) -> bool:
    return any(g.get(f"{p}_n", pd.Series(dtype=float)).fillna(0).sum() > 0 for p in products)


def fig_group_days(stats_day, column, groups, counts, start, ndays, products, show_raw, title, path):
    x = np.arange(1, ndays + 1)
    fig, axes = plt.subplots(2, len(groups), figsize=(4.4 * len(groups) + 1, 6.4), squeeze=False)
    handles = {}
    for j, r in enumerate(groups):
        g = stats_day[stats_day[column] == r].set_index("day").reindex(x)
        ser = bar_series(products, has_friction(g, products), show_raw)
        bars(axes[0, j], x, ser, g, "rmse", "{:.1f}")
        bars(axes[1, j], x, ser, g, "bias", "{:+.1f}")
        n = counts[r]
        axes[0, j].set_title(f"{r}  ({n} station{'s' if n > 1 else ''})", loc="left", fontsize=10, color=INK,
                             fontweight="bold")
        axes[1, j].set_xticks(x, day_labels(start, ndays), fontsize=7.5)
        axes[0, j].set_xticks(x, [])
        handles.update(zip(*reversed(axes[0, j].get_legend_handles_labels())))
    axes[0, 0].set_ylabel("Speed RMSE (m/s)")
    axes[1, 0].set_ylabel("Speed bias, model − obs (m/s)")
    fig.legend(list(handles.values()), list(handles.keys()), loc="lower center", ncol=min(len(handles), 4),
               frameon=False)
    fig.suptitle(title, x=.01, ha="left", fontsize=12, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, .07, 1, .95))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_station_days(st_day, s, name, start, ndays, n, products, show_raw, out):
    x = np.arange(1, ndays + 1)
    g = st_day[st_day.station_id == s.station_id].set_index("day").reindex(x)
    inside = has_friction(g, products)
    ser = bar_series(products, inside, show_raw)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.6), layout="constrained")
    fig.patch.set_facecolor("white")
    for ax, stat, lab, fmt in ((axes[0], "rmse", "RMSE (m s$^{-1}$)", "{:.1f}"),
                               (axes[1], "bias", "Bias, model − obs (m s$^{-1}$)", "{:+.1f}")):
        bars(ax, x, ser, g, stat, fmt)
        ax.set_xticks(x, day_labels(start, ndays))
        ax.set_ylabel(lab, fontsize=ps.TS_FONT["label"], color=INK)
        ax.tick_params(labelsize=ps.TS_FONT["tick"] - 1, colors=ps.TS_AXIS, labelcolor=INK)
    fig.suptitle(f"Wind-speed error by day: {name}", x=.01, ha="left", fontsize=ps.TS_FONT["title"],
                 fontweight="bold", color="black")
    obs_txt = "observations at sensor height (not adjustable)" if s.source == "ghcnh" else "observations adjusted to 10 m"
    src = ps.SOURCE_NAMES.get(s.source, s.source.upper())
    note = "" if inside else f" · {OUTSIDE_NOTE}"
    axes[0].set_title(f"{src} {s.station_id} · {abs(s.lat):.2f}°N, {abs(s.lon):.2f}°W · vs 1-h mean {obs_txt} · "
                      f"n = {n}{note}", loc="left", fontsize=ps.TS_FONT["subtitle"] - 1,
                      color=ps.TS_INK_MUTED, pad=10)
    fig.legend(*axes[0].get_legend_handles_labels(), loc="outside lower center", ncol=min(len(ser), 4),
               frameon=False, fontsize=ps.TS_FONT["legend"])
    fig.savefig(out / f"error_by_day_{s.source}_{s.station_id}.png", dpi=200, facecolor="white")
    plt.close(fig)


def fig_overview(df, regions, names, unadj, outside, start, end, by_station, products, show_raw, out):
    ncol = 3
    nrow_per = {r: math.ceil(len(ss) / ncol) for r, ss in regions.items()}
    nrow = sum(nrow_per.values())
    plt.rcParams.update({"font.size": 8.5, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(nrow, ncol, figsize=(15, 2.55 * nrow + 1), sharex=True, sharey=True, squeeze=False)
    cols = ["obs_speed"] + [f"{p}_speed" for p in products] + [f"{p}_raw_speed" for p in products]
    ymax = np.nanmax(df[cols].values) * 1.08
    color = {"gdas": GDAS_C, "gfs": GFS_C}
    bs = by_station.set_index("station_id")
    row0 = 0
    for r, ss in regions.items():
        for k in range(nrow_per[r] * ncol):
            ax = axes[row0 + k // ncol, k % ncol]
            if k >= len(ss):
                ax.set_visible(False)
                continue
            s = ss[k]
            g = df[df.station_id == s]
            inside = s not in outside
            stat_lines = []
            for p in products:
                if inside:
                    ax.plot(g.valid, g[f"{p}_speed"], color=color[p], lw=1.6, label=NAMES[p])
                    stat_lines.append(f"{NAMES[p]} RMSE {bs.loc[s, f'{p}_rmse']:.1f}  bias {bs.loc[s, f'{p}_bias']:+.1f}")
                if show_raw or not inside:
                    ax.plot(g.valid, g[f"{p}_raw_speed"], color=color[p], lw=1.0, ls=(0, (2.5, 1.8)), alpha=.9,
                            label=f"{NAMES[p]} raw")
                    if not inside:
                        stat_lines.append(f"{NAMES[p]} raw RMSE {bs.loc[s, f'{p}_raw_rmse']:.1f}  "
                                          f"bias {bs.loc[s, f'{p}_raw_bias']:+.1f}")
            ax.plot(g.valid, g.obs_speed, color=OBS_C, lw=0, marker="o", ms=2.6, label="Observed (1-h mean)")
            ax.text(.99, .97, "\n".join(stat_lines) + " m/s", transform=ax.transAxes, ha="right", va="top",
                    fontsize=7.3, color=INK2, linespacing=1.4)
            title = names[s] + (" — obs as reported, not height-adjusted" if s in unadj else "") + \
                (" — raw model (outside RICHAMP grid)" if not inside else "")
            ax.set_title(title if k else f"{r.upper()}\n{title}", loc="left", fontsize=9, color=INK,
                         fontweight="normal" if k else "bold")
            ax.grid(color=GRID, lw=.6)
            for d in range(1, math.ceil((end - start) / pd.Timedelta("1D"))):
                ax.axvline(start + pd.Timedelta(days=d), color=GRID, lw=1.2, zorder=0)
            if k % ncol == 0:
                ax.set_ylabel("Wind speed (m/s)")
        row0 += nrow_per[r]
    axes[0, 0].set_ylim(0, ymax)
    handles = {}
    for ax in axes.flat:
        if ax.get_visible():
            handles.update(zip(*reversed(ax.get_legend_handles_labels())))
    axes[0, 0].legend(list(handles.values()), list(handles.keys()), frameon=False, loc="upper left", fontsize=7.5)
    for ax in axes[-1]:
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax.set_xlim(start, end)
    what = " and ".join(NAMES[p] for p in products)
    fig.suptitle(f"10-m wind speed: land-friction {what} vs observations, {start:%Y-%m-%d %H}Z – {end:%Y-%m-%d %H}Z",
                 x=.01, y=.995, ha="left", fontsize=12, color=INK, fontweight="bold")
    fig.text(.01, .975, "Observations: QC-passed 1-h means centred on the hour, adjusted to 10 m (airports: as "
                        "reported). Models: RICHAMP land-friction winds, bilinear to the station"
                        + ("; dashed: raw MetGet winds" if show_raw else "") + ". Vertical lines: days.",
             fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, .965))
    fig.savefig(out / "overview_timeseries.png", dpi=140)
    plt.close(fig)


def fig_station_map(st, regions, names, start, end, out, cache):
    """The skill's station map, with the RICHAMP land-friction grid outlined and the 0.1° MetGet grid points."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.ticker as mticker
    from matplotlib.patches import Rectangle

    proj = ccrs.PlateCarree()
    st = st.set_index("station_id")
    reg_of = {s: r for r, ss in regions.items() for s in ss}
    color = {r: REGION_COLORS[i % len(REGION_COLORS)] for i, r in enumerate(regions)}

    def extent_of(ids, pad_frac, pad_min):
        lo, la = st.loc[ids, "lon"], st.loc[ids, "lat"]
        span = max(lo.max() - lo.min(), la.max() - la.min(), 0.05)
        p = max(pad_min, pad_frac * span)
        return [lo.min() - p, lo.max() + p, la.min() - 0.75 * p, la.max() + 0.75 * p]

    ids = list(st.index)
    main_ext = extent_of(ids, 0.12, 0.25)
    main_span = main_ext[1] - main_ext[0]
    detail = None
    for r, ss in regions.items():
        if len(ss) >= 3:
            e = extent_of(ss, 0.2, 0.06)
            if (e[1] - e[0]) < 0.45 * main_span and (detail is None or (e[1] - e[0]) < (detail[1][1] - detail[1][0])):
                detail = (r, e)
    extents = [main_ext] + ([detail[1]] if detail else [])
    ratios = [(e[1] - e[0]) / (e[3] - e[2]) * 0.75 for e in extents]
    width = 21 if detail else 15
    fig = plt.figure(figsize=(width, width / (sum(ratios) + 0.3)), layout="constrained")
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(1, len(extents), width_ratios=ratios)
    fine = ps.census_states(cache)
    axes = []
    for k, ext in enumerate(extents):
        ax = fig.add_subplot(gs[0, k], projection=proj)
        ax.set_extent(ext, crs=proj)
        ax.set_aspect(1 / np.cos(np.deg2rad(np.mean(ext[2:]))))
        if fine:
            ax.add_geometries(fine, crs=proj, facecolor=ps.MAP_LAND, edgecolor=ps.MAP_COAST, lw=0.6, zorder=0)
        else:
            ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor=ps.MAP_LAND, edgecolor="none", zorder=0)
            ax.add_feature(cfeature.COASTLINE.with_scale("10m"), lw=0.6, edgecolor=ps.MAP_COAST, zorder=1)
        glon = np.arange(np.floor(ext[0] * 10) / 10, ext[1] + 0.1, 0.1)
        glat = np.arange(np.floor(ext[2] * 10) / 10, ext[3] + 0.1, 0.1)
        gx, gy = np.meshgrid(glon, glat)
        ax.scatter(gx, gy, marker="+", s=20 if k else 8, lw=0.5, color="#b0b0b0", zorder=2, transform=proj)
        b = RICHAMP_BOX
        ax.add_patch(Rectangle((b[0], b[2]), b[1] - b[0], b[3] - b[2], fill=False, ec="#c0392b", lw=1.4, ls="-",
                               zorder=4, transform=proj))
        step = ps.nice_step(ext[1] - ext[0], 6)
        gl = ax.gridlines(draw_labels=True, color=ps.MAP_GRID, lw=0.5, zorder=0.5,
                          xlocs=mticker.MultipleLocator(step), ylocs=mticker.MultipleLocator(step))
        gl.top_labels = gl.right_labels = False
        gl.xlabel_style = gl.ylabel_style = {"color": ps.MAP_INK_SOFT, "size": 9}
        size = 95 if k else 60
        pts = []
        for sid in ids:
            s = st.loc[sid]
            lo, la = float(s["lon"]), float(s["lat"])
            if not (ext[0] < lo < ext[1] and ext[2] < la < ext[3]):
                continue
            in_detail = detail is not None and k == 0 and reg_of[sid] == detail[0]
            mk = TYPE_MARKERS.get(s["source"], ("o", ""))[0]
            ax.scatter(lo, la, marker=mk, s=size * (1.7 if mk == "^" else 1.0), color=color[reg_of[sid]],
                       edgecolor="white", lw=1.2, zorder=5, transform=proj)
            if not in_detail:
                pts.append((names[sid], lo, la, np.sqrt(size) / 2 + 1, False))
        axes.append((ax, pts))
    if detail:
        e = detail[1]
        axes[0][0].add_patch(Rectangle((e[0], e[2]), e[1] - e[0], e[3] - e[2], fill=False, ec=ps.MAP_BOX, lw=1.0,
                                       ls="--", zorder=6, transform=proj))
        axes[0][0].annotate("(b)", (e[0], e[3]), xytext=(2, 3), textcoords="offset points", fontsize=10,
                            color=ps.MAP_BOX, transform=proj, zorder=6)
    for ax, pts in axes:
        if pts:
            ps.label_stations(ax, fig, pts, fontsize=10, transform_kw={"transform": proj})
    axes[0][0].set_title(r"$\bf{(a)}$ All stations" + (f" (box: {detail[0]}, panel b)" if detail else ""),
                         loc="left", fontsize=11, color=INK)
    if detail:
        axes[1][0].set_title(fr"$\bf{{(b)}}$ {detail[0]}", loc="left", fontsize=11, color=INK)
    used_types = [t for t in TYPE_MARKERS if t in set(st["source"])]
    handles = [Line2D([], [], marker="o", ls="", ms=9, color=color[r], label=f"{r} ({len(ss)})")
               for r, ss in regions.items()]
    handles += [Line2D([], [], marker=TYPE_MARKERS[t][0], ls="", ms=8, color="#777777", label=TYPE_MARKERS[t][1])
                for t in used_types]
    handles.append(Line2D([], [], marker="+", ls="", ms=8, color="#b0b0b0", label="MetGet 0.1° grid point"))
    handles.append(Line2D([], [], color="#c0392b", lw=1.4, label="RICHAMP land-friction grid (30 m)"))
    fig.legend(handles=handles, loc="outside lower center", ncol=min(len(handles), 4), frameon=False, fontsize=10)
    fig.suptitle(f"Observation stations: land-friction GDAS/GFS comparison, {start:%Y-%m-%d %H}Z – {end:%Y-%m-%d %H}Z",
                 x=0.01, ha="left", fontsize=14, fontweight="bold", color="black")
    fig.savefig(out / "station_map.png", dpi=200, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- subcommand: compare
def read_station_wind(spec: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """'gdas=path.csv' items -> one wide table (key, valid, <p>_speed/_dir, <p>_raw_speed/_dir, in_richamp)."""
    wide, products, inside, z0 = None, [], set(), {}
    for item in spec:
        p, path = item.split("=", 1)
        if p not in PRODUCTS:
            raise SystemExit(f"--station-wind {item}: product must be one of {PRODUCTS}")
        d = pd.read_csv(path, parse_dates=["valid"], dtype={"station_id": str})
        inside |= set(d.loc[d.in_richamp.astype(str).eq("True"), "key"])
        z0 |= d.dropna(subset=["z0_local_m"]).groupby("key").z0_local_m.first().to_dict()
        d = d.rename(columns={"fric_speed": f"{p}_speed", "fric_dir": f"{p}_dir",
                              "raw_speed": f"{p}_raw_speed", "raw_dir": f"{p}_raw_dir"})
        d = d[["key", "valid", f"{p}_speed", f"{p}_dir", f"{p}_raw_speed", f"{p}_raw_dir"]]
        wide = d if wide is None else wide.merge(d, on=["key", "valid"], how="outer")
        products.append(p)
    wide["in_richamp"] = wide.key.isin(inside)
    wide["z0_local_m"] = wide.key.map(z0)
    return wide, [p for p in PRODUCTS if p in products]


def cmd_compare(args):
    notes = dict(n.split("=", 1) for n in args.source_note)
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    out = args.out
    for sub in ("", "stations", "stations_error_by_day"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    regions, names = load_preset(args.stations_file)
    keys = [k for ss in regions.values() for k in ss]

    stations, obs = wind_obs.read_observations(args.obs_dir)
    stations = stations.drop_duplicates("key")
    hourly = wind_obs.average_observations(obs)
    # GHCNh land records have no published anemometer height: used as reported and labelled so
    unadj_ids = set(hourly.loc[hourly.wind_speed_adj.isna() & hourly.wind_speed.notna(), "station_id"])
    hourly["obs_speed"] = hourly["wind_speed_adj"].fillna(hourly["wind_speed"])
    hourly = hourly.rename(columns={"time": "valid", "wind_direction": "obs_dir"})[
        ["station_id", "valid", "obs_speed", "obs_dir"]]

    model, products = read_station_wind(args.station_wind)
    have = set(hourly.loc[hourly.valid.between(start, end), "station_id"])
    key_to_id = dict(zip(stations.key, stations.station_id))
    dropped = [k for k in keys if key_to_id.get(k) not in have or k not in set(model.key)]
    if dropped:
        print("no usable observations or model wind, left out:", ", ".join(dropped))
    regions = {r: [key_to_id[k] for k in ss if k not in dropped] for r, ss in regions.items()}
    regions = {r: ss for r, ss in regions.items() if ss}
    ids = [s for ss in regions.values() for s in ss]
    st = stations.set_index("station_id").loc[ids].reset_index()
    names = {key_to_id[k]: v for k, v in names.items() if k in key_to_id}
    for s in st.itertuples(index=False):
        names.setdefault(s.station_id, ps.station_text(s)[0].replace("Wind-speed comparison at ", ""))
    unadj = unadj_ids & set(ids)
    st["station_type"] = [station_type(s) for s in st.itertuples(index=False)]

    df = model.merge(st[["key", "station_id", "source", "station_type"]], on="key")
    df = df[df.valid.between(start, end)].merge(hourly, on=["station_id", "valid"], how="left")
    df["region"] = df.station_id.map({s: r for r, ss in regions.items() for s in ss})
    df["name"] = df.station_id.map(names)
    df["hour"] = ((df.valid - start) / pd.Timedelta("1h")).round().astype(int)
    df["day"] = np.clip((df["hour"] - 1) // 24 + 1, 1, None)
    ndays = int(df["day"].max())
    outside = set(df.loc[~df.in_richamp, "station_id"])
    df.to_csv(out / "hourly_matched.csv", index=False, float_format="%.4f")

    series = [s for p in products for s in (p, f"{p}_raw")]
    md = wind_obs.MIN_SPEED_FOR_DIRECTION
    by_station = grouped(df, ["region", "station_id", "name", "station_type", "in_richamp", "z0_local_m"], series, md)
    st_day = grouped(df, ["region", "station_id", "name", "day"], series, md)
    by_region_day = grouped(df, ["region", "day"], series, md)
    by_type = grouped(df, ["station_type"], series, md)
    by_type_day = grouped(df, ["station_type", "day"], series, md)
    for name, t in (("stats_by_station", by_station), ("stats_by_station_day", st_day),
                    ("stats_by_region_day", by_region_day), ("stats_by_type", by_type),
                    ("stats_by_type_day", by_type_day)):
        t.round(3).to_csv(out / f"{name}.csv", index=False)
    pd.set_option("display.width", 240)
    show = ["region", "name", "in_richamp"] + [f"{s}_{m}" for s in series for m in ("n", "bias", "rmse", "r")]
    print(by_station[show].round(2).to_string(index=False))

    print("== figures", flush=True)
    try:
        fig_station_map(st, regions, names, start, end, out, args.obs_dir / "raw" / "basemap")
    except ImportError as err:
        print("station map skipped (needs cartopy):", err)
    fig_overview(df, regions, names, unadj, outside, start, end, by_station, products, args.show_raw, out)
    fig_group_days(by_region_day, "region", list(regions), {r: len(ss) for r, ss in regions.items()}, start, ndays,
                   products, args.show_raw, "Wind-speed error vs observations by day (stations in the region pooled)",
                   out / "error_by_day_regions.png")
    types = list(dict.fromkeys(st.station_type))
    fig_group_days(by_type_day, "station_type", types, st.station_type.value_counts().to_dict(), start, ndays,
                   products, args.show_raw, "Wind-speed error vs observations by day (stations of a type pooled)",
                   out / "error_by_day_types.png")
    n_by = by_station.set_index("station_id")
    for s in st.itertuples(index=False):
        o = obs[(obs.key == s.key) & obs.qc_pass & obs.time.between(start, end)].sort_values("time")
        oh = wind_obs.average_observations(o)
        oh = oh[oh.time.between(start, end)].sort_values("time")
        h = df[df.station_id == s.station_id]
        fig_station_timeseries(s, names[s.station_id], o, oh, h, start, end, products, args.show_raw,
                               out / "stations", not args.no_zoom, notes)
        n_col = f"{products[0]}_n" if s.station_id not in outside else f"{products[0]}_raw_n"
        fig_station_days(st_day, s, names[s.station_id], start, ndays, int(n_by.loc[s.station_id, n_col]),
                         products, args.show_raw, out / "stations_error_by_day")
        print(f"  {names[s.station_id]}", flush=True)
    print("outputs in", out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("observations", "compare"):
        q = sub.add_parser(name)
        q.add_argument("--stations-file", type=Path, required=True, help="station preset JSON")
        q.add_argument("--start", required=True, help="UTC, e.g. '2026-09-24 12:00'")
        q.add_argument("--end", required=True, help="UTC, e.g. '2026-09-29 12:00'")
    q = sub.choices["observations"]
    q.add_argument("--obs-dir", type=Path, required=True, help="observation folder (raw downloads, stations.csv, ...)")
    q = sub.choices["compare"]
    q.add_argument("--obs-dir", type=Path, required=True, help="observation folder written by the observations step")
    q.add_argument("--station-wind", nargs="+", required=True, metavar="PRODUCT=CSV",
                   help="extract_station_wind.py outputs, e.g. gdas=station_wind_gdas.csv gfs=station_wind_gfs.csv")
    q.add_argument("--out", type=Path, required=True, help="results folder")
    q.add_argument("--show-raw", action="store_true", help="also draw/tabulate the raw (no land friction) winds")
    q.add_argument("--no-zoom", action="store_true", help="skip the two-panel zoom figures")
    q.add_argument("--source-note", nargs="*", default=[], metavar="PRODUCT=TEXT",
                   help="what each product is, for captions, e.g. 'gdas=MetGet --multiple-forecasts'")
    args = p.parse_args(argv)
    cmd_observations(args) if args.cmd == "observations" else cmd_compare(args)


if __name__ == "__main__":
    main()
