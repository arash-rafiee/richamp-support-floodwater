"""Figure style of the gfs-run-vs-obs skill (wind_comparison_bundle.zip): colours, fonts, the station
time-series panel and header, and the map helpers. Kept identical so the figures match the skill's."""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.ticker import MultipleLocator

GFS, GDAS = "#2a78d6", "#eb6834"  # colour follows the model in every figure
TS_FONT = {"title": 15.0, "subtitle": 12.0, "panel": 12.0, "label": 13.0, "tick": 11.0, "legend": 11.5, "note": 10.0,
           "table": 10.0}
TS_STYLE = {
    "obs": {"color": "black", "linewidth": 2.1, "linestyle": (0, (5, 2.5)), "zorder": 6},
    "obs_raw": {"color": "#9a9a9a", "linewidth": 0.8, "alpha": 0.5, "zorder": 2},  # before averaging
    "gdas": {"color": GDAS, "linewidth": 2.1, "zorder": 4},
    "gfs": {"color": GFS, "linewidth": 2.1, "zorder": 5},
}
TS_INK, TS_INK_SOFT, TS_INK_MUTED, TS_GRID = "#222222", "#555555", "#7a7a7a", "#e6e6e6"
TS_AXIS, TS_GRID_MINOR = "#8c8c8c", "#f2f2f2"
TS_ZOOM_SHADE = {"facecolor": "#808080", "alpha": 0.08, "edgecolor": "none", "zorder": 0}
TS_ZOOM_YMAX = 20.0  # m s-1 for the detail panel; raised when its data go higher
TS_YMAX = 26.0  # m s-1; raised for a station whose data go higher
TS_ZOOM_HALF_HOURS = 30.0  # zoom panel spans the observed peak +/- this
SOURCE_NAMES = {"ndbc": "NDBC", "coops": "CO-OPS", "ghcnh": "GHCNh"}
MAP_LAND, MAP_COAST, MAP_GRID = "#ececec", "#8c8c8c", "#e9e9e9"
MAP_INK, MAP_INK_SOFT, MAP_BOX = "#1a1a1a", "#666666", "#444444"
CENSUS_STATES_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip"


def with_gaps(times, values, factor: float = 3.0):
    """``values`` with a NaN wherever the time step exceeds ``factor`` x its median (breaks the line only)."""
    d = pd.DataFrame({"t": pd.to_datetime(np.asarray(times)), "v": np.asarray(values, dtype=float)})
    dt = d["t"].diff()
    gap = dt > factor * dt.median()
    if gap.any():
        d = pd.concat([d, pd.DataFrame({"t": d["t"][gap] - dt[gap] / 2, "v": np.nan})]).sort_values("t", kind="stable")
    return d["t"], d["v"]


def name_parts(s) -> tuple[str, str]:
    """'EAST HATTERAS - 150 NM East of Cape Hatteras' -> ('East Hatteras', '150 NM east of Cape Hatteras')."""
    head, _, where = str(s.name).partition(" - ")
    head = head.strip().title() if head.isupper() else head.strip()
    where = re.sub(r"\b(North|South|East|West)([a-z]*) of\b", lambda m: m.group(0).lower(), where.strip())
    return head, where


def station_text(s, adjusted: bool = True) -> tuple[str, str]:
    """Title ('Wind-speed comparison at ...') and subtitle ('CO-OPS 8447387 · Observed and modeled 10-m winds')."""
    head, where = name_parts(s)
    head = re.sub(r" at [^,]+$", "", head)
    sub = f"{SOURCE_NAMES.get(s.source, str(s.source).upper())} {s.station_id} · Observed and modeled " + (
        "10-m winds" if adjusted else "winds")
    return f"Wind-speed comparison at {head}", sub + (f" · {where}" if where else "")


def obs_column(o: pd.DataFrame) -> tuple[str, str]:
    """The observed series drawn: adjusted to 10 m, or at sensor height where it cannot be adjusted."""
    if o["wind_speed_adj"].notna().any():
        return "wind_speed_adj", "10 m"
    return "wind_speed", "sensor height"


def zoom_window(o: pd.DataFrame, model_t, model_v, xlim):
    """The observed peak (model peak if no observations) +/- TS_ZOOM_HALF_HOURS, inside ``xlim``."""
    col = obs_column(o)[0]
    if o[col].notna().any():
        peak = o.loc[o[col].idxmax(), "time"]
    else:
        peak = pd.Series(np.asarray(model_t))[int(np.nanargmax(np.asarray(model_v, float)))]
    c, h = pd.Timestamp(peak).floor("6h"), pd.Timedelta(hours=TS_ZOOM_HALF_HOURS)
    return max(c - h, xlim[0]), min(c + h, xlim[1])


def speed_panel(ax, o, oh, models, xlim, ytop: float, zoom: bool) -> None:
    """One time-series panel: grey unaveraged obs, dashed black window means, then ``models``,
    a list of (times, speeds, line style)."""
    col = obs_column(o)[0]
    t, v = with_gaps(o["time"], o[col])
    ax.plot(t, v, **TS_STYLE["obs_raw"])
    t, v = with_gaps(oh["time"], oh[col])
    ax.plot(t, v, **TS_STYLE["obs"])
    for times, speeds, style in models:
        t, v = with_gaps(times, speeds)
        ax.plot(t, v, **style)
    ax.set_xlim(*xlim)
    ax.set_ylim(0, ytop)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    if zoom:  # 12-hourly: the date at 00 UTC, the time at 12 UTC
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
        ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 18]))
        ax.xaxis.set_major_formatter(
            lambda x, _: (d := mdates.num2date(x)).strftime("%b %d" if d.hour == 0 else "%H:%M"))
    else:
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


def header(ax, title, subtitle, tag=None) -> None:
    """Stacked left header: title above subtitle above the panel tag."""
    lines = [(tag, TS_FONT["panel"], {"color": TS_INK}),
             (subtitle, TS_FONT["subtitle"], {"color": TS_INK_MUTED}),
             (title, TS_FONT["title"], {"color": "black", "fontweight": "bold"})]
    y = 8.0
    for k, (text, size, kw) in enumerate(t for t in lines if t[0]):
        if k == 0:
            ax.set_title(text, loc="left", fontsize=size, pad=y, **kw)
        else:
            ax.annotate(text, xy=(0, 1), xycoords="axes fraction", xytext=(0, y), textcoords="offset points",
                        ha="left", va="bottom", fontsize=size, annotation_clip=False, **kw)
        y += size * 1.3 + 3


def stats_table(ax, scores: pd.DataFrame) -> None:
    """'Full-period statistics' above the panel's top-right corner; model names in their line colours."""
    scores = scores.dropna(subset=["rmse"])
    if scores.empty:
        return
    size = TS_FONT["table"]
    cols = [("n", lambda r: f"{int(r['n'])}", -196), ("Bias", lambda r: f"{r['bias']:+.2f}", -144),
            ("MAE", lambda r: f"{r['mae']:.2f}", -96), ("RMSE", lambda r: f"{r['rmse']:.2f}", -48),
            ("r", lambda r: f"{r['correlation']:.2f}", 0)]
    x_name = -max(272, 225 + 7.8 * max(len(n) for n in scores.index))  # bold names: ~7.5 pt per character
    step = size * 1.45

    def put(text, x, y, ha="right", **kw):
        ax.annotate(text, xy=(1, 1), xycoords="axes fraction", xytext=(x, y), textcoords="offset points",
                    ha=ha, va="bottom", fontsize=kw.pop("fontsize", size), annotation_clip=False, **kw)

    y = 6.0
    put("Errors in m s$^{-1}$", 0, y, fontsize=size - 1, color=TS_INK_MUTED)
    y += step + 2
    for name in reversed(list(scores.index)):
        r = scores.loc[name]
        color = GDAS if name.startswith("GDAS") else GFS if name.startswith("GFS") else TS_INK
        put(name, x_name, y, ha="left", color=color, fontweight="bold")
        for _, fmt, x in cols:
            put(fmt(r), x, y, color=TS_INK)
        y += step
    put("Model", x_name, y, ha="left", color=TS_INK_SOFT)
    for head, _, x in cols:
        put(head, x, y, color=TS_INK_SOFT)
    y += step + 1
    put("Full-period statistics", x_name, y, ha="left", color=TS_INK, fontweight="bold")


def write_caption(png: Path, text: str) -> None:
    png.with_suffix(".caption.txt").write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------- maps
def census_states(cache: Path):
    """US state polygons at 1:500,000 (Census cartographic boundaries), cached under ``cache``; None if
    they cannot be fetched or read (the map then uses Natural Earth)."""
    folder = cache / "cb_2023_us_state_500k"
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
    except Exception as err:  # noqa: BLE001
        print(f"Census 1:500k shoreline unavailable ({err}); using Natural Earth")
        return None


def nice_step(span: float, target: int = 6) -> float:
    """A round gridline spacing giving about ``target`` lines over ``span`` degrees."""
    for step in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20):
        if span / step <= target:
            return step
    return 30.0


def label_stations(ax, fig, pts, *, fontsize: float, transform_kw: dict, blocked=()) -> None:
    """Station labels at consistent offsets, clear of every marker, other labels and the panel edge.

    ``pts``: (label, lon, lat, marker radius in points, bold). Tries eight directions close to the
    marker, then further out with a thin leader line.
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
    for lab, lon, lat, r, bold in sorted(pts, key=lambda t: (not t[4], -t[2], t[1])):
        x, y = xy[lab]
        near = min(((np.hypot(xy[o][0] - x, xy[o][1] - y), o) for o in xy if o != lab), default=(np.inf, None))
        dirs = base_dirs
        if near[0] < 45:  # a close neighbour: try the side facing away from it first
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
