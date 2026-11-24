"""Station wind figure: speed, direction (dots), barbs (one column per day, rows
00Z-18Z) and a wind rose for the whole window, with the water-graph heading ("Station — Station ID",
"GFS | window UTC").

Figure size 11.2 x 5.5 in.
Barbs in m/s: half feather 5, full feather 10, flag 50; the staff points to where the wind comes from.

Input: a CSV with station, time, u10, v10, speed_ms, dir_from_deg (from gfs_wind_series.py or
netcdf_wind_series.py). Uses pandas.
Usage: python wind_figure.py <wind_csv> "<station>" <out.png>"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch, Rectangle

plt.rcParams.update({"font.size": 10, "xtick.labelsize": 10, "ytick.labelsize": 10, "axes.labelsize": 10.5})
CSV, STATION, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
d = pd.read_csv(CSV, parse_dates=["time"])
p = d[d.station == STATION].reset_index(drop=True)
t0, t1 = p.time.min(), p.time.max()

fig = plt.figure(figsize=(11.2, 5.5))
gs = fig.add_gridspec(3, 2, width_ratios=[2.6, 1], height_ratios=[1, 1, 1.3], hspace=0.12, wspace=0.1,
                      left=0.07, right=0.985, top=0.83, bottom=0.075)

# ---- heading, water-graph style (Grapher.py): bold "Station — Station ID", grey "forcing | window UTC"
IDS = {"Providence, RI": "8454000", "Conimicut Light, RI": "8452944", "Quonset Pt, RI": "8454049",
       "Newport, RI": "8452660"}
fig.suptitle(STATION + (" — Station " + IDS[STATION] if STATION in IDS else ""), fontsize=15, fontweight="bold",
             y=0.985)
fig.text(0.5, 0.905, f"GFS | {t0:%b} {t0.day} {t0:%H:%M} – {t1:%b} {t1.day} {t1:%H:%M}, {t1.year} UTC",
         ha="center", fontsize=11.5, color="0.25")

# ---- speed
ax = fig.add_subplot(gs[0, 0])
ax.plot(p.time, p.speed_ms, color="C0", lw=1.8)
ax.set_ylabel("Speed\n(m/s)"); ax.set_ylim(0, p.speed_ms.max() * 1.15); ax.grid(alpha=0.3)
ax.set_xlim(t0, t1); ax.tick_params(labelbottom=False)

# ---- direction (dots: no false jump at north)
axd = fig.add_subplot(gs[1, 0], sharex=ax)
axd.scatter(p.time, p.dir_from_deg, s=5, color="C0", zorder=3)
axd.set_ylim(-20, 380); axd.set_yticks([0, 90, 180, 270, 360]); axd.set_yticklabels(["N", "E", "S", "W", "N"])
axd.set_ylabel("Wind\nfrom"); axd.grid(alpha=0.3); axd.tick_params(labelbottom=False)

# ---- barbs, chessboard: one column per day, rows 00Z-18Z
axv = fig.add_subplot(gs[2, 0], sharex=ax)
ROWS = [0, 6, 12, 18]
day = p.time.dt.floor("D")
colx = {dd: g.min() + (g.max() - g.min()) / 2 if len(g) < 24 else dd + pd.Timedelta(hours=12)
        for dd, g in p.time.groupby(day)}
for c, (dd, g) in enumerate(p.time.groupby(day)):
    xa = mdates.date2num(max(dd, t0)); xb = mdates.date2num(min(dd + pd.Timedelta(days=1), t1))
    for r in range(len(ROWS)):
        axv.add_patch(Rectangle((xa, r - 0.5), xb - xa, 1, facecolor="#E8EEF4" if (c + r) % 2 == 0 else "white",
                                edgecolor="#C8D2DC", lw=0.5, zorder=0))
for r, hr in enumerate(ROWS):
    k = p[p.time.dt.hour == hr]
    xs = mdates.date2num(pd.Series([colx[dd] for dd in k.time.dt.floor("D")]))
    axv.barbs(xs, np.full(len(k), r), k.u10, k.v10, color="C0", length=5.2, linewidth=1.0, pivot="middle",
              barb_increments=dict(half=5, full=10, flag=50), sizes=dict(emptybarb=0.1), zorder=3, clip_on=False)
axv.set_ylim(-0.5, len(ROWS) - 0.5)
axv.set_yticks(range(len(ROWS))); axv.set_yticklabels([f"{hr:02d}Z" for hr in ROWS])
axv.tick_params(axis="y", length=0); axv.set_ylabel("Barbs")
axv.xaxis.set_major_locator(mdates.DayLocator()); axv.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

# ---- right: rose (rows 0-1)
BINS = [0, 2, 4, 6, 8, 99]; LAB = ["< 2", "2-4", "4-6", "6-8", "> 8"]
COLS = plt.cm.YlOrRd(np.linspace(0.25, 0.95, 5)); NS = 16; W = 2 * np.pi / NS
sec = ((p.dir_from_deg + 360 / NS / 2) % 360 // (360 / NS)).astype(int).values
sub = gs[0:2, 1].subgridspec(1, 2, width_ratios=[3.0, 1], wspace=0.22)
ra = fig.add_subplot(sub[0], projection="polar")
ra.set_theta_zero_location("N"); ra.set_theta_direction(-1)
base = np.zeros(NS)
for j in range(5):
    m = (p.speed_ms.values >= BINS[j]) & (p.speed_ms.values < BINS[j + 1])
    val = np.array([(m & (sec == s_)).sum() for s_ in range(NS)]) / len(p) * 100
    ra.bar(np.arange(NS) * W, val, width=W * 0.92, bottom=base, color=COLS[j], edgecolor="white", lw=0.5, zorder=3)
    base += val
step = 5 if base.max() <= 25 else 10
rmax = int(np.ceil(base.max() / step) * step)
ra.set_ylim(0, rmax); ra.set_yticks(list(range(step, rmax + 1, step)))
ra.set_yticklabels([f"{v}%" for v in range(step, rmax + 1, step)], fontsize=7.5, color="grey")
ra.set_rlabel_position(157.5)
ra.set_xticks(np.arange(0, 2 * np.pi, np.pi / 2)); ra.set_xticklabels(["N", "E", "S", "W"], fontsize=9.5)
ra.tick_params(axis="x", pad=-3)
ra.grid(color="#DDDDDD", lw=0.6); ra.spines["polar"].set_color("#B0B0B0"); ra.set_facecolor("#FAFAFA")
ra.set_title("Wind rose (% of hours)", fontsize=10, fontweight="bold", pad=16)
al = fig.add_subplot(sub[1]); al.axis("off")
al.legend(handles=[Patch(color=c, label=l) for c, l in zip(COLS, LAB)], loc="center left", fontsize=9,
          frameon=False, handlelength=1.4, handleheight=1.1, title="m/s", title_fontsize=9, borderaxespad=0)

# ---- right: barb key (row 2)
ak = fig.add_subplot(gs[2, 1]); ak.set_xlim(0, 1); ak.set_ylim(0, 1); ak.axis("off")
ak.add_patch(Rectangle((0.02, 0.03), 0.96, 0.94, facecolor="#F4F7FA", edgecolor="#C8D2DC", lw=0.8))
ak.text(0.07, 0.9, "Barbs (m/s)", fontsize=11, fontweight="bold", va="top")
for i, (spd, lab) in enumerate([(5, "5 half"), (10, "10 full"), (15, "15"), (50, "50 flag")]):
    x0, y0 = (0.17, 0.5) if i < 2 else (0.62, 0.5)
    yy = 0.64 - (i % 2) * 0.3
    xx = 0.17 if i < 2 else 0.6
    ak.barbs([xx], [yy], [spd], [0], length=6, color="C0", linewidth=1.2, pivot="middle",
             barb_increments=dict(half=5, full=10, flag=50))
    ak.text(xx + 0.11, yy, lab, va="center", fontsize=10.5)
ak.text(0.07, 0.07, "staff points to where wind comes from", fontsize=8.5, color="0.4", va="bottom")

fig.savefig(OUT, dpi=220)
print(OUT)
