#!/usr/bin/env python3
"""
Animate a RICHAMP_wind.nc file over the Rhode Island map background.

Each frame shows wind speed (shaded), wind direction (arrows) and a mesh overlay:
by default the GFS 0.25-degree source grid, or an ADCIRC mesh if --fort14 is given.

Usage:
    python wind_video.py RICHAMP_wind.nc                       # -> RICHAMP_wind.mp4
    python wind_video.py RICHAMP_wind.nc -o ri_wind.mp4 --fps 6
    python wind_video.py RICHAMP_wind.nc --fort14 fort.14       # ADCIRC mesh overlay
    python wind_video.py RICHAMP_wind.nc --gif                  # GIF instead of MP4

Requires: matplotlib netCDF4 numpy pillow, and ffmpeg on PATH for MP4 output.
"""
import argparse
import datetime
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.animation as animation  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import netCDF4  # noqa: E402
import numpy  # noqa: E402
from matplotlib.tri import Triangulation  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# RHODE_ISLAND_CHAMP bounds from MAP_BACKGROUND_BOUNDS.txt: W E / N S
RI_CHAMP_BOUNDS = (-71.9050164752, -71.1307245329, 42.000010143316864, 41.1192500979)
BASE_DATE = datetime.datetime(1990, 1, 1)


def read_fort14(path, bounds):
    """Return a Triangulation of the ADCIRC mesh elements that fall inside bounds."""
    w, e, n, s = bounds
    with open(path) as f:
        f.readline()
        ne, nn = (int(x) for x in f.readline().split()[:2])
        xy = numpy.empty((nn, 2))
        for i in range(nn):
            parts = f.readline().split()
            xy[i] = float(parts[1]), float(parts[2])
        tri = numpy.empty((ne, 3), dtype=int)
        for i in range(ne):
            parts = f.readline().split()
            tri[i] = int(parts[2]) - 1, int(parts[3]) - 1, int(parts[4]) - 1
    inside = (xy[:, 0] >= w) & (xy[:, 0] <= e) & (xy[:, 1] >= s) & (xy[:, 1] <= n)
    keep = inside[tri].all(axis=1)
    return Triangulation(xy[:, 0], xy[:, 1], tri[keep])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wind", help="RICHAMP_wind.nc-style file (group Main: time, lon, lat, spd, dir)")
    ap.add_argument("-o", "--out", help="output file (default: <wind name>.mp4 or .gif)")
    ap.add_argument("--background", default=os.path.join(HERE, "RhodeIslandChamp.png"))
    ap.add_argument("--bounds", type=float, nargs=4, metavar=("W", "E", "N", "S"), default=RI_CHAMP_BOUNDS,
                    help="background image bounds and plot extent")
    ap.add_argument("--background-flipped", type=lambda v: v.lower() in ("1", "true", "yes"), default=True,
                    help="the repo's map PNGs are stored south-up (Grapper.py draws them with a N/S-swapped extent); "
                         "pass false for a normal north-up image")
    ap.add_argument("--fort14", help="ADCIRC fort.14 to draw as the mesh instead of the GFS grid")
    ap.add_argument("--gfs-res", type=float, default=0.25, help="GFS grid spacing for the mesh overlay (deg)")
    ap.add_argument("--stride", type=int, default=4, help="subsample the wind grid by this factor for shading")
    ap.add_argument("--arrows", type=int, default=22, help="approximate number of arrows across the width")
    ap.add_argument("--vmax", type=float, default=30.0, help="top of the wind speed color scale, m/s")
    ap.add_argument("--fps", type=int, default=8)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--gif", action="store_true", help="write an animated GIF instead of MP4")
    ap.add_argument("--title", default="GFS 10 m wind", help="title prefix")
    args = ap.parse_args()

    out = args.out or os.path.splitext(os.path.basename(args.wind))[0] + (".gif" if args.gif else ".mp4")
    w, e, n, s = args.bounds

    nc = netCDF4.Dataset(args.wind)
    m = nc["Main"]
    lon = numpy.array(m["lon"][:])
    lat = numpy.array(m["lat"][:])
    times = [BASE_DATE + datetime.timedelta(minutes=float(t)) for t in m["time"][:]]

    # Index ranges covering the plot extent (plus one cell of margin)
    ii = numpy.where((lon >= w) & (lon <= e))[0]
    jj = numpy.where((lat >= s) & (lat <= n))[0]
    i0, i1 = max(ii[0] - 1, 0), min(ii[-1] + 2, lon.size)
    j0, j1 = max(jj[0] - 1, 0), min(jj[-1] + 2, lat.size)
    st = args.stride
    lon_s, lat_s = lon[i0:i1:st], lat[j0:j1:st]
    qs = max(1, (i1 - i0) // args.arrows)
    lon_q, lat_q = lon[i0:i1:qs], lat[j0:j1:qs]

    def frame_data(k):
        spd = numpy.array(m["spd"][k, j0:j1, i0:i1])
        drc = numpy.deg2rad(numpy.array(m["dir"][k, j0:j1, i0:i1]))
        u = -spd * numpy.sin(drc)  # dir is "coming from", meteorological convention
        v = -spd * numpy.cos(drc)
        return spd[::st, ::st], u[::qs, ::qs], v[::qs, ::qs]

    data_aspect = 1 / numpy.cos(numpy.deg2rad((n + s) / 2))  # y units per x unit so degrees look square on the ground
    # Fixed layout: axes box [left, bottom, width, height] in figure fractions; figure height follows the map aspect
    left, right, bottom, top = 0.09, 0.84, 0.06, 0.95
    fig_w = 8.0
    ax_h_in = (right - left) * fig_w * (n - s) * data_aspect / (e - w)
    fig_h = ax_h_in / (top - bottom)
    fig = plt.figure(figsize=(fig_w, fig_h))
    ax = fig.add_axes([left, bottom, right - left, top - bottom])
    cax = fig.add_axes([right + 0.02, bottom + 0.1 * (top - bottom), 0.025, 0.8 * (top - bottom)])
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)
    ax.set_aspect(data_aspect)
    if os.path.exists(args.background):
        img = plt.imread(args.background)
        if args.background_flipped:
            img = numpy.flipud(img)
        ax.imshow(img, extent=(w, e, s, n), aspect=data_aspect, zorder=0)  # imshow would otherwise reset aspect to 1
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")

    spd0, u0, v0 = frame_data(0)
    mesh = ax.pcolormesh(lon_s, lat_s, spd0, cmap="YlOrRd", vmin=0, vmax=args.vmax, alpha=0.55,
                         shading="nearest", zorder=1)
    quiv = ax.quiver(lon_q, lat_q, u0, v0, color="#1a1a1a", scale=args.vmax * 18, width=0.0022,
                     pivot="middle", zorder=3)
    cb = fig.colorbar(mesh, cax=cax)
    cb.set_label("Wind speed (m/s)")

    if args.fort14:
        tri = read_fort14(args.fort14, args.bounds)
        ax.triplot(tri, color="white", lw=0.25, alpha=0.6, zorder=2)
        mesh_label = f"ADCIRC mesh ({os.path.basename(args.fort14)})"
    else:
        r = args.gfs_res
        for x in numpy.arange(numpy.floor(w / r) * r, e + r, r):
            ax.axvline(x, color="white", lw=0.8, alpha=0.7, zorder=2)
        for y in numpy.arange(numpy.floor(s / r) * r, n + r, r):
            ax.axhline(y, color="white", lw=0.8, alpha=0.7, zorder=2)
        gx, gy = numpy.meshgrid(numpy.arange(numpy.floor(w / r) * r, e + r, r), numpy.arange(numpy.floor(s / r) * r, n + r, r))
        ax.plot(gx.ravel(), gy.ravel(), "o", color="white", ms=4, zorder=2)
        mesh_label = f"GFS {r}° grid"
    ax.text(0.01, 0.01, f"mesh: {mesh_label}", transform=ax.transAxes, fontsize=8, color="white",
            bbox=dict(facecolor="black", alpha=0.5, pad=2, lw=0), zorder=4)

    title = ax.set_title("")

    def update(k):
        spd, u, v = frame_data(k)
        mesh.set_array(spd.ravel())
        quiv.set_UVC(u, v)
        title.set_text(f"{args.title}  {times[k]:%Y-%m-%d %H:%M} UTC  (+{(times[k] - times[0]).total_seconds() / 3600:.0f} h)  max {spd.max():.1f} m/s")
        return mesh, quiv, title

    nframes = len(times)
    if args.gif:
        writer = animation.PillowWriter(fps=args.fps)
    else:
        writer = animation.FFMpegWriter(fps=args.fps, codec="libx264",
                                        extra_args=["-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"])
    with writer.saving(fig, out, dpi=args.dpi):
        for k in range(nframes):
            update(k)
            writer.grab_frame()
            if k % 10 == 0 or k == nframes - 1:
                print(f"  frame {k + 1}/{nframes}", flush=True)
    nc.close()
    print(f"Wrote {out}: {nframes} frames at {args.fps} fps ({nframes / args.fps:.0f} s)")


if __name__ == "__main__":
    main()
