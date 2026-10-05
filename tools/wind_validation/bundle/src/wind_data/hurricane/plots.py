"""Figures for the hurricane wind evaluation.

Encoding rules (shared with :mod:`wind_data.plotting._style`):

* Colour identifies the model only: GFS blue, GDAS orange, in every figure.
* Observations are ink, never a series hue: height-adjusted values solid
  and labelled "adjusted to 10 m (log law)", sensor-height values dashed
  and labelled "at sensor height". Adjusted values are never called raw.
* Station class on maps is a marker shape (circle offshore, square coastal,
  triangle land), so no extra hues are needed.
* Magnitude (track intensity, record counts) uses one sequential hue.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from types import SimpleNamespace

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator

from wind_data.hurricane import evaluate
from wind_data.hurricane.geodesy import MOTION_QUADRANTS, band_labels
from wind_data.plotting import _style
from wind_data.plotting.maps import _map_axes

log = logging.getLogger(__name__)

GFS, GDAS = _style.SOURCE_COLORS["gfs"], _style.SOURCE_COLORS["gdas"]


def label_colors(cfg) -> dict[str, str]:
    """The two series shown in single-series figures: hourly GDAS and the GFS composite."""
    return {evaluate.gdas_series(cfg): GDAS, evaluate.GFS_COMPOSITE: GFS}


CLASS_MARKERS = {"offshore": "o", "coastal": "s", "land": "^"}
# Saffir-Simpson thresholds (kt) for colouring the track.
SS_EDGES = [0, 34, 64, 83, 96, 113, 137, 200]
SS_NAMES = ["TD", "TS", "Cat 1", "Cat 2", "Cat 3", "Cat 4", "Cat 5"]


def _save(fig, cfg, name: str, dpi: int = 150) -> Path:
    path = _style.save_figure(fig, cfg.figures_dir / f"{name}.png", dpi=dpi)
    plt.close(fig)
    return path


def _obs_label(adjusted: bool, target: float) -> str:
    return f"observed, adjusted to {target:g} m (log law)" if adjusted else "observed at sensor height"


# ---------------------------------------------------------------------------
# Map and station geometry
# ---------------------------------------------------------------------------
def track_map(track, stations: pd.DataFrame, domain, cfg) -> Path:
    lon = np.array([domain.lon_min, domain.lon_max])
    lat = np.array([domain.lat_min, domain.lat_max])
    try:
        fig, ax, kw = _map_axes(None, lon, lat, coastlines=True)
    except Exception as err:  # no cartopy, or Natural Earth data cannot be fetched
        log.warning("map without coastlines: %s", err)
        fig, ax, kw = _map_axes(None, lon, lat, coastlines=False)
    f = track.fixes
    cmap = _style.SPEED_CMAP
    norm = BoundaryNorm(SS_EDGES, cmap.N)
    ax.plot(f["lon"], f["lat"], color=_style.TEXT_MUTED, linewidth=1.0, zorder=4, **kw)
    sc = ax.scatter(f["lon"], f["lat"], c=f["vmax_kt"], cmap=cmap, norm=norm, s=28, zorder=5,
                    edgecolors=_style.SURFACE, linewidths=0.8, **kw)
    lf = f[f["record_id"] == "L"]
    ax.scatter(lf["lon"], lf["lat"], marker="*", s=140, color=_style.TEXT_PRIMARY, zorder=6, **kw)
    for cls, marker in CLASS_MARKERS.items():
        s = stations[stations["station_class"] == cls]
        ax.scatter(s["lon"], s["lat"], marker=marker, s=22, facecolors="none", edgecolors=_style.TEXT_PRIMARY,
                   linewidths=0.9, zorder=4, **kw)
    cb = fig.colorbar(sc, ax=ax, shrink=0.7, pad=0.02, ticks=[(a + b) / 2 for a, b in zip(SS_EDGES, SS_EDGES[1:])])
    cb.ax.set_yticklabels(SS_NAMES)
    cb.set_label("best-track intensity (1-min sustained, kt)", color=_style.TEXT_SECONDARY)
    handles = [Line2D([], [], marker=m, linestyle="", markerfacecolor="none", markeredgecolor=_style.TEXT_PRIMARY,
                      label=f"{c} station ({(stations['station_class'] == c).sum()})")
               for c, m in CLASS_MARKERS.items()]
    handles.append(Line2D([], [], marker="*", linestyle="", color=_style.TEXT_PRIMARY, markersize=10,
                          label="landfall"))
    ax.legend(handles=handles, frameon=False, loc="lower right", labelcolor=_style.TEXT_SECONDARY, fontsize=8)
    extra = " plus requested stations" if cfg.include else ""
    ax.set_title(f"Hurricane {track.name.title()} ({track.storm_id}): NHC best track; stations within "
                 f"{cfg.radius_km:g} km{extra}; frame = model domain", loc="left", fontsize=10)
    return _save(fig, cfg, "map_track_stations")


# Requested-station map: marker shape = data outcome (no extra hues; colour stays reserved for models).
STATUS_STYLE = {
    "reported wind": {"marker": "o", "filled": True},
    "wave-only buoy (no anemometer)": {"marker": "^", "filled": False},
    "offline all period": {"marker": "s", "filled": False},
    "no data file for the period": {"marker": "X", "filled": True},
    "anemometer failed": {"marker": "D", "filled": False},
}


def station_status(stations: pd.DataFrame, availability: pd.DataFrame | None) -> pd.Series:
    """One of STATUS_STYLE's keys per station, from the observation-stage availability log."""
    st = stations.set_index("key")
    av = availability.set_index("key")["status"] if availability is not None and len(availability) else pd.Series(
        dtype=object)
    out = {}
    for key in st.index:
        status = av.get(key, "")
        ptype = str(st.at[key, "platform_type"]).lower()
        if status == "ok":
            out[key] = "reported wind"
        elif "waverider" in ptype:
            out[key] = "wave-only buoy (no anemometer)"
        elif status == "not available":
            out[key] = "no data file for the period"
        elif status == "no valid wind in period":
            out[key] = "anemometer failed"
        else:
            out[key] = "offline all period"
    return pd.Series(out)


#: Legend wording for each status (publication phrasing; keys stay the internal status names).
STATUS_LABELS = {
    "reported wind": "Wind observations available",
    "wave-only buoy (no anemometer)": "Wave-only buoy (no anemometer)",
    "offline all period": "Offline during analysis period",
    "no data file for the period": "No data file for the period",
    "anemometer failed": "Anemometer failed during period",
}
MAP_LAND, MAP_COAST, MAP_STATE, MAP_GRID = "#ececec", "#8c8c8c", "#b4b4b4", "#e9e9e9"
MAP_INK, MAP_INK_SOFT, MAP_BOX = "#1a1a1a", "#666666", "#444444"
#: Approximate label positions for US states near the Atlantic coast (lat, lon), drawn faintly for orientation.
STATE_LABELS = {
    "ME": (45.3, -69.2), "NH": (43.6, -71.6), "VT": (44.0, -72.7), "MA": (42.35, -72.0), "RI": (41.62, -71.62),
    "CT": (41.6, -72.7), "NY": (42.9, -75.5), "NJ": (40.2, -74.6), "PA": (40.9, -77.6), "DE": (38.95, -75.5),
    "MD": (39.3, -76.9), "VA": (37.5, -78.6), "NC": (35.5, -79.4), "SC": (33.9, -80.9), "GA": (32.7, -83.4),
    "FL": (28.3, -81.7), "AL": (32.8, -86.8), "MS": (32.7, -89.7), "LA": (31.0, -92.0), "TX": (31.0, -98.5),
}


CENSUS_STATES_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip"


def _census_states(cfg) -> list | None:
    """US state polygons at 1:500,000, land clipped to the shoreline (Census cartographic boundaries).

    Natural Earth's finest coastline (1:10 million) turns a bay into a few
    straight segments; this resolves one. Cached under ``data/raw/basemap``;
    None when it cannot be fetched or read.
    """
    folder = cfg.data_dir / "raw" / "basemap" / "cb_2023_us_state_500k"
    shp = folder / "cb_2023_us_state_500k.shp"
    try:
        if not shp.exists():
            import io
            import urllib.request
            import zipfile
            with urllib.request.urlopen(CENSUS_STATES_URL, timeout=120) as resp:
                zipfile.ZipFile(io.BytesIO(resp.read())).extractall(folder)
        from cartopy.io import shapereader
        return [r.geometry for r in shapereader.Reader(str(shp)).records()]
    except Exception as err:
        log.warning("Census 1:500k shoreline unavailable (%s); using Natural Earth", err)
        return None


def _nice_step(span: float, target: int = 6) -> float:
    """A round gridline spacing giving about ``target`` lines over ``span`` degrees."""
    for step in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20):
        if span / step <= target:
            return step
    return 30.0


def _label_stations(ax, fig, pts, *, fontsize: float, transform_kw: dict, blocked=()) -> None:
    """Station IDs at consistent offsets, clear of every marker, other labels and the panel edge.

    ``pts``: (label, lon, lat, marker radius in points, bold). Works in
    points after the layout is settled, so offsets do not depend on the map
    scale. Tries eight directions close to the marker, then further out with
    a thin leader line. ``blocked``: extra (x0, y0, x1, y1) boxes in points.
    """
    fig.canvas.draw()  # settle the layout so data -> points is final
    to_pt = 72.0 / fig.dpi
    trans = ax.transData if not transform_kw else transform_kw["transform"]._as_mpl_transform(ax)
    frame = ax.get_window_extent()
    fx0, fy0, fx1, fy1 = (v * to_pt for v in (frame.x0, frame.y0, frame.x1, frame.y1))
    xy = {lab: tuple(v * to_pt for v in trans.transform((lon, lat))) for lab, lon, lat, _, _ in pts}
    obstacles = [(xy[lab][0] - r, xy[lab][1] - r, xy[lab][0] + r, xy[lab][1] + r) for lab, _, _, r, _ in pts]
    obstacles += list(blocked)
    base_dirs = [(1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1), (0, 1), (0, -1)]
    # Bold (primary) stations first, then top to bottom.
    for lab, lon, lat, r, bold in sorted(pts, key=lambda t: (not t[4], -t[2], t[1])):
        x, y = xy[lab]
        # With a station close by, try the side facing away from it first (so a close pair splits left/right).
        near = min(((np.hypot(xy[o][0] - x, xy[o][1] - y), o) for o in xy if o != lab), default=(np.inf, None))
        dirs = base_dirs
        if near[0] < 45:
            ax_, ay_ = x - xy[near[1]][0], y - xy[near[1]][1]
            dirs = sorted(base_dirs, key=lambda d: -(d[0] * ax_ + d[1] * ay_) / np.hypot(*d))
        w, h = 0.62 * fontsize * len(lab) * (1.08 if bold else 1.0), 1.15 * fontsize
        chosen = None
        for dist, leader in ((r + 3.5, False), (r + 14, True), (r + 26, True)):
            for dx, dy in dirs:
                ox, oy = dx * dist, dy * dist * 0.8
                bx0 = x + ox if dx > 0 else (x + ox - w if dx < 0 else x - w / 2)
                by0 = y + oy if dy > 0 else (y + oy - h if dy < 0 else y + oy - h / 2)
                box = (bx0, by0, bx0 + w, by0 + h)
                inside = box[0] >= fx0 + 2 and box[2] <= fx1 - 2 and box[1] >= fy0 + 2 and box[3] <= fy1 - 2
                if inside and not any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1]
                                      for b in obstacles):
                    chosen = (ox, oy, dx, dy, box, leader)
                    break
            if chosen:
                break
        if chosen is None:  # nowhere free: right of the marker
            chosen = (r + 3.5, 0, 1, 0, (x + r + 3.5, y - h / 2, x + r + 3.5 + w, y + h / 2), False)
        ox, oy, dx, dy, box, leader = chosen
        obstacles.append(box)
        ax.annotate(lab, xy=(lon, lat), xycoords=trans, xytext=(ox, oy), textcoords="offset points",
                    ha="left" if dx > 0 else ("right" if dx < 0 else "center"),
                    va="bottom" if dy > 0 else ("top" if dy < 0 else "center"),
                    fontsize=fontsize, color=MAP_INK, fontweight="bold" if bold else "normal", zorder=8,
                    arrowprops=dict(arrowstyle="-", color=MAP_INK_SOFT, linewidth=0.6, shrinkA=1, shrinkB=r)
                    if leader else None)


def requested_station_map(track, stations: pd.DataFrame, availability: pd.DataFrame | None, cfg) -> Path | None:
    """Two-panel map of every requested station, marked by its data outcome:
    (a) the region, with the storm's track when there is one (``track=None``
    for a period away from the storm), and (b) a detail panel with station IDs.

    ``cfg.map_highlight`` stations get a ring and a bold label;
    ``cfg.map_region_name`` names panel (b).
    """
    req = stations[stations["key"].isin(cfg.include)].copy()
    if req.empty:
        return None
    req["status"] = station_status(req, availability).reindex(req["key"]).to_numpy()
    req["label"] = req["station_id"].astype(str)
    req["primary"] = req["key"].isin(cfg.map_highlight)
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        import matplotlib.ticker as mticker
        proj = ccrs.PlateCarree()
    except ImportError:
        ccrs = None

    # Detail extent: the crowded Mid-Atlantic / New England coast, or a tight cluster (one bay) with a margin.
    zoom, region = (-78.2, -65.8, 34.6, 45.2), cfg.map_region_name or "Mid-Atlantic and New England"
    span = max(req["lon"].max() - req["lon"].min(), req["lat"].max() - req["lat"].min())
    if span < 3.0:
        pad_lon, pad_lat = max(0.15, 0.3 * span), max(0.1, 0.18 * span)
        zoom = (req["lon"].min() - pad_lon, req["lon"].max() + pad_lon,
                req["lat"].min() - pad_lat, req["lat"].max() + pad_lat)
        region = cfg.map_region_name or "Station detail"
    f = track.fixes if track is not None else req.iloc[:0]
    lon_min = min(req["lon"].min(), f["lon"].min() if len(f) else np.inf) - 2.0
    lon_max = max(req["lon"].max(), f["lon"].max() if len(f) else -np.inf) + 2.0
    lat_min = min(req["lat"].min(), f["lat"].min() if len(f) else np.inf) - 2.0
    lat_max = max(req["lat"].max(), f["lat"].max() if len(f) else -np.inf) + 1.5
    extents = ((lon_min, lon_max, lat_min, lat_max), zoom)
    # Equal heights: in PlateCarree each panel's width / height is its lon span / lat span.
    ratios = [(e[1] - e[0]) / (e[3] - e[2]) for e in extents]

    fig = plt.figure(figsize=(14, 14 / (sum(ratios) + 0.35)), layout="constrained")
    fig.patch.set_facecolor("white")
    fig.get_layout_engine().set(w_pad=0.02, h_pad=0.02, wspace=0.03)
    gs = fig.add_gridspec(1, 2, width_ratios=ratios)
    panels = []
    for k, extent in enumerate(extents):
        detail = k == 1
        span_k = max(extent[1] - extent[0], extent[3] - extent[2])
        scale = "10m" if span_k < 6 else "50m"
        if ccrs is not None:
            ax = fig.add_subplot(gs[0, k], projection=proj)
            ax.set_extent(extent, crs=proj)
            fine = _census_states(cfg) if detail and span_k < 3 else None
            try:
                if fine:  # land and state lines from one 1:500k layer
                    ax.add_geometries(fine, crs=proj, facecolor=MAP_LAND, edgecolor=MAP_COAST, linewidth=0.6,
                                      zorder=0)
                    raise StopIteration
                ax.add_feature(cfeature.LAND.with_scale(scale), facecolor=MAP_LAND, edgecolor="none", zorder=0)
                ax.add_feature(cfeature.STATES.with_scale(scale), linewidth=0.45, edgecolor=MAP_STATE,
                               facecolor="none", zorder=1)
                ax.add_feature(cfeature.BORDERS.with_scale(scale), linewidth=0.5, edgecolor=MAP_STATE, zorder=1)
                ax.add_feature(cfeature.COASTLINE.with_scale(scale), linewidth=0.6, edgecolor=MAP_COAST, zorder=2)
            except StopIteration:
                pass
            except Exception as err:  # Natural Earth data unavailable offline
                log.warning("station map without coastlines: %s", err)
            step = _nice_step(extent[1] - extent[0], 6 if detail else 9)
            gl = ax.gridlines(draw_labels=True, color=MAP_GRID, linewidth=0.5, zorder=0.5,
                              xlocs=mticker.MultipleLocator(step), ylocs=mticker.MultipleLocator(step))
            gl.top_labels = gl.right_labels = False
            gl.xlabel_style = gl.ylabel_style = {"color": MAP_INK_SOFT, "size": 9}
            kw = {"transform": proj}
        else:
            ax = fig.add_subplot(gs[0, k])
            ax.set_xlim(extent[0], extent[1])
            ax.set_ylim(extent[2], extent[3])
            kw = {}
        ax.set_facecolor("white")
        panels.append((ax, extent, kw))
    main, zoom_ax = panels[0][0], panels[1][0]
    main_kw, zoom_kw = panels[0][2], panels[1][2]

    # Faint state abbreviations on a regional panel small enough for them to help, away from the detail box.
    for abbr, (la, lo) in STATE_LABELS.items() if extents[0][1] - extents[0][0] < 15 else ():
        e = extents[0]
        if e[0] + 0.3 < lo < e[1] - 0.3 and e[2] + 0.3 < la < e[3] - 0.3 and not (
                zoom[0] - 0.2 < lo < zoom[1] + 0.2 and zoom[2] - 0.2 < la < zoom[3] + 0.2):
            main.text(lo, la, abbr, fontsize=9, color="#9a9a9a", ha="center", va="center", zorder=2, **main_kw)

    sizes = {"main": 26.0, "zoom": 48.0}  # marker areas (points^2)
    for (ax, extent, kw), size in zip(panels, (sizes["main"], sizes["zoom"])):
        if track is not None:
            ax.plot(f["lon"], f["lat"], color=_style.TEXT_MUTED, linewidth=2.0, zorder=3, **kw)
            lf = f[f["record_id"] == "L"]
            ax.scatter(lf["lon"], lf["lat"], marker="*", s=130, color=_style.TEXT_SECONDARY, zorder=4, **kw)
        for status, style in STATUS_STYLE.items():
            g = req[req["status"] == status]
            if g.empty:
                continue
            ax.scatter(g["lon"], g["lat"], marker=style["marker"], s=size * np.where(g["primary"], 1.35, 1.0),
                       facecolors=MAP_INK if style["filled"] else "white", edgecolors=MAP_INK,
                       linewidths=1.0, zorder=6, **kw)
        p = req[req["primary"]]
        if len(p):  # ring around each primary station
            ax.scatter(p["lon"], p["lat"], marker="o", s=size * 4.2, facecolors="none", edgecolors=MAP_INK,
                       linewidths=0.9, zorder=5, **kw)

    # Track dates (storm runs only): first fix, first landfall, last fix.
    if track is not None:
        marks = [(f.iloc[0], 0.4, -1.4, "left"), (f.iloc[-1], 0.6, 0.2, "left")]
        if (f["record_id"] == "L").any():
            marks.append((f[f["record_id"] == "L"].iloc[0], -0.8, 0.2, "right"))
        for r, dx, dy, ha in marks:
            main.text(r["lon"] + dx, r["lat"] + dy, r["time"].strftime("%d %b %HZ"), fontsize=8, ha=ha,
                      color=_style.TEXT_SECONDARY, zorder=5, **main_kw)

    # Zoom box on (a); panel (b)'s frame gets the same dashed treatment.
    x0, x1, y0, y1 = zoom
    box_style = {"color": MAP_BOX, "linewidth": 1.1, "linestyle": (0, (4, 2.5))}
    main.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], zorder=7, **box_style, **main_kw)
    for spine in zoom_ax.spines.values():
        spine.set_edgecolor(MAP_BOX)
        spine.set_linewidth(1.1)
        spine.set_linestyle((0, (4, 2.5)))

    # Legend: frameless, upper left of (a), markers exactly as on the map.
    counts = req["status"].value_counts()
    ms = np.sqrt(sizes["main"]) * 1.15
    handles = [Line2D([], [], marker=st["marker"], linestyle="", markersize=ms,
                      markerfacecolor=MAP_INK if st["filled"] else "white", markeredgecolor=MAP_INK,
                      markeredgewidth=1.0, label=f"{STATUS_LABELS.get(name, name)} ({counts.get(name, 0)})")
               for name, st in STATUS_STYLE.items() if counts.get(name, 0)]
    labels = [h.get_label() for h in handles]
    if req["primary"].any():  # dot + ring, overlaid exactly as on the map
        dot = Line2D([], [], marker="o", linestyle="", markersize=ms * 1.16, markerfacecolor=MAP_INK,
                     markeredgecolor=MAP_INK, markeredgewidth=1.0)
        ring = Line2D([], [], marker="o", linestyle="", markersize=ms * 2.05, markerfacecolor="none",
                      markeredgecolor=MAP_INK, markeredgewidth=0.9)
        handles.append((ring, dot))
        labels.append("Primary station")
    if track is not None:
        handles += [Line2D([], [], color=_style.TEXT_MUTED, linewidth=2.0),
                    Line2D([], [], marker="*", linestyle="", color=_style.TEXT_SECONDARY, markersize=10)]
        labels += [f"{track.name.title()} best track", "Landfall"]
    leg = main.legend(handles, labels, loc="upper left", frameon=False, fontsize=9.5, labelcolor=MAP_INK,
                      handletextpad=0.6, borderaxespad=0.6, labelspacing=0.7)
    leg.set_zorder(9)

    main.set_title(r"$\bf{(a)}$ Regional station locations", loc="left", fontsize=12, color=MAP_INK, pad=8)
    zoom_ax.set_title(fr"$\bf{{(b)}}$ {region} detail", loc="left", fontsize=12, color=MAP_INK, pad=8)

    # Station IDs: every station in the detail panel; on the regional panel only those outside the box.
    inside = req["lon"].between(zoom[0], zoom[1]) & req["lat"].between(zoom[2], zoom[3])
    fig.canvas.draw()
    leg_box = leg.get_window_extent()
    to_pt = 72.0 / fig.dpi
    leg_pts = (leg_box.x0 * to_pt, leg_box.y0 * to_pt, leg_box.x1 * to_pt, leg_box.y1 * to_pt)

    def pts(rows, size):
        r0 = np.sqrt(size) / 2 + 1.5
        return [(lab, lo, la, r0 * (2.1 if pri else 1.0), bool(pri))
                for lab, lo, la, pri in zip(rows["label"], rows["lon"], rows["lat"], rows["primary"])]

    _label_stations(zoom_ax, fig, pts(req[inside], sizes["zoom"]), fontsize=9.5, transform_kw=zoom_kw)
    if (~inside).any():
        _label_stations(main, fig, pts(req[~inside], sizes["main"]), fontsize=7.5, transform_kw=main_kw,
                        blocked=[leg_pts])
    return _save(fig, cfg, "map_requested_stations", dpi=300)


def station_distances(stations: pd.DataFrame, cfg, top: int = 40) -> Path:
    s = stations.sort_values("cpa_distance_km").head(top).iloc[::-1]
    fig, ax = _style.new_axes(figsize=(8, max(3.5, 0.22 * len(s) + 1)))
    ax.barh(s["key"], s["cpa_distance_km"], color=_style.TEXT_MUTED, height=0.7)
    for y, (d, cls) in enumerate(zip(s["cpa_distance_km"], s["station_class"])):
        ax.text(d + 5, y, cls, va="center", fontsize=7, color=_style.TEXT_SECONDARY)
    _style.style_axes(ax, grid_axis="x")
    ax.set_xlabel("closest approach of best track (km, geodesic)")
    ax.set_title(f"Nearest {len(s)} stations to the track", loc="left")
    ax.tick_params(axis="y", labelsize=7)
    return _save(fig, cfg, "station_distance_from_track")


# ---------------------------------------------------------------------------
# Time series (publication style)
# ---------------------------------------------------------------------------
# Shared by the single- and two-panel figures so both read identically.
TS_FONT = {"title": 15.0, "subtitle": 12.0, "panel": 12.0, "label": 13.0, "tick": 11.0, "legend": 11.5, "note": 10.0,
           "table": 10.0}
TS_STYLE = {
    "obs": {"color": "black", "linewidth": 2.1, "linestyle": (0, (5, 2.5)), "zorder": 6},
    "obs_raw": {"color": "#9a9a9a", "linewidth": 0.8, "alpha": 0.5, "zorder": 2},  # before averaging, secondary
    "gdas": {"color": GDAS, "linewidth": 2.1, "zorder": 4},
    "gfs": {"color": GFS, "linewidth": 2.1, "zorder": 5},
}
TS_LABELS = {"gdas": "GDAS", "gfs": "GFS"}
TS_SHADE = {"facecolor": "#808080", "alpha": 0.07, "edgecolor": "none", "zorder": 0}  # best-track period
TS_INK, TS_INK_SOFT, TS_INK_MUTED, TS_GRID = "#222222", "#555555", "#7a7a7a", "#e6e6e6"
TS_AXIS, TS_GRID_MINOR = "#8c8c8c", "#f2f2f2"  # subtle spines/ticks; minor grid barely visible
TS_ZOOM_SHADE = {"facecolor": "#808080", "alpha": 0.08, "edgecolor": "none", "zorder": 0}  # panel (b) interval
TS_ZOOM_YMAX = 20.0  # m s-1 for the detail panel; raised when its data go higher
TS_YMAX = 26.0  # m s-1; raised for a station whose data go higher
TS_ZOOM_HALF_HOURS = 30.0  # zoom panel spans the observed peak +/- this
SOURCE_NAMES = {"ndbc": "NDBC", "coops": "CO-OPS", "ghcnh": "GHCNh"}


def _with_gaps(times, values, factor: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """``values`` with a NaN wherever the time step exceeds ``factor`` x its median.

    The NaN only breaks the drawn line across a missing period; no value is changed.
    """
    d = pd.DataFrame({"t": pd.to_datetime(np.asarray(times)), "v": np.asarray(values, dtype=float)})
    dt = d["t"].diff()
    gap = dt > factor * dt.median()
    if gap.any():
        d = pd.concat([d, pd.DataFrame({"t": d["t"][gap] - dt[gap] / 2, "v": np.nan})]).sort_values("t", kind="stable")
    return d["t"], d["v"]


def _name_parts(s) -> tuple[str, str]:
    """'EAST HATTERAS - 150 NM East of Cape Hatteras' -> ('East Hatteras', '150 NM east of Cape Hatteras')."""
    head, _, where = str(s.name).partition(" - ")
    head = head.strip().title() if head.isupper() else head.strip()
    where = re.sub(r"\b(North|South|East|West)([a-z]*) of\b", lambda m: m.group(0).lower(), where.strip())
    return head, where


def _station_text(s, adjusted: bool = True) -> tuple[str, str]:
    """Title ('Wind-speed comparison at Borden Flats Light') and subtitle
    ('CO-OPS 8447387 · Observed and modeled 10-m winds', plus the location when the name has one)."""
    head, where = _name_parts(s)
    head = re.sub(r" at [^,]+$", "", head)  # 'Borden Flats Light at Fall River' -> 'Borden Flats Light'
    sub = f"{SOURCE_NAMES.get(s.source, str(s.source).upper())} {s.station_id} · Observed and modeled " + (
        "10-m winds" if adjusted else "winds")
    return f"Wind-speed comparison at {head}", sub + (f" · {where}" if where else "")


def _zoom_window(o: pd.DataFrame, m: pd.DataFrame, xlim) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The observed peak (model peak if no observations) +/- TS_ZOOM_HALF_HOURS, inside ``xlim``."""
    col = "wind_speed_adj" if o["wind_speed_adj"].notna().any() else "wind_speed"
    if o[col].notna().any():
        peak = o.loc[o[col].idxmax(), "time"]
    else:
        peak = m.loc[m["model_speed"].idxmax(), "valid_time"]
    c, h = pd.Timestamp(peak).floor("6h"), pd.Timedelta(hours=TS_ZOOM_HALF_HOURS)
    return max(c - h, xlim[0]), min(c + h, xlim[1])


def _obs_column(o: pd.DataFrame) -> tuple[str, str]:
    """The observed series drawn: adjusted to 10 m, or at sensor height where it cannot be adjusted."""
    if o["wind_speed_adj"].notna().any():
        return "wind_speed_adj", "10 m"
    return "wind_speed", "sensor height"


def _speed_panel(ax, o: pd.DataFrame, m: pd.DataFrame, cfg, xlim, ytop: float, storm_period, zoom: bool,
                 oh: pd.DataFrame | None = None) -> None:
    """One time-series panel; every style comes from TS_* so all panels match.

    ``oh``: window-mean observations, drawn over the dimmed unaveraged ``o``.
    """
    col = _obs_column(o)[0]
    if oh is not None:
        t, v = _with_gaps(o["time"], o[col])
        ax.plot(t, v, **TS_STYLE["obs_raw"])
        o = oh
    t, v = _with_gaps(o["time"], o[col])
    ax.plot(t, v, **TS_STYLE["obs"])
    for label, key in ((evaluate.gdas_series(cfg), "gdas"), (evaluate.GFS_COMPOSITE, "gfs")):
        g = m[m["model_label"] == label].sort_values("valid_time")
        t, v = _with_gaps(g["valid_time"], g["model_speed"])
        ax.plot(t, v, **TS_STYLE[key])
    if storm_period is not None:
        a, b = max(storm_period[0], xlim[0]), min(storm_period[1], xlim[1])
        if a < b and (a, b) != tuple(xlim):  # a span filling the whole panel says nothing
            ax.axvspan(a, b, **TS_SHADE)
            ax.annotate("Best-track period", xy=(b, 1), xycoords=("data", "axes fraction"), xytext=(-5, -4),
                        textcoords="offset points", ha="right", va="top", fontsize=TS_FONT["note"],
                        color=TS_INK_MUTED)

    ax.set_xlim(*xlim)
    ax.set_ylim(0, ytop)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    if zoom:  # 12-hourly: the date at 00 UTC, the time at 12 UTC
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
        ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 18]))
        ax.xaxis.set_major_formatter(
            lambda x, _: (d := mdates.num2date(x)).strftime("%b %d" if d.hour == 0 else "%H:%M"))
    else:  # every second day at 00 UTC
        ax.set_xticks(pd.date_range(xlim[0].ceil("D"), xlim[1].floor("D"), freq="2D"))
        ax.xaxis.set_minor_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(TS_AXIS)
        ax.spines[side].set_linewidth(0.8)
    ax.grid(True, axis="both", which="major", color=TS_GRID, linewidth=0.6)
    ax.grid(False, which="minor")
    if zoom:
        ax.grid(True, axis="x", which="minor", color=TS_GRID_MINOR, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", which="major", labelsize=TS_FONT["tick"], colors=TS_AXIS, labelcolor=TS_INK,
                   length=4, width=0.8, pad=3, labelrotation=0)
    ax.tick_params(axis="x", which="minor", colors=TS_AXIS, length=2, width=0.6)
    ax.set_ylabel("Wind speed (m s$^{-1}$)", fontsize=TS_FONT["label"], color=TS_INK, labelpad=6)
    ax.set_xlabel("Time (UTC)", fontsize=TS_FONT["label"], color=TS_INK, labelpad=4)


def _header(ax, title: str | None, subtitle: str | None, scores: pd.DataFrame | None, tag: str | None = None) -> None:
    """Stacked left header (title above subtitle above panel tag) and the statistics table on the right."""
    lines = [(tag, TS_FONT["panel"], {"color": TS_INK}),
             (subtitle, TS_FONT["subtitle"], {"color": TS_INK_MUTED}),
             (title, TS_FONT["title"], {"color": "black", "fontweight": "bold"})]
    y = 8.0
    for k, (text, size, kw) in enumerate(t for t in lines if t[0]):
        if k == 0:  # the line nearest the axes is a normal title
            ax.set_title(text, loc="left", fontsize=size, pad=y, **kw)
        else:
            ax.annotate(text, xy=(0, 1), xycoords="axes fraction", xytext=(0, y), textcoords="offset points",
                        ha="left", va="bottom", fontsize=size, annotation_clip=False, **kw)
        y += size * 1.3 + 3
    if scores is not None:
        _stats_table(ax, scores)


def _stats_table(ax, scores: pd.DataFrame) -> None:
    """'Full-period statistics' above the panel's top-right corner, clear of the data.

    Proportional font; each column right-aligned at a fixed offset (points)
    from the axes' right edge, model names in their line colours.
    """
    scores = scores.dropna(subset=["rmse"])
    if scores.empty:
        return
    size = TS_FONT["table"]
    cols = [("n", lambda r: f"{int(r['n'])}", -196), ("Bias", lambda r: f"{r['bias']:+.2f}", -144),
            ("MAE", lambda r: f"{r['mae']:.2f}", -96), ("RMSE", lambda r: f"{r['rmse']:.2f}", -48),
            ("r", lambda r: f"{r['correlation']:.2f}", 0)]
    step = size * 1.45

    def put(text, x, y, ha="right", **kw):
        ax.annotate(text, xy=(1, 1), xycoords="axes fraction", xytext=(x, y), textcoords="offset points",
                    ha=ha, va="bottom", fontsize=kw.pop("fontsize", size), annotation_clip=False, **kw)

    y = 6.0
    put("Errors in m s$^{-1}$", 0, y, fontsize=size - 1, color=TS_INK_MUTED)
    y += step + 2
    for name in reversed(list(scores.index)):
        r = scores.loc[name]
        put(name, -272, y, ha="left", color={"GDAS": GDAS, "GFS": GFS}.get(name, TS_INK), fontweight="bold")
        for _, fmt, x in cols:
            put(fmt(r), x, y, color=TS_INK)
        y += step
    put("Model", -272, y, ha="left", color=TS_INK_SOFT)
    for head, _, x in cols:
        put(head, x, y, color=TS_INK_SOFT)
    y += step + 1
    put("Full-period statistics", -272, y, ha="left", color=TS_INK, fontweight="bold")


def _scores(metrics: pd.DataFrame | None, cfg, key: str | None = None) -> pd.DataFrame:
    """n, bias, MAE, RMSE, r for GDAS and GFS (index), for one station ``key`` or all stations.

    Against observations adjusted to 10 m where the station has them, otherwise at sensor height.
    """
    cols = ["n", "bias", "mae", "rmse", "correlation"]
    if metrics is None or metrics.empty:
        return pd.DataFrame(columns=cols)
    pair = {evaluate.gdas_series(cfg): "GDAS", evaluate.GFS_COMPOSITE: "GFS"}
    rows = metrics[metrics["model_label"].isin(pair) & (metrics["scored"] == True)]  # noqa: E712 (object column)
    rows = rows[(rows["grouping"] == "station") & (rows["group"] == key)] if key else rows[rows["grouping"] == "overall"]
    for ref in ("adjusted", "unadjusted"):
        r = rows[rows["reference"] == ref]
        if len(r):
            return r.assign(model=r["model_label"].map(pair)).set_index("model").reindex(["GDAS", "GFS"])[cols]
    return pd.DataFrame(columns=cols)


def _ts_legend(fig, obs_height: str, average_minutes: int = 0) -> None:
    """One frameless row under the plot: the observation(s) and the two models."""
    if average_minutes:
        mean = f"{average_minutes // 60:g}-h" if average_minutes % 60 == 0 else f"{average_minutes:g}-min"
        labels = {"obs_raw": f"Observed ({obs_height}, unaveraged)", "obs": f"Observed ({obs_height}, {mean} mean)"}
        keys = ("obs_raw", "obs", "gdas", "gfs")
    else:
        labels, keys = {"obs": f"Observed ({obs_height})"}, ("obs", "gdas", "gfs")
    labels.update(TS_LABELS)
    handles = [Line2D([], [], **{k: v for k, v in TS_STYLE[key].items() if k != "zorder"}, label=labels[key])
               for key in keys]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles), frameon=False,
               fontsize=TS_FONT["legend"], handlelength=3.0, handletextpad=0.6, columnspacing=2.4,
               borderaxespad=0.2, labelcolor=TS_INK)


def _ts_caption(s, o: pd.DataFrame, cfg, xlim, storm_period, zoom=None, averaged: bool = False) -> str:
    """Figure caption: the methodological detail that stays out of the legend."""
    fmt = "%d %b %H:%M"
    lat = f"{abs(s.lat):.2f}°{'N' if s.lat >= 0 else 'S'}"
    lon = f"{abs(s.lon):.2f}°{'E' if s.lon >= 0 else 'W'}"
    where = _name_parts(s)[1]
    loc = f"{lat}, {lon}" + (f"; {where}" if where else "")
    z = o["z_sensor_m"].dropna()
    z0 = o["z0_m"].dropna() if "z0_m" in o else pd.Series(dtype=float)
    parts = [f"Wind speed at {SOURCE_NAMES.get(s.source, s.source)} {s.station_id} ({loc}), "
             f"{xlim[0]:{fmt}} – {xlim[1]:{fmt}} UTC {xlim[1]:%Y}."]
    if zoom is not None:
        parts.append(f"(a) Full analysis period; (b) {zoom[0]:{fmt}} – {zoom[1]:{fmt}} UTC, centred on the "
                     "observed peak (shaded interval in a).")
    if len(z) and o["wind_speed_adj"].notna().any():
        z0txt = f" (z0 = {z0.iloc[0]:g} m)" if len(z0) else ""
        height_txt = (f"observed sustained speed adjusted from the {z.iloc[0]:.1f}-m sensor height to "
                      f"{cfg.target_height_m:g} m with the neutral logarithmic wind profile{z0txt}")
    else:
        height_txt = "observed sustained speed at sensor height (height unknown, not adjusted)"
    if averaged:
        w_min = cfg.obs_average_minutes
        parts.append(f"Observations: {height_txt}, then averaged. Dashed black: {w_min:g}-min means centred on each "
                     f"model hour (windows with less than {cfg.obs_average_min_coverage:.0%} of the expected samples "
                     "omitted); gray: the same observations before averaging. Model–observation statistics use "
                     "the means.")
    else:
        parts.append(f"Dashed black: {height_txt}.")
    w = cfg.gdas_lead_windows[0]
    parts.append(f"GDAS: hourly series from the f{w:03d}–f{w + 5:03d} lead window"
                 + (" (f000 final analysis at the 00, 06, 12 and 18 UTC cycles; f001–f005 forecasts between)."
                    if w == 0 else "."))
    parts.append(f"GFS: composite using, at each valid time, the shortest available lead >= "
                 f"{cfg.composite_min_lead} h. Model 10-m winds are {cfg.interpolation}-interpolated to the station.")
    if storm_period is not None and storm_period[0] < xlim[1] and storm_period[1] > xlim[0]:
        parts.append(f"Shading: best-track period ({storm_period[0]:{fmt}} – {storm_period[1]:{fmt}} UTC).")
    if len(o) and o["time"].max() < xlim[1] - pd.Timedelta(hours=3):
        parts.append(f"No quality-controlled observations after {o['time'].max():{fmt}} UTC.")
    return " ".join(parts)


def _write_caption(png: Path, text: str) -> None:
    png.with_suffix(".caption.txt").write_text(text + "\n", encoding="utf-8")


def station_timeseries(labelled: pd.DataFrame, obs: pd.DataFrame, stations: pd.DataFrame, cfg,
                       n_stations: int = 6, period=None, storm_period=None,
                       obs_averaged: pd.DataFrame | None = None, metrics: pd.DataFrame | None = None) -> list[Path]:
    """The ``n_stations`` nearest the track, plus every requested station, that have wind data.

    Two figures per station: the full ``period`` (default: the observation span)
    and a two-panel version that adds a zoom on the observed peak. ``storm_period``
    (first, last best-track fix) is shaded. ``obs_averaged`` (window means) is
    drawn over the dimmed unaveraged observations. Each PNG gets a ``.caption.txt``.
    Requested stations go to ``figures/stations/``; the nearest also to ``figures/``.
    """
    paths = []
    storm_period = tuple(pd.Timestamp(t) for t in storm_period) if storm_period is not None else None
    cand = stations[stations["key"].isin(obs.loc[obs["qc_pass"], "key"].unique())].sort_values("cpa_distance_km")
    nearest = set(cand.head(n_stations)["key"])
    requested = set(cand.loc[cand["selected_by"] == "manual include", "key"])
    for s in cand[cand["key"].isin(nearest | requested)].itertuples(index=False):
        o = obs[(obs["key"] == s.key) & obs["qc_pass"]].sort_values("time")
        m = labelled[labelled["key"] == s.key]
        m = m[m["model_label"].isin([evaluate.gdas_series(cfg), evaluate.GFS_COMPOSITE])]
        oh = None
        if obs_averaged is not None:
            oh = obs_averaged[(obs_averaged["source"] == s.source)
                              & (obs_averaged["station_id"].astype(str) == str(s.station_id))].sort_values("time")
        avg_min = cfg.obs_average_minutes if oh is not None else 0
        xlim = tuple(pd.Timestamp(t) for t in period) if period is not None else (o["time"].min(), o["time"].max())
        obs_col, obs_label = _obs_column(o)
        title, subtitle = _station_text(s, adjusted=obs_col == "wind_speed_adj")
        scores = _scores(metrics, cfg, s.key)
        ov = o.loc[o["time"].between(*xlim), obs_col].to_numpy(dtype=float)
        mv = m.loc[m["valid_time"].between(*xlim), "model_speed"].to_numpy(dtype=float)
        ytop = max(TS_YMAX, math.ceil(np.nanmax(np.r_[ov, mv, 0.0]) + 1))
        name = f"timeseries_{s.key.replace(':', '_')}"
        name = name if s.key in nearest else f"stations/{name}"

        # Single panel, full period.
        fig, ax = plt.subplots(figsize=(14, 6), layout="constrained")
        fig.patch.set_facecolor("white")
        fig.get_layout_engine().set(h_pad=0.03, w_pad=0.04)
        _speed_panel(ax, o, m, cfg, xlim, ytop, storm_period, zoom=False, oh=oh)
        _header(ax, title, subtitle, scores)
        _ts_legend(fig, obs_label, avg_min)
        paths.append(_save(fig, cfg, name, dpi=300))
        _write_caption(paths[-1], _ts_caption(s, o, cfg, xlim, storm_period, averaged=oh is not None))

        # Two panels: (a) full period, (b) zoom on the observed peak (shaded in a).
        zoom = _zoom_window(oh if oh is not None and len(oh) else o, m, xlim)
        fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(14, 10.5), layout="constrained")
        fig.patch.set_facecolor("white")
        fig.get_layout_engine().set(h_pad=0.03, w_pad=0.04, hspace=0.06)
        _speed_panel(ax_a, o, m, cfg, xlim, ytop, storm_period, zoom=False, oh=oh)
        in_zoom = [v[(t >= zoom[0]) & (t <= zoom[1])] for t, v in
                   ((o["time"], o[obs_col]), (m["valid_time"], m["model_speed"]))]
        zmax = np.nanmax(np.r_[np.concatenate([np.asarray(v, dtype=float) for v in in_zoom]), 0.0])
        ytop_b = max(TS_ZOOM_YMAX, math.ceil(zmax + 1))
        _speed_panel(ax_b, o, m, cfg, zoom, ytop_b, storm_period, zoom=True, oh=oh)
        ax_a.axvspan(*zoom, **TS_ZOOM_SHADE)
        ax_a.annotate("Panel (b) interval", xy=(zoom[0], 1), xycoords=("data", "axes fraction"), xytext=(4, -4),
                      textcoords="offset points", ha="left", va="top", fontsize=TS_FONT["note"] - 1,
                      color=TS_INK_MUTED)
        _header(ax_a, title, subtitle, scores, tag=r"$\bf{(a)}$ Full analysis period")
        _header(ax_b, None, None, None,
                tag=fr"$\bf{{(b)}}$ Detailed comparison: {zoom[0]:%b %d %H:%M}–{zoom[1]:%b %d %H:%M} UTC")
        _ts_legend(fig, obs_label, avg_min)
        paths.append(_save(fig, cfg, f"{name}_zoom", dpi=300))
        _write_caption(paths[-1], _ts_caption(s, o, cfg, xlim, storm_period, zoom=zoom, averaged=oh is not None))
    return paths


def error_vs_cpa_time(labelled: pd.DataFrame, cfg, bin_hours: float = 6.0) -> Path:
    """Mean error (model - adjusted obs) binned by hours from closest approach."""
    e = labelled[(labelled["match_flag"] == "matched") & labelled["obs_speed_adj"].notna()
                 & labelled["use_in_metrics"].astype(bool) & labelled["hours_from_cpa"].notna()]
    fig, ax = _style.new_axes(figsize=(9, 4))
    for label, color in label_colors(cfg).items():
        g = e[e["model_label"] == label]
        b = (np.floor(g["hours_from_cpa"] / bin_hours) * bin_hours + bin_hours / 2)
        agg = (g["model_speed"] - g["obs_speed_adj"]).groupby(b).agg(["mean", "count"])
        agg = agg[agg["count"] >= cfg.min_samples]
        ax.plot(agg.index, agg["mean"], color=color, linewidth=_style.LINE_WIDTH, marker="o", label=label,
                **_style.MARKER_RING)
    ax.axhline(0, color=_style.AXIS, linewidth=1.0)
    ax.axvline(0, color=_style.AXIS, linewidth=1.0, linestyle=":")
    _style.style_axes(ax)
    _style.style_legend(ax)
    ax.set_xlabel(f"hours from station's closest approach ({bin_hours:g} h bins, n >= {cfg.min_samples})")
    ax.set_ylabel("mean error, model - obs (m s$^{-1}$)")
    ax.set_title(f"Speed error relative to closest approach (obs adjusted to {cfg.target_height_m:g} m)",
                 loc="left")
    return _save(fig, cfg, "error_vs_time_from_cpa")


# ---------------------------------------------------------------------------
# Scatter and grouped errors
# ---------------------------------------------------------------------------
def scatter(labelled: pd.DataFrame, cfg, reference: str = "adjusted") -> Path:
    col = "obs_speed_adj" if reference == "adjusted" else "obs_speed"
    e = labelled[(labelled["match_flag"] == "matched") & labelled[col].notna() & labelled["model_speed"].notna()
                 & labelled["use_in_metrics"].astype(bool)]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8), layout="constrained", sharex=True, sharey=True)
    fig.patch.set_facecolor(_style.SURFACE)
    top = max(5.0, float(np.nanmax(e[[col, "model_speed"]].to_numpy())) * 1.05) if len(e) else 30.0
    for ax, (label, color) in zip(axes, label_colors(cfg).items()):
        g = e[e["model_label"] == label]
        ax.plot([0, top], [0, top], color=_style.AXIS, linewidth=1.0, label="1:1")
        ax.scatter(g[col], g["model_speed"], s=10 if len(g) > 500 else 14, color=color,
                   alpha=0.35 if len(g) > 500 else 0.6, edgecolors="none", label=label)
        _style.style_axes(ax, grid_axis="both")
        ax.set_xlim(0, top)
        ax.set_ylim(0, top)
        ax.set_aspect("equal")
        ax.set_title(f"{label} (n = {len(g)})", loc="left", fontsize=10)
        ax.set_xlabel(_obs_label(reference == "adjusted", cfg.target_height_m) + " (m s$^{-1}$)", fontsize=9)
    axes[0].set_ylabel("model 10 m wind speed (m s$^{-1}$)")
    return _save(fig, cfg, f"scatter_{reference}")


def _metric_rows(metrics: pd.DataFrame, grouping: str, reference: str = "adjusted") -> pd.DataFrame:
    return metrics[(metrics["grouping"] == grouping) & (metrics["reference"] == reference)
                   & (metrics["scored"] == True)]  # noqa: E712 (object column)


def error_by_lead(metrics: pd.DataFrame, cfg) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout="constrained")
    fig.patch.set_facecolor(_style.SURFACE)
    lead = _metric_rows(metrics, "lead").copy()
    lead["start"] = lead["group"].astype(float)
    lead = lead.sort_values("start")
    gdas_label = evaluate.gdas_series(cfg)
    gd = _metric_rows(metrics, "overall")
    gd = gd[gd["model_label"] == gdas_label]
    for ax, metric in zip(axes, ("bias", "rmse")):
        # Each window at the mean lead actually verified in it (its centre when hourly).
        x = lead["mean_forecast_hour"]
        ax.plot(x, lead[metric], color=GFS, linewidth=_style.LINE_WIDTH, marker="o", label="GFS by lead window",
                **_style.MARKER_RING)
        if len(gd):
            ax.axhline(float(gd[metric].iloc[0]), color=GDAS, linewidth=_style.LINE_WIDTH, linestyle="--",
                       label=gdas_label)
        if metric == "bias":
            ax.axhline(0, color=_style.AXIS, linewidth=1.0)
        _style.style_axes(ax)
        _style.style_legend(ax)
        ax.set_xlabel("GFS forecast lead, mean over the window's pairs (h)")
        ax.set_ylabel(f"{metric.upper() if metric == 'rmse' else 'mean bias'} (m s$^{{-1}}$)")
    fig.suptitle(f"Speed error vs observations adjusted to {cfg.target_height_m:g} m", x=0.01, ha="left")
    return _save(fig, cfg, "error_by_lead")


def _grouped_bars(metrics: pd.DataFrame, grouping: str, order: list[str], cfg, title: str, name: str) -> Path:
    rows = _metric_rows(metrics, grouping)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout="constrained")
    fig.patch.set_facecolor(_style.SURFACE)
    width = 0.38
    x = np.arange(len(order))
    for ax, metric in zip(axes, ("bias", "rmse")):
        for k, (label, color) in enumerate(label_colors(cfg).items()):
            g = rows[rows["model_label"] == label].set_index("group").reindex(order)
            ax.bar(x + (k - 0.5) * width, g[metric], width=width - 0.02, color=color, label=label)
            for xi, (val, n) in enumerate(zip(g[metric], g["n"])):
                if np.isfinite(val):
                    ax.text(xi + (k - 0.5) * width, 0, f"n={int(n)}", rotation=90, fontsize=6, ha="center",
                            va="bottom", color=_style.TEXT_SECONDARY)
        ax.axhline(0, color=_style.AXIS, linewidth=1.0)
        ax.set_xticks(x, order, fontsize=8)
        _style.style_axes(ax)
        _style.style_legend(ax)
        ax.set_ylabel(f"{'RMSE' if metric == 'rmse' else 'mean bias'} (m s$^{{-1}}$)")
    fig.suptitle(f"{title} (groups with n < {cfg.min_samples} omitted)", x=0.01, ha="left")
    return _save(fig, cfg, name)


def error_by_distance(metrics: pd.DataFrame, cfg) -> Path:
    return _grouped_bars(metrics, "radial_band", band_labels(cfg.radial_bands_km), cfg,
                         "Speed error by distance from the storm centre", "error_by_distance")


def error_by_quadrant(metrics: pd.DataFrame, cfg) -> Path:
    return _grouped_bars(metrics, "motion_quadrant", list(MOTION_QUADRANTS), cfg,
                         "Speed error by motion-relative quadrant", "error_by_quadrant")


def peak_comparison(peaks: pd.DataFrame, cfg) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), layout="constrained")
    fig.patch.set_facecolor(_style.SURFACE)
    top = max(5.0, float(np.nanmax(peaks[["obs_peak", "model_peak"]].to_numpy())) * 1.05) if len(peaks) else 30.0
    axes[0].plot([0, top], [0, top], color=_style.AXIS, linewidth=1.0)
    for label, color in label_colors(cfg).items():
        g = peaks[peaks["model_label"] == label]
        axes[0].scatter(g["obs_peak"], g["model_peak"], s=30, color=color, label=label, edgecolors=_style.SURFACE,
                        linewidths=1.0)
    edges = np.arange(-60, 61, 6).tolist()
    for label, color in label_colors(cfg).items():
        g = peaks[peaks["model_label"] == label]
        axes[1].hist(g["peak_timing_error_h"].clip(-60, 60), bins=edges, histtype="step", linewidth=_style.LINE_WIDTH,
                     color=color, label=label)
    axes[0].set_xlim(0, top)
    axes[0].set_ylim(0, top)
    axes[0].set_xlabel(f"observed peak, adjusted to {cfg.target_height_m:g} m (m s$^{{-1}}$)")
    axes[0].set_ylabel("model peak (m s$^{-1}$)")
    axes[0].set_title("Peak magnitude per station", loc="left", fontsize=10)
    axes[1].set_xlabel("model peak time - observed peak time (h)")
    axes[1].set_ylabel("stations")
    axes[1].set_title("Peak timing error", loc="left", fontsize=10)
    for ax in axes:
        _style.style_axes(ax, grid_axis="both" if ax is axes[0] else "y")
        _style.style_legend(ax)
    return _save(fig, cfg, "peak_wind")


def direction_errors(dir_err: pd.DataFrame, cfg) -> Path:
    fig, ax = _style.new_axes(figsize=(9, 4))
    edges = np.arange(-180, 181, 15).tolist()
    for label, color in label_colors(cfg).items():
        g = dir_err[dir_err["model_label"] == label]["direction_error"]
        if len(g):
            ax.hist(g, bins=edges, histtype="step", linewidth=_style.LINE_WIDTH, color=color, density=True,
                    label=f"{label} (n = {len(g)})")
    ax.axvline(0, color=_style.AXIS, linewidth=1.0)
    ax.set_xticks(np.arange(-180, 181, 45))
    _style.style_axes(ax)
    _style.style_legend(ax)
    ax.set_xlabel("direction error, model - obs (deg, shortest signed angle; positive = model clockwise)")
    ax.set_ylabel("density")
    ax.set_title(f"Wind-direction error (both speeds >= {cfg.min_speed_for_direction:g} m s$^{{-1}}$)", loc="left")
    return _save(fig, cfg, "direction_error")


def coverage_heatmap(obs: pd.DataFrame, stations: pd.DataFrame, period, cfg, top_land: int = 20) -> Path:
    """Every marine station (NDBC, CO-OPS), plus the ``top_land`` land stations nearest the track."""
    by_dist = stations.sort_values("cpa_distance_km")
    keys = (by_dist.loc[by_dist["source"] != "ghcnh", "key"].tolist()
            + by_dist.loc[by_dist["source"] == "ghcnh", "key"].head(top_land).tolist())
    days = pd.date_range(pd.Timestamp(period[0]).floor("D"), pd.Timestamp(period[1]).floor("D"), freq="D")
    o = obs[obs["qc_pass"] & obs["key"].isin(keys)]
    hrs = (o.assign(day=o["time"].dt.floor("D"), hour=o["time"].dt.floor("h"))
            .groupby(["key", "day"])["hour"].nunique().unstack("day").reindex(index=keys, columns=days).fillna(0))
    fig, ax = _style.new_axes(figsize=(10, max(3.5, 0.2 * len(keys) + 1.5)))
    im = ax.imshow(hrs.to_numpy(), aspect="auto", cmap=_style.SPEED_CMAP, vmin=0, vmax=24, interpolation="nearest")
    ax.set_yticks(range(len(keys)), keys, fontsize=7)
    ax.set_xticks(range(len(days)), [d.strftime("%m-%d") for d in days], fontsize=7, rotation=90)
    cb = fig.colorbar(im, ax=ax, shrink=0.8)
    cb.set_label("hours with a quality-passing observation", color=_style.TEXT_SECONDARY)
    _style.style_axes(ax, grid_axis="")
    ax.set_title("Observation coverage (white = no data)", loc="left")
    return _save(fig, cfg, "coverage_observations")


def metrics_summary(summary: pd.DataFrame, stations: pd.DataFrame, cfg) -> Path | None:
    """Bias, MAE, RMSE and correlation per station and for all stations, GDAS beside GFS.

    Up to 12 stations: vertical bars with values. More: one row per station
    (north to south), the four metrics side by side, so every name stays legible.
    """
    if summary.empty:
        return None
    st = stations.set_index("key")
    keys = [k for k in dict.fromkeys(summary["key"]) if k]
    keys = sorted(keys, key=lambda k: -float(st.at[k, "lat"])) if len(keys) > 12 else keys
    order = keys + [""]

    def label(k: str) -> str:
        if not k:
            return "All stations"
        name = _name_parts(SimpleNamespace(name=st.at[k, "name"]))[0]
        return f"{st.at[k, 'station_id']}  {name}"[:34] if len(keys) > 12 else name

    labels = [label(k) for k in order]
    panels = (("bias", "Bias, model − obs (m s$^{-1}$)"), ("mae", "MAE (m s$^{-1}$)"),
              ("rmse", "RMSE (m s$^{-1}$)"), ("correlation", "Correlation r"))
    models = (("GDAS", GDAS), ("GFS", GFS))
    values = {m: summary[summary["model"] == m].set_index("key").reindex(order) for m, _ in models}
    tall = len(keys) > 12
    if tall:
        fig, axes = plt.subplots(1, 4, figsize=(14, 0.26 * len(order) + 1.8), layout="constrained", sharey=True)
    else:
        fig, axes = plt.subplots(2, 2, figsize=(max(10.0, 1.1 * len(order) + 4), 8), layout="constrained",
                                 sharex=True)
    fig.patch.set_facecolor("white")
    pos, width = np.arange(len(order)), 0.38
    for ax, (col, axis_label) in zip(np.ravel(axes), panels):
        for k, (model, color) in enumerate(models):
            v = values[model][col].to_numpy(dtype=float)
            if tall:
                ax.barh(pos + (k - 0.5) * width, v, height=width - 0.04, color=color)
            else:
                bars = ax.bar(pos + (k - 0.5) * width, v, width=width - 0.03, color=color)
                ax.bar_label(bars, fmt="%.2f", fontsize=7.5, padding=2, color=TS_INK_SOFT)
        ref_line = ax.axvline if tall else ax.axhline
        if col == "bias":
            ref_line(0, color=TS_INK_SOFT, linewidth=0.9)
        if col == "correlation":
            (ax.set_xlim if tall else ax.set_ylim)(0, 1.05 if tall else 1.08)
        # Separate the all-stations group.
        (ax.axhline if tall else ax.axvline)(len(order) - 1.5, color=TS_GRID, linewidth=1.0)
        if tall:
            ax.set_xlabel(axis_label, fontsize=10.5)
            ax.set_yticks(pos, labels, fontsize=8.5)
            ax.grid(True, axis="x", color=TS_GRID, linewidth=0.6)
        else:
            ax.set_ylabel(axis_label, fontsize=11)
            ax.set_xticks(pos, labels, rotation=30, ha="right", fontsize=10)
            ax.grid(True, axis="y", color=TS_GRID, linewidth=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(TS_AXIS)
        ax.set_axisbelow(True)
        ax.tick_params(labelsize=9.5 if tall else 10, colors=TS_AXIS, labelcolor=TS_INK)
    if tall:
        axes[0].invert_yaxis()  # shared y: once flips all four; north at the top, all stations at the bottom
        axes[0].set_ylim(len(order) - 0.5, -0.5)
        axes[0].get_yticklabels()[-1].set_fontweight("bold")
    ref = "observations adjusted to 10 m" if (summary["reference"] == "adjusted").all() else "observations"
    w = cfg.obs_average_minutes
    means = f" ({w // 60:g}-h means)" if w and w % 60 == 0 else (f" ({w:g}-min means)" if w else "")
    fig.suptitle(f"GDAS and GFS wind speed against {ref}{means}", x=0.01, ha="left", fontsize=14,
                 fontweight="bold")
    fig.legend(handles=[Patch(facecolor=GDAS, label="GDAS"), Patch(facecolor=GFS, label="GFS")],
               loc="outside lower center", ncol=2, frameon=False, fontsize=11)
    return _save(fig, cfg, "metrics_by_station", dpi=300)


def storm_in_period(track, period) -> bool:
    """Whether any of the storm's lifetime falls inside the analysis period."""
    return track.first_fix <= pd.Timestamp(period[1]) and track.last_fix >= pd.Timestamp(period[0])


def make_all(ev: dict, obs: pd.DataFrame, stations: pd.DataFrame, track, domain, period, cfg,
             availability: pd.DataFrame | None = None, obs_averaged: pd.DataFrame | None = None) -> list[Path]:
    """Every figure; a failing figure is logged and skipped, not fatal.

    For a period away from the storm, the track and storm-relative figures
    (distance, quadrant, closest approach) are left out.
    """
    if storm_in_period(track, period):
        jobs = [
            lambda: [track_map(track, stations, domain, cfg)],
            lambda: [p for p in [requested_station_map(track, stations, availability, cfg)] if p is not None],
            lambda: [station_distances(stations, cfg)],
            lambda: station_timeseries(ev["labelled"], obs, stations, cfg, period=period,
                                       storm_period=(track.first_fix, track.last_fix), obs_averaged=obs_averaged, metrics=ev["metrics"]),
            lambda: [error_vs_cpa_time(ev["labelled"], cfg)],
        ]
    else:  # every station goes to figures/stations/: "nearest the track" means nothing here
        jobs = [
            lambda: [p for p in [requested_station_map(None, stations, availability, cfg)] if p is not None],
            lambda: station_timeseries(ev["labelled"], obs, stations, cfg, n_stations=0, period=period,
                                       obs_averaged=obs_averaged, metrics=ev["metrics"]),
        ]
    jobs += [
        lambda: [p for p in [metrics_summary(ev["summary"], stations, cfg)] if p is not None],
        lambda: [scatter(ev["labelled"], cfg, "adjusted"), scatter(ev["labelled"], cfg, "unadjusted")],
        lambda: [error_by_lead(ev["metrics"], cfg)],
    ]
    if storm_in_period(track, period):
        jobs += [lambda: [error_by_distance(ev["metrics"], cfg)], lambda: [error_by_quadrant(ev["metrics"], cfg)]]
    jobs += [
        lambda: [peak_comparison(ev["peaks"], cfg)] if len(ev["peaks"]) else [],
        lambda: [direction_errors(ev["direction_errors"], cfg)],
        lambda: [coverage_heatmap(obs, stations, period, cfg)],
    ]
    paths: list[Path] = []
    for job in jobs:
        try:
            paths.extend(job())
        except Exception as err:
            log.error("figure failed: %s", err, exc_info=True)
            plt.close("all")
    return paths
