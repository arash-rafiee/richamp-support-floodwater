import os
import json
import math
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
from matplotlib.cm import ScalarMappable
from matplotlib.figure import Figure
from matplotlib.tri import Triangulation
from datetime import datetime, timezone
import imageio
import re
import gc
from geographiclib.geodesic import Geodesic


SMALL_SIZE = 14
MEDIUM_SIZE = 18
BIGGER_SIZE = 22

plt.rc('font', size=SMALL_SIZE)          # controls default text sizes
plt.rc('axes', titlesize=SMALL_SIZE)     # fontsize of the axes title
plt.rc('axes', labelsize=MEDIUM_SIZE)    # fontsize of the x and y labels
plt.rc('xtick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('ytick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('legend', fontsize=SMALL_SIZE)    # legend fontsize
plt.rc('figure', titlesize=BIGGER_SIZE)  # fontsize of the figure title

_bannerInstalled = False


def stampFigureBanner(fig, bannerLines):
    """Reserve space at the top of fig and draw bannerLines there.

    The figure is grown by the banner height and every existing axes is moved
    back to the inch geometry it had before the resize, so the banner never
    lands on top of an axes title.
    """
    if getattr(fig, "richampBannerDrawn", False):
        return
    fig.richampBannerDrawn = True
    pad = 0.12          # inches of whitespace above and below the banner
    lineHeight = 0.28   # inches per banner line
    bannerHeight = 2 * pad + lineHeight * len(bannerLines)
    width, height = fig.get_size_inches()
    newHeight = height + bannerHeight
    fig.set_size_inches(width, newHeight, forward=False)
    scale = height / newHeight
    for ax in fig.axes:
        box = ax.get_position()
        ax.set_position([box.x0, box.y0 * scale, box.width, box.height * scale])
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:  # a backend that cannot measure text; skip the fitting
        renderer = None
    maxWidth = 0.98 * width * fig.dpi
    for index, line in enumerate(bannerLines):
        text = fig.text(
            0.5,
            1.0 - (pad + lineHeight * index) / newHeight,
            line,
            ha="center",
            va="top",
            fontsize=SMALL_SIZE if index == 0 else SMALL_SIZE - 3,
            fontweight="bold" if index == 0 else "normal",
        )
        # A long storm name overruns the narrower figures, so shrink to fit
        while renderer is not None and text.get_fontsize() > 6:
            if text.get_window_extent(renderer=renderer).width <= maxWidth:
                break
            text.set_fontsize(text.get_fontsize() - 1)


def installFigureBanner(banner):
    """Draw banner at the top of every figure Grapher saves.

    Wrapping Figure.savefig stamps all of the graphs from one place instead of
    touching each of the ~78 individual title/savefig pairs.
    """
    global _bannerInstalled
    if not banner or _bannerInstalled:
        return
    bannerLines = banner.split("\n")
    originalSavefig = Figure.savefig

    def savefigWithBanner(self, *args, **kwargs):
        stampFigureBanner(self, bannerLines)
        return originalSavefig(self, *args, **kwargs)

    Figure.savefig = savefigWithBanner
    _bannerInstalled = True


MAP_VIDEO_FPS = 10

# ---------------------------------------------------------------------------
# Water-surface elevation video (--maps). Only the look is set here; the
# ADCIRC values, wet/dry masking and timing come straight from the run.
# ---------------------------------------------------------------------------
WATER_VIDEO_HEIGHT_PX = 1080         # output frame height
# None fits the frame width to the map, so a tall region has no empty side
# bands. Set e.g. 1920 for a fixed 16:9 frame (the map is centered in it).
WATER_VIDEO_WIDTH_PX = None
WATER_VIDEO_DPI = 100                # with the frame size this sets the font scale
WATER_VIDEO_FPS = MAP_VIDEO_FPS
WATER_VIDEO_EXTENT = None            # [west, east, south, north]; None = --backgroundChoice axis
# Color limits are the same for every frame. vcenter=0 keeps 0 m at the
# blue/red boundary even when the scale is not symmetric, so set-down (blue)
# and surge (red) read apart.
# None takes the limit from the run's wet extremes (over all frames, rounded
# outward to WATER_VIDEO_AUTO_ROUND m); set numbers, e.g. -1.0 and 3.0, to
# compare runs on one scale.
WATER_VIDEO_VMIN = None
WATER_VIDEO_VMAX = None
WATER_VIDEO_AUTO_ROUND = 0.25
WATER_VIDEO_TICK_STEP = None         # None picks 0.1/0.25/0.5/1 m from the range
WATER_VIDEO_CMAP = "RdBu_r"
WATER_VIDEO_CMAP_TRIM = 0.12         # fraction of the pale middle removed each side of 0; 0 keeps it
WATER_VIDEO_DATUM = "NAVD88"         # RICHAMP zeta is m NAVD88; "" leaves the datum out of the label
WATER_VIDEO_TITLE = "ADCIRC Water-Surface Elevation"   # "" hides it
WATER_VIDEO_TIME_FORMAT = "%Y-%m-%d %H:%M UTC"
# Extra lines under the timestamp, off by default, e.g.
# ["GFS 12Z Forecast", "Forecast initialized: 2026-09-24 12Z"]
WATER_VIDEO_ANNOTATIONS = []
WATER_VIDEO_FIELD_ALPHA = 0.95       # opacity of the ADCIRC field
WATER_VIDEO_LAND_COLOR = "#c4c4c4"   # dry land / outside the mesh; mid gray stays gray if a player brightens video
WATER_VIDEO_COASTLINE = True         # thin line around the always-wet area
WATER_VIDEO_COASTLINE_COLOR = "#555555"
# Grayscale street basemap (state lines, roads, gray land) from
# tools/fetch_street_basemap.py, used when <background>StreetGray.png exists
WATER_VIDEO_STREET_BASEMAP = True
WATER_VIDEO_STREET_SHADE = 0.8       # multiplies the basemap brightness; lower is darker gray land
WATER_VIDEO_STREET_CREDIT = ("Basemap: Esri World Street Map, grayscale "
                             "(Esri, HERE, Garmin, OpenStreetMap contributors, GIS user community)")
WATER_VIDEO_STATE_BORDERS = "StateBorders.json"   # from tools/fetch_street_basemap.py --borders; "" hides them
WATER_VIDEO_BASEMAP = False          # True draws the --backgroundChoice photo under the field
WATER_VIDEO_BASEMAP_ALPHA = 0.35     # how strongly the faded photo shows
WATER_VIDEO_FONT = "DejaVu Sans"
WATER_VIDEO_FONT_SIZES = {"title": 13, "time": 20, "annotation": 12,
                          "cbar_label": 14, "cbar_ticks": 11, "ticks": 11, "legend": 11, "station": 9,
                          "city": 11, "water": 11, "region": 13, "scale": 10}
# Reference labels drawn on the map (only those inside the extent show).
# (name, longitude, latitude, kind, horizontal alignment); kind "city" gets a
# dot, "water" is italic blue-gray, "region" is light gray capitals.
WATER_VIDEO_PLACES = [
    ("Providence", -71.4128, 41.8240, "city", "left"),
    ("Warwick", -71.4162, 41.7001, "city", "right"),
    ("Bristol", -71.2662, 41.6771, "city", "left"),
    ("Fall River", -71.1550, 41.7015, "city", "right"),
    ("Newport", -71.3128, 41.4901, "city", "left"),
    ("Narragansett", -71.4495, 41.4501, "city", "right"),
    ("Westerly", -71.8273, 41.3776, "city", "left"),
    ("New London", -72.0995, 41.3557, "city", "right"),
    ("Montauk", -71.9545, 41.0359, "city", "left"),
    ("Block Island", -71.5330, 41.1650, "water", "left"),
    ("Narragansett\nBay", -71.4000, 41.5850, "water", "center"),
    ("Rhode Island Sound", -71.3300, 41.3400, "water", "center"),
    ("Block Island Sound", -71.7300, 41.2300, "water", "center"),
    ("RHODE ISLAND", -71.6200, 41.7800, "region", "center"),
    ("MA", -71.2300, 41.9300, "region", "center"),
    ("CT", -71.8550, 41.7000, "region", "center"),
]
WATER_VIDEO_PLACES_MAX_DEGREES = 2.5  # place labels only on maps narrower than this (they pile up on basin maps)
WATER_VIDEO_SCALE_BAR_KM = None      # None picks a round length for the extent; 0 hides it


def findFfmpeg():
    """Path of an ffmpeg program that is already present, or None. Nothing is installed."""
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def writeVideo(outputBase, makeFrames, width, height, fps):
    """Write frames as outputBase.mp4 (H.264) when ffmpeg exists, else outputBase.avi (MJPEG).

    makeFrames() returns a fresh iterator of (height, width, 3) uint8 frames; it
    is called again for the .avi if the ffmpeg encode fails part way.
    """
    import subprocess
    ffmpeg = findFfmpeg()
    if ffmpeg:
        outputFile = outputBase + ".mp4"
        command = [ffmpeg, "-y", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", "-metadata", "title=ADCIRC water-surface elevation", outputFile]
        process = None
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
            for frame in makeFrames():
                process.stdin.write(np.ascontiguousarray(frame).tobytes())
            process.stdin.close()
            if process.wait() == 0:
                print("Wrote " + outputFile, flush=True)
                return outputFile
        except (OSError, ValueError) as error:
            print("ffmpeg failed (" + str(error) + ")", flush=True)
            if process is not None:
                process.kill()
                process.wait()
        print("ffmpeg could not write " + outputFile + "; writing an .avi instead", flush=True)
        if os.path.exists(outputFile):
            os.remove(outputFile)
    outputFile = outputBase + ".avi"
    writeMjpegAvi(outputFile, makeFrames(), width, height, fps)
    print("Wrote " + outputFile, flush=True)
    return outputFile


def writeMjpegAvi(outputFile, frames, width, height, fps):
    """Write RGB uint8 frames as a Motion JPEG .avi using only Pillow.

    Needs no ffmpeg or extra package. Each frame is its own JPEG inside a
    plain RIFF/AVI 1.0 container with an idx1 index, which VLC, Windows Media
    Player and PowerPoint all play.
    """
    import struct
    from io import BytesIO
    from PIL import Image

    def chunk(fourcc, payload):
        return fourcc + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")

    with open(outputFile, "wb") as f:
        # Headers are written with a zero frame count and patched at the end
        def headers(frameCount, maxFrameBytes):
            avih = struct.pack("<10I4I", int(1e6 / fps), maxFrameBytes * fps, 0, 0x10, frameCount,
                               0, 1, maxFrameBytes, width, height, 0, 0, 0, 0)
            strh = (b"vidsMJPG" + struct.pack("<IHHIIIIIIiI", 0, 0, 0, 0, 1, fps, 0, frameCount,
                                              maxFrameBytes, -1, 0)
                    + struct.pack("<4h", 0, 0, width, height))
            strf = struct.pack("<IiiHH4sIiiII", 40, width, height, 1, 24, b"MJPG", width * height * 3, 0, 0, 0, 0)
            strl = b"LIST" + struct.pack("<I", 4 + len(chunk(b"strh", strh)) + len(chunk(b"strf", strf))) + b"strl" \
                + chunk(b"strh", strh) + chunk(b"strf", strf)
            hdrl = b"hdrl" + chunk(b"avih", avih) + strl
            return b"LIST" + struct.pack("<I", len(hdrl)) + hdrl

        f.write(b"RIFF\0\0\0\0AVI ")
        f.write(headers(0, 0))
        moviStart = f.tell()
        f.write(b"LIST\0\0\0\0movi")
        index = []
        maxFrameBytes = 0
        for frame in frames:
            buffer = BytesIO()
            Image.fromarray(frame).save(buffer, format="JPEG", quality=92)
            data = buffer.getvalue()
            index.append((f.tell() - (moviStart + 8), len(data)))
            maxFrameBytes = max(maxFrameBytes, len(data))
            f.write(chunk(b"00dc", data))
        moviEnd = f.tell()
        f.write(b"idx1" + struct.pack("<I", 16 * len(index)))
        for offset, size in index:
            f.write(b"00dc" + struct.pack("<III", 0x10, offset, size))
        fileEnd = f.tell()
        f.seek(4)
        f.write(struct.pack("<I", fileEnd - 8))
        f.seek(12)
        f.write(headers(len(index), maxFrameBytes))
        f.seek(moviStart + 4)
        f.write(struct.pack("<I", moviEnd - moviStart - 8))


def writeMapAnimation(graph_directory, framePrefix, frameCount, name):
    """Stitch graph_directory/<framePrefix><i>.png into <name>.avi and remove the frames.

    The floodwater conda environment has no video encoder (no ffmpeg), so the
    video is a Motion JPEG .avi written with Pillow alone. bbox_inches="tight"
    frames can differ by a few pixels as the time label changes, so each frame
    is padded with white to the largest size, rounded up to a multiple of 16.
    Some players (KMPlayer) garble or refuse MJPEG at widths such as 1130, and
    the tight box gives a different size on every run.
    """
    from PIL import Image
    frameFiles = [graph_directory + framePrefix + str(index) + ".png" for index in range(frameCount)]
    if not frameFiles:
        return
    sizes = []
    for frameFile in frameFiles:
        with Image.open(frameFile) as frame:
            sizes.append(frame.size)
    width = -(-max(size[0] for size in sizes) // 16) * 16
    height = -(-max(size[1] for size in sizes) // 16) * 16

    def paddedFrames():
        for frameFile in frameFiles:
            with Image.open(frameFile) as frame:
                image = np.asarray(frame.convert("RGB"))
            canvas = np.full((height, width, 3), 255, dtype=np.uint8)
            canvas[:image.shape[0], :image.shape[1]] = image
            yield canvas

    outputFile = graph_directory + name + ".avi"
    writeMjpegAvi(outputFile, paddedFrames(), width, height, MAP_VIDEO_FPS)
    print("Wrote " + outputFile, flush=True)
    for frameFile in frameFiles:
        os.remove(frameFile)


def loadMapArrays(dataset):
    """Swap {"npy": path} entries Reader.saveMapArrays left in map_data back to numpy arrays."""
    mapData = dataset.get("map_data") if isinstance(dataset, dict) else None
    if mapData:
        for key, value in mapData.items():
            if isinstance(value, dict) and "npy" in value:
                mapData[key] = np.load(value["npy"])
    return dataset


class MeshRasterizer:
    """Linear interpolation of node values onto a fixed pixel grid of the plot area.

    Each pixel's containing triangle and barycentric weights are found once;
    after that a frame is a gather and a weighted sum. This draws the same
    linear-within-triangle shading as tripcolor(shading="gouraud"). A pixel is
    blank when its triangle is masked or has a node at the -99999 dry value.
    """

    DRY = -99999.0

    def __init__(self, longitudes, latitudes, triangles, maskedTriangles, plotAxis, width=1200):
        longitudes = np.asarray(longitudes, dtype=np.float64)
        latitudes = np.asarray(latitudes, dtype=np.float64)
        triangles = np.asarray(triangles)
        west, east, south, north = plotAxis
        self.extent = [west, east, south, north]
        self.width = int(width)
        self.height = max(1, int(round(self.width * (north - south) / (east - west))))
        pixelX = west + (np.arange(self.width) + 0.5) * (east - west) / self.width
        pixelY = south + (np.arange(self.height) + 0.5) * (north - south) / self.height
        gridX, gridY = np.meshgrid(pixelX, pixelY)
        gridX = gridX.ravel()
        gridY = gridY.ravel()
        triangleIds = Triangulation(longitudes, latitudes, triangles=triangles).get_trifinder()(gridX, gridY)
        if len(maskedTriangles):
            masked = np.asarray(maskedTriangles, dtype=bool)
            triangleIds = np.where((triangleIds >= 0) & ~masked[np.maximum(triangleIds, 0)], triangleIds, -1)
        inside = triangleIds >= 0
        self.pixels = np.flatnonzero(inside)
        self.nodes = triangles[triangleIds[inside]]
        x0, x1, x2 = (longitudes[self.nodes[:, k]] for k in range(3))
        y0, y1, y2 = (latitudes[self.nodes[:, k]] for k in range(3))
        px = gridX[inside]
        py = gridY[inside]
        det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / det
        w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / det
        self.weights = np.stack([w0, w1, 1.0 - w0 - w1], axis=1).astype(np.float32)

    def values(self, nodeValues):
        """(height, width) float32 image of nodeValues, NaN where nothing is drawn."""
        nodeValues = np.asarray(nodeValues, dtype=np.float32)
        cornerValues = nodeValues[self.nodes]
        dry = (cornerValues == self.DRY).any(axis=1)
        image = np.full(self.width * self.height, np.nan, dtype=np.float32)
        image[self.pixels] = np.where(dry, np.nan, (cornerValues * self.weights).sum(axis=1))
        return image.reshape(self.height, self.width)

    def imshow(self, ax, nodeValues, **kwargs):
        return ax.imshow(self.values(nodeValues), extent=self.extent, origin="lower",
                         interpolation="nearest", **kwargs)


def waterStatistics(modelTimes, modelValues, obsTimes, obsValues, maxGapSeconds=3600):
    """Model-vs-observation error statistics at the model output times.

    The observation is linearly interpolated to each model time between its two
    neighbouring readings; times outside the record, across a gap longer than
    maxGapSeconds, or where either value is missing or dry (-99999) are skipped.
    Returns {"n", "bias", "mae", "rmse", "r"} with bias = mean(model - observed),
    or None when nothing pairs.
    """
    def seconds(times):
        return np.array([t.timestamp() if hasattr(t, "timestamp") else float(t) for t in times], dtype=np.float64)

    def clean(values):
        values = np.array([np.nan if v is None else v for v in values], dtype=np.float64)
        values[values < -9000] = np.nan
        return values

    if len(modelTimes) == 0 or len(obsTimes) < 2:
        return None
    modelSeconds, model = seconds(modelTimes), clean(modelValues)
    obsSeconds, obs = seconds(obsTimes), clean(obsValues)
    keep = np.isfinite(obs)
    obsSeconds, obs = obsSeconds[keep], obs[keep]
    order = np.argsort(obsSeconds)
    obsSeconds, obs = obsSeconds[order], obs[order]
    if len(obsSeconds) < 2:
        return None
    # obsSeconds[before] <= model time < obsSeconds[after]; a time on the last reading uses it directly
    after = np.searchsorted(obsSeconds, modelSeconds, side="right")
    inside = (after > 0) & ((after < len(obsSeconds)) | (modelSeconds == obsSeconds[-1]))
    after = np.clip(after, 1, len(obsSeconds) - 1)
    before = after - 1
    gap = obsSeconds[after] - obsSeconds[before]
    exact = (obsSeconds[before] == modelSeconds) | (obsSeconds[after] == modelSeconds)
    inside &= (gap <= maxGapSeconds) | exact
    weight = np.where(gap > 0, (modelSeconds - obsSeconds[before]) / np.where(gap > 0, gap, 1), 0)
    obsAtModel = obs[before] + weight * (obs[after] - obs[before])
    paired = inside & np.isfinite(model) & np.isfinite(obsAtModel)
    if not paired.any():
        return None
    error = model[paired] - obsAtModel[paired]
    n = int(paired.sum())
    r = float(np.corrcoef(model[paired], obsAtModel[paired])[0, 1]) if n > 2 else float("nan")
    return {"n": n, "bias": float(error.mean()), "mae": float(np.abs(error).mean()),
            "rmse": float(np.sqrt((error ** 2).mean())), "r": r}


def drawStatisticsTable(ax, rows, observedLabel):
    """Draw a "Full-period statistics" table on an empty axes.

    rows are (label, color, stats) with stats from waterStatistics.
    """
    ax.axis("off")
    # Compact block on the left, like a journal table, rather than spread over the full width
    columns = [("Model", 0.0, "left"), ("n", 0.26, "right"), ("Bias", 0.35, "right"),
               ("MAE", 0.43, "right"), ("RMSE", 0.51, "right"), ("r", 0.58, "right")]
    tableRight = columns[-1][1]
    lineHeight = 1.0 / (len(rows) + 2.6)
    y = 1.0
    ax.text(0.0, y, "Full-period statistics vs " + observedLabel, transform=ax.transAxes, ha="left", va="top",
            fontsize=10.5, fontweight="bold", color="0.15")
    y -= lineHeight
    for name, x, align in columns:
        ax.text(x, y, name, transform=ax.transAxes, ha=align, va="top", fontsize=9.5, color="0.35")
    ax.plot([0.0, tableRight], [y - 0.8 * lineHeight] * 2, transform=ax.transAxes, color="0.75", linewidth=0.6)
    for label, color, stats in rows:
        y -= lineHeight
        values = [str(stats["n"]), f"{stats['bias']:+.3f}", f"{stats['mae']:.3f}", f"{stats['rmse']:.3f}",
                  "–" if not np.isfinite(stats["r"]) else f"{stats['r']:.2f}"]
        ax.text(0.0, y - 0.2 * lineHeight, label, transform=ax.transAxes, ha="left", va="top", fontsize=10,
                fontweight="bold", color=color)
        for (name, x, align), value in zip(columns[1:], values):
            ax.text(x, y - 0.2 * lineHeight, value, transform=ax.transAxes, ha=align, va="top", fontsize=10,
                    color="0.15")
    ax.text(tableRight, 0.0, "Errors in m; bias = model − observed", transform=ax.transAxes, ha="right", va="bottom",
            fontsize=8.5, color="0.45")


def compactDateRange(start, end):
    """Format a date span as "Sep 13–20, 2026", widening as months/years differ."""
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    if start.day != end.day:
        return f"{start:%b} {start.day}–{end.day}, {end.year}"
    return f"{start:%b} {start.day}, {end.year}"


def bannerSubtitle(banner, fallbackStart=None, fallbackEnd=None):
    """Condense the two line storm banner into "GFS | Sep 13–20, 2026 UTC".

    The banner window is "YYYY-MM-DD HH:MM – YYYY-MM-DD HH:MM UTC"; when the
    banner has none, the plotted data's own first and last time is used.
    """
    lines = [line for line in (banner or "").split("\n") if line.strip()]
    source = ""
    start, end = fallbackStart, fallbackEnd
    for line in lines:
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", line)
        if len(dates) == 2:
            start = datetime.strptime(dates[0], "%Y-%m-%d")
            end = datetime.strptime(dates[1], "%Y-%m-%d")
        elif not source:
            source = line
    parts = []
    if source:
        parts.append(source)
    if start is not None and end is not None:
        parts.append(compactDateRange(start, end) + " UTC")
    return " | ".join(parts)


class Grapher:
    DATE_FORMAT = "%m/%d/%y-%HZ"    
    CONVERT_TO_WATER_DEPTH = False
        
    def extractLatitudeIndex(self, nodeIndex):
        return int(nodeIndex[1: nodeIndex.find(",")])
    
    def extractLongitudeIndex(self, nodeIndex):
        return int(nodeIndex[nodeIndex.find(",") + 1: nodeIndex.find(")")])
    
    def vectorSpeed(self, x,y):
        return math.sqrt(x**2 + y**2)
    
    def vectorDirection(self, x,y):
        degrees = math.degrees(math.atan2(-y,x))
        if(degrees < 0):
            return degrees + 360
        return degrees
    
    def unixTimeToDeltaHours(self, timestamp, startDate):
        startDateTimestamp = datetime.timestamp(startDate)
#         Difference in timestamp between startDate and 1938 date
        timestampDelta = timestamp - startDateTimestamp
#         startDateTimestamp - (-987120000.0)
#         return datetime.fromtimestamp(timestamp, timezone.utc)
#         timestamp = (-987120000.0) + timestampDelta
        return datetime.fromtimestamp(timestamp, timezone.utc)
        delta = datetime.fromtimestamp(timestamp, timezone.utc) - startDate
        return delta.total_seconds()/3600
    
    def extrapolateWindToTenMeterHeight(self, windVelocity, altitude):
        return windVelocity
    #     WIND_PROFILE_EXPONENT = 0.11
    #     return windVelocity * ((10.0/altitude)**WIND_PROFILE_EXPONENT)
    
    def writeWaterVideo(self, graph_directory, rasterizer, plotAxis, img):
        """Draw the water-surface elevation frames into one fixed 16:9 figure and save the video.

        Only presentation lives here: the field is the rasterizer's linear
        interpolation of the ADCIRC node values, dry nodes stay blank, and every
        frame shares one color normalization. Static artists (axes, colorbar,
        coastline, stations) are drawn once; each frame only swaps the field
        data, the timestamp and any runup lines.
        """
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.ticker import MaxNLocator, FuncFormatter
        from matplotlib import patheffects

        fontSizes = WATER_VIDEO_FONT_SIZES
        west, east, south, north = WATER_VIDEO_EXTENT or plotAxis
        if WATER_VIDEO_EXTENT:
            rasterizer = MeshRasterizer(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes,
                                        self.mapWaterTriangles, self.mapWaterMaskedTriangles, [west, east, south, north])

        vmin, vmax = WATER_VIDEO_VMIN, WATER_VIDEO_VMAX
        if vmin is None or vmax is None:
            # One range for the whole run (never per frame): the wet extremes over
            # every frame, rounded outward to WATER_VIDEO_AUTO_ROUND, at least one step each side of 0
            step = WATER_VIDEO_AUTO_ROUND
            low, high = np.inf, -np.inf
            for frameWaters in self.mapWaters:
                wet = np.asarray(frameWaters)
                wet = wet[wet != MeshRasterizer.DRY]
                if wet.size:
                    low, high = min(low, float(wet.min())), max(high, float(wet.max()))
            if not np.isfinite(low):
                low, high = -step, step
            if vmin is None:
                vmin = min(-step, math.floor(low / step) * step)
            if vmax is None:
                vmax = max(step, math.ceil(high / step) * step)
            print("Water video color range", vmin, "to", vmax, "m (run extremes", low, "to", high, ")", flush=True)
        cmap = plt.get_cmap(WATER_VIDEO_CMAP)
        if WATER_VIDEO_CMAP_TRIM > 0:
            # Drop the near-white middle of the diverging map so small set-up or
            # set-down is still clearly colored; 0 m is the sharp blue/red boundary
            half = 128
            cmap = mcolors.ListedColormap(np.vstack([cmap(np.linspace(0, 0.5 - WATER_VIDEO_CMAP_TRIM, half)),
                                                     cmap(np.linspace(0.5 + WATER_VIDEO_CMAP_TRIM, 1, half))]))
        if vmin < 0 < vmax:
            norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
        else:
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
        levelBoundaries = np.linspace(vmin, vmax, 101)
        tickStep = WATER_VIDEO_TICK_STEP
        if tickStep is None:
            span = vmax - vmin
            tickStep = 0.1 if span <= 1 else 0.25 if span <= 2 else 0.5 if span <= 5 else 1.0
        # Ticks on whole multiples of the step (so 0 is always one when it is in range)
        ticks = np.arange(math.ceil(vmin / tickStep - 1e-9), math.floor(vmax / tickStep + 1e-9) + 1) * tickStep
        label = "Water-Surface Elevation (m" + (" " + WATER_VIDEO_DATUM if WATER_VIDEO_DATUM else "") + ")"

        # True geographic shape: a degree of longitude is cos(latitude) as long as
        # a degree of latitude, so the map is drawn taller than it is wide here.
        lonScale = math.cos(math.radians((south + north) / 2))
        mapAspect = (north - south) / ((east - west) * lonScale)   # on-screen height / width

        # Layout in inches, left to right: lat ticks | map | colorbar | colorbar label.
        # The map takes the full frame height and the title/timestamp sit inside
        # it, so with WATER_VIDEO_WIDTH_PX = None the frame is only as wide as needed.
        dpi = WATER_VIDEO_DPI
        figH = WATER_VIDEO_HEIGHT_PX / dpi
        marginTop, marginBottom, marginLeft = 0.2, 0.45, 0.65
        cbarGap, cbarW, cbarLabelW = 0.18, 0.24, 1.25
        marginRight = cbarGap + cbarW + cbarLabelW
        mapH = figH - marginTop - marginBottom
        mapW = mapH / mapAspect
        if WATER_VIDEO_WIDTH_PX:
            figW = WATER_VIDEO_WIDTH_PX / dpi
            if mapW > figW - marginLeft - marginRight:   # a wide extent is limited by width instead
                mapW = figW - marginLeft - marginRight
                mapH = mapW * mapAspect
        else:
            # Rounded up to a multiple of 16 pixels; KMPlayer garbles MJPEG at some odd widths
            figW = math.ceil((marginLeft + mapW + marginRight) * dpi / 16) * 16 / dpi
        mapLeft = marginLeft + (figW - marginLeft - mapW - marginRight) / 2
        mapBottom = marginBottom + (figH - marginTop - marginBottom - mapH) / 2

        with plt.rc_context({"font.family": WATER_VIDEO_FONT}):
            fig = Figure(figsize=(figW, figH), dpi=dpi, facecolor="white")
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_axes([mapLeft / figW, mapBottom / figH, mapW / figW, mapH / figH])
            ax.set_facecolor(WATER_VIDEO_LAND_COLOR)

            streetMap = None
            if WATER_VIDEO_STREET_BASEMAP and self.backgroundMap:
                streetFile = os.path.splitext(self.backgroundMap)[0] + "StreetGray.png"
                if os.path.exists(streetFile):
                    streetMap = mpimg.imread(streetFile)
                    if streetMap.ndim == 3:
                        streetMap = streetMap[..., :3].mean(axis=2)
                    if streetMap.max() > 1.0:
                        streetMap = streetMap / 255.0
                    # Row 0 is the north edge; the file covers the --backgroundChoice axis exactly
                    bgWest, bgEast, bgNorth, bgSouth = self.backgroundAxis
                    ax.imshow(streetMap * WATER_VIDEO_STREET_SHADE, extent=(bgWest, bgEast, bgSouth, bgNorth),
                              origin="upper", cmap="gray", vmin=0, vmax=1, interpolation="antialiased", zorder=0)
                else:
                    print("No " + streetFile + "; run tools/fetch_street_basemap.py for a street basemap", flush=True)
            if streetMap is None and WATER_VIDEO_BASEMAP and img is not None:
                # Desaturated and lightened toward white so it gives context without competing
                photo = np.asarray(img, dtype=np.float32)[..., :3]
                if photo.max() > 1.0:
                    photo = photo / 255.0
                photo = 0.5 * photo + 0.5 * photo.mean(axis=2, keepdims=True)
                photo = 1.0 - WATER_VIDEO_BASEMAP_ALPHA * (1.0 - photo)
                ax.imshow(photo, extent=self.backgroundAxis, zorder=0)

            field = rasterizer.imshow(ax, self.mapWaters[0], cmap=cmap, norm=norm,
                                      alpha=WATER_VIDEO_FIELD_ALPHA, zorder=1)

            if WATER_VIDEO_COASTLINE:
                # Outline of the nodes that stay wet in every frame (the normal water
                # body); flooding shows as color beyond it. Drawn once, never changes.
                alwaysWet = np.ones(len(self.mapWaters[0]), dtype=bool)
                for frameWaters in self.mapWaters:
                    alwaysWet &= np.asarray(frameWaters) != MeshRasterizer.DRY
                wetFraction = rasterizer.values(alwaysWet.astype(np.float32))
                rWest, rEast, rSouth, rNorth = rasterizer.extent
                gridX = rWest + (np.arange(rasterizer.width) + 0.5) * (rEast - rWest) / rasterizer.width
                gridY = rSouth + (np.arange(rasterizer.height) + 0.5) * (rNorth - rSouth) / rasterizer.height
                if np.isfinite(wetFraction).any() and np.nanmax(wetFraction) > 0.5 > np.nanmin(wetFraction):
                    ax.contour(gridX, gridY, wetFraction, levels=[0.5], colors=WATER_VIDEO_COASTLINE_COLOR,
                               linewidths=0.6, zorder=2)

            bordersFile = os.path.join(os.path.dirname(os.path.abspath(__file__)), WATER_VIDEO_STATE_BORDERS)
            if WATER_VIDEO_STATE_BORDERS and os.path.exists(bordersFile):
                # Dashed state borders over a white underlay, drawn once
                with open(bordersFile) as file:
                    borders = json.load(file)
                # Only maps inside the area the file was made for; outside it the
                # borders are incomplete and show as stray pieces
                boxWest, boxSouth, boxEast, boxNorth = borders.get("box", [-180, -90, 180, 90])
                insideBox = boxWest <= west and east <= boxEast and boxSouth <= south and north <= boxNorth
                for line in (borders["lines"] if insideBox else []):
                    line = np.asarray(line)
                    if (line[:, 0].max() < west or line[:, 0].min() > east
                            or line[:, 1].max() < south or line[:, 1].min() > north):
                        continue
                    ax.plot(line[:, 0], line[:, 1], color="white", linewidth=2.6, alpha=0.8, zorder=2.5)
                    ax.plot(line[:, 0], line[:, 1], color="#333333", linewidth=1.2, dashes=(5, 2.5), zorder=2.6)

            if(self.meshExists):
                ax.scatter(self.assetLongitudes, self.assetLatitudes, label="Assets", zorder=3, marker="o", s=30,
                           color="#eb6834", edgecolors="white", linewidths=0.6)
            if(self.obsExists):
                ax.scatter(self.tideLongitudes, self.tideLatitudes, label="Observation stations", zorder=3, marker="o",
                           s=30, color="#256abf", edgecolors="white", linewidths=0.6)
                for tideIndex in range(len(self.tideLabels)):
                    ax.annotate(self.tideLabels[tideIndex], (self.tideLongitudes[tideIndex], self.tideLatitudes[tideIndex]),
                                xytext=(4, 3), textcoords="offset points", fontsize=fontSizes["station"], color="#222222",
                                zorder=4)

            ax.set_xlim(west, east)
            ax.set_ylim(south, north)
            ax.set_aspect(1 / lonScale, adjustable="box", anchor="C")
            # About 5-7 round ticks per side whatever the extent (1, 2 or 5 x 10^n
            # degrees), with just enough decimals to tell them apart
            for axis, span in ((ax.xaxis, east - west), (ax.yaxis, north - south)):
                axis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
                decimals = max(1, -math.floor(math.log10(span / 6)))
                axis.set_major_formatter(FuncFormatter(
                    lambda value, position, decimals=decimals: f"{value:.{decimals}f}".replace("-", "−")))
            ax.tick_params(labelsize=fontSizes["ticks"], colors="#444444", length=3, width=0.6)
            for spine in ax.spines.values():
                spine.set_linewidth(0.6)
                spine.set_color("#666666")

            # Colorbar the height of the map, just to its right, fixed for every frame
            cax = fig.add_axes([(mapLeft + mapW + cbarGap) / figW, mapBottom / figH, cbarW / figW, mapH / figH])
            colorbar = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax, boundaries=levelBoundaries,
                                    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2, ticks=ticks)
            colorbar.ax.yaxis.set_major_formatter(
                FuncFormatter(lambda value, position: "0" if abs(value) < 1e-9
                              else f"{value:.2f}".rstrip("0").rstrip(".").replace("-", "−")))
            colorbar.ax.tick_params(labelsize=fontSizes["cbar_ticks"], colors="#333333", length=3, width=0.6)
            colorbar.outline.set_linewidth(0.6)
            colorbar.set_label(label, fontsize=fontSizes["cbar_label"], color="#222222", labelpad=10)

            # White halo keeps labels readable over both land and the colored field
            halo = [patheffects.withStroke(linewidth=3, foreground="white")]

            # Reference places, drawn once
            places = WATER_VIDEO_PLACES if east - west <= WATER_VIDEO_PLACES_MAX_DEGREES else []
            for name, lon, lat, kind, align in places:
                if not (west <= lon <= east and south <= lat <= north):
                    continue
                if kind == "city":
                    ax.plot(lon, lat, "o", markersize=3.5, color="#222222", markeredgecolor="white",
                            markeredgewidth=0.6, zorder=4)
                    offset = {"left": 5, "right": -5}.get(align, 0)
                    ax.annotate(name, (lon, lat), xytext=(offset, 0), textcoords="offset points", ha=align,
                                va="center", fontsize=fontSizes["city"], color="#222222", path_effects=halo, zorder=5,
                                clip_on=True)
                elif kind == "water":
                    ax.text(lon, lat, name, ha=align, va="center", fontsize=fontSizes["water"], style="italic",
                            color="#2b4a6b", path_effects=halo, zorder=5, clip_on=True)
                else:
                    ax.text(lon, lat, name, ha=align, va="center", fontsize=fontSizes["region"], color="#666666",
                            path_effects=halo, zorder=5, clip_on=True)

            if streetMap is not None and WATER_VIDEO_STREET_CREDIT:
                # Required attribution, small in the lower-right corner of the map
                ax.annotate(WATER_VIDEO_STREET_CREDIT, (1, 0), xycoords="axes fraction", xytext=(-4, 3),
                            textcoords="offset points", ha="right", va="bottom", fontsize=6, color="#333333",
                            path_effects=halo, zorder=6)

            barKm = WATER_VIDEO_SCALE_BAR_KM
            if barKm is None:
                # Largest round length up to a fifth of the map width
                mapKm = (east - west) * 111.32 * lonScale
                barKm = max([length for length in (0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
                             if length <= mapKm / 5] or [0.05])
            if barKm:
                # Scale bar in the lower-left corner; length uses the map's mid-latitude
                barDegrees = barKm / (111.32 * lonScale)
                barX = west + 0.05 * (east - west)
                barY = south + 0.04 * (north - south)
                ax.plot([barX, barX + barDegrees], [barY, barY], color="#222222", linewidth=2.5,
                        solid_capstyle="butt", path_effects=halo, zorder=5)
                ax.annotate(f"{barKm:g} km" if barKm >= 1 else f"{barKm * 1000:g} m", (barX + barDegrees / 2, barY), xytext=(0, 5),
                            textcoords="offset points", ha="center", va="bottom", fontsize=fontSizes["scale"],
                            color="#222222", path_effects=halo, zorder=5)

            # Title, the one timestamp and optional annotations, stacked in the
            # upper-left corner of the map
            cursor = -8
            if WATER_VIDEO_TITLE:
                ax.annotate(self.titlePrefix + WATER_VIDEO_TITLE, (0, 1), xycoords="axes fraction", xytext=(10, cursor),
                            textcoords="offset points", ha="left", va="top", fontsize=fontSizes["title"],
                            color="#333333", path_effects=halo, zorder=6)
                cursor -= fontSizes["title"] * 1.5
            timeText = ax.annotate("", (0, 1), xycoords="axes fraction", xytext=(10, cursor), textcoords="offset points",
                                   ha="left", va="top", fontsize=fontSizes["time"], color="#111111",
                                   path_effects=halo, zorder=6)
            cursor -= fontSizes["time"] * 1.4
            for annotation in WATER_VIDEO_ANNOTATIONS:
                ax.annotate(annotation, (0, 1), xycoords="axes fraction", xytext=(10, cursor), textcoords="offset points",
                            ha="left", va="top", fontsize=fontSizes["annotation"], color="#333333",
                            path_effects=halo, zorder=6)
                cursor -= fontSizes["annotation"] * 1.5
            if(self.meshExists or self.obsExists):
                ax.legend(loc="lower right", framealpha=0.85, edgecolor="none", fontsize=fontSizes["legend"],
                          handletextpad=0.4)

            frameCount = len(self.mapWaterTimes)
            # Exact even pixel size for the encoder: Agg truncates figsize * dpi, so
            # a 9.12 in figure can render 911 px wide; frames are padded/cropped to this.
            frameWidth = int(round(figW * dpi / 2)) * 2
            frameHeight = int(round(figH * dpi / 2)) * 2

            def makeFrames():
                for index in range(frameCount):
                    field.set_data(rasterizer.values(self.mapWaters[index]))
                    timeText.set_text(datetime.fromtimestamp(self.mapWaterTimes[index], timezone.utc)
                                      .strftime(WATER_VIDEO_TIME_FORMAT))
                    # Runup lines change every frame; remember what they add so it can be removed after drawing
                    frameArtists = []
                    if(self.runupExists):
                        before = set(ax.get_children())
                        for runupIndex, runupLabel in enumerate(self.runupLabels):
                            self.plotExtendedLines(ax, runupIndex, index, runupLabel)
                        frameArtists = [artist for artist in ax.get_children() if artist not in before]
                    canvas.draw()
                    rendered = np.asarray(canvas.buffer_rgba())[:frameHeight, :frameWidth, :3]
                    frame = np.full((frameHeight, frameWidth, 3), 255, dtype=np.uint8)
                    frame[:rendered.shape[0], :rendered.shape[1]] = rendered
                    for artist in frameArtists:
                        artist.remove()
                    if(index % 50 == 0):
                        print("  water map frame", index, "of", frameCount, flush=True)
                    yield frame

            writeVideo(graph_directory + "water", makeFrames, frameWidth, frameHeight, WATER_VIDEO_FPS)

    def plotExtendedLines(self, ax, runupIndex, index, runupLabel):
        # Get coordinates for waterline (two points)
        waterline_lon1 = float(self.datapointsWaterlineLongitudes[runupIndex][index][0])
        waterline_lat1 = float(self.datapointsWaterlineLatitudes[runupIndex][index][0])
        waterline_lon2 = float(self.datapointsWaterlineLongitudes[runupIndex][index][1])
        waterline_lat2 = float(self.datapointsWaterlineLatitudes[runupIndex][index][1])
    
        # Get coordinates for runup line (two points)
        runup_lon1 = float(self.datapointsRunupLongitudes[runupIndex][index][0])
        runup_lat1 = float(self.datapointsRunupLatitudes[runupIndex][index][0])
        runup_lon2 = float(self.datapointsRunupLongitudes[runupIndex][index][1])
        runup_lat2 = float(self.datapointsRunupLatitudes[runupIndex][index][1])
    
        # Initialize geodesic calculator
        geod = Geodesic.WGS84
    
        # Calculate waterline properties
        water_g = geod.Inverse(waterline_lat1, waterline_lon1, waterline_lat2, waterline_lon2)
        water_length = water_g['s12']
        water_azi1 = water_g['azi1']  # Forward azimuth from point 1
        water_azi2 = water_g['azi2']  # Forward azimuth from point 2
    
        # Calculate runup line properties
        runup_g = geod.Inverse(runup_lat1, runup_lon1, runup_lat2, runup_lon2)
        runup_length = runup_g['s12']
        runup_azi1 = runup_g['azi1']
        runup_azi2 = runup_g['azi2']
    
        # Calculate extension distance (10x original length)
        water_ext_dist = water_length * 10
        runup_ext_dist = runup_length * 10
    
        # Extend waterline - backward from point 1
        water_back = geod.Direct(waterline_lat1, waterline_lon1, water_azi1 + 180, water_ext_dist)
        water_extend_back_lon = water_back['lon2']
        water_extend_back_lat = water_back['lat2']
    
        # Extend waterline - forward from point 2
        water_forward = geod.Direct(waterline_lat2, waterline_lon2, water_azi2, water_ext_dist)
        water_extend_forward_lon = water_forward['lon2']
        water_extend_forward_lat = water_forward['lat2']
    
        # Extend runup - backward from point 1
        runup_back = geod.Direct(runup_lat1, runup_lon1, runup_azi1 + 180, runup_ext_dist)
        runup_extend_back_lon = runup_back['lon2']
        runup_extend_back_lat = runup_back['lat2']
    
        # Extend runup - forward from point 2
        runup_forward = geod.Direct(runup_lat2, runup_lon2, runup_azi2, runup_ext_dist)
        runup_extend_forward_lon = runup_forward['lon2']
        runup_extend_forward_lat = runup_forward['lat2']
    
        # Create lists for plotting
        waterline_lons = [water_extend_back_lon, waterline_lon1, waterline_lon2, water_extend_forward_lon]
        waterline_lats = [water_extend_back_lat, waterline_lat1, waterline_lat2, water_extend_forward_lat]
        runup_lons = [runup_extend_back_lon, runup_lon1, runup_lon2, runup_extend_forward_lon]
        runup_lats = [runup_extend_back_lat, runup_lat1, runup_lat2, runup_extend_forward_lat]
    
        # Plot the extended lines
        ax.plot(waterline_lons, waterline_lats,
                label=runupLabel, zorder=3, alpha=0.7,
                marker=".", color="green")
        ax.plot(runup_lons, runup_lats,
                label=runupLabel, zorder=3, alpha=0.7,
                marker=".", color="red")
                
    def findMatchingIndices(self, string_array, match_string):
        return [i for i, s in enumerate(string_array) if match_string in s]

    # Usage example:
    # plot_extended_lines(self, ax, runupIndex, index, runupLabel)

    def __init__(self, dataToGraph={}, STATIONS_FILE="", backgroundMap="", backgroundAxis=[], titlePrefix="", stormBanner="", obsWaterLabel="NOAA Observed"):
        print("Initializing grapher", flush=True)
        self.obsExists = False
        self.gaugeExists = False
        self.tideExists = False
        self.buoyExists = False
        self.assetExists = False
        self.windExists = False
        self.wavesExists = False
        self.rainExists = False
        self.waterExists = False
        self.stillwaterExists = False
        self.tidewaterExists = False
        self.etaExists = False
        self.meshExists = False
        self.runupExists = False
        self.velocityExists = False

        self.windStartDate = None
        self.waveStartDate = None
        self.rainStartDate = None
        self.waterStartDate = None
        self.etaStartDate = None
        self.runupStartDate = None
        self.velocityStartDate = None
        
        self.windType = ""
        
        self.backgroundMap = backgroundMap
        self.backgroundAxis = backgroundAxis
        
        self.titlePrefix=titlePrefix
        self.stormBanner=stormBanner
        self.obsWaterLabel=obsWaterLabel
        installFigureBanner(stormBanner)
        
        if("OBS" in dataToGraph):
            self.obsExists = True
        if("GAUGE" in dataToGraph):
            self.gaugeExists = True
        if("TIDE" in dataToGraph):
            self.tideExists = True
        if("POST" in dataToGraph or "GFS" in dataToGraph or "FORT" in dataToGraph):
            self.windExists = True
        if("SWH" in dataToGraph or "MWD" in dataToGraph or "MWP" in dataToGraph or "PWP" in dataToGraph or "RAD" in dataToGraph):
            self.wavesExists = True
        if("BUOY" in dataToGraph):
            self.buoyExists = True
        if("RAIN" in dataToGraph):
            self.rainExists = True
        if("WATER" in dataToGraph):
            self.waterExists = True
        if("STILLWATER" in dataToGraph):
            self.stillwaterExists = True
        if("TIDEWATER" in dataToGraph):
            self.tidewaterExists = True
        if("ETA" in dataToGraph):
            self.etaExists = True
        if("MESH" in dataToGraph):
            self.meshExists = True
        if("ASSET" in dataToGraph):
            self.assetExists = True
        if("RUNUP" in dataToGraph):
            self.runupExists = True
        if("VELOCITY" in dataToGraph):
            self.velocityExists = True
        with open(STATIONS_FILE) as outfile:
            self.obsMetadata = json.load(outfile)
            
                
#         There are 3 possible perturbations. 
#          Graphing wave data on wave mesh, and also trying to graph GFS data
#           Graphing wave data on wave mesh, and also graphing POST data
#          Graphing wave data on wave mesh, and also graphing GFS/POST data and graphing OBS
#          3 sets of lat, long, labels, and times are needed, assuming that each datatype,
#           even if multiple files are contained, are internally consistent with respect to the timedelta of the data,
#         i.e. even if wave data is comprised of 5 files, the same datapointsTimes array can  be used to
#          graph the 5 timeseries, saving some space as well.

#          On second thought, the assumption that each data type will be internally consistent
#           with timedeltas does not hold for observational data, as some stations may have more data
#           than others. the obsDatapointsTimes will be structurally different from the forecsated
#           wind and waves because the observational will have timestamps for each station's wind data
#           while the forecasted data will have one master timestamp array for all the nodes being examined.

#          UPDATE: Added another perturbation by adding rain data
        
        obsLabelsInitialized = False
        self.obsLongitudes = []
        self.obsLatitudes = []
        self.obsLabels = []
        
        self.obsDatapointsTimes = []
        self.obsDatapointsDirections = []
        self.obsDatapointsSpeeds = []
        self.obsDatapointsHeights = []
        
        gaugeLabelsInitialized = False
        self.gaugeLongitudes = []
        self.gaugeLatitudes = []
        self.gaugeLabels = []
        
        self.gaugeDatapointsTimes = []
        self.gaugeDatapointsRains = []
        
        tideLabelsInitialized = False
        self.tideLongitudes = []
        self.tideLatitudes = []
        self.tideLabels = []
        self.tideIds = []
        
        self.tideDatapointsTimes = []
        self.tideDatapointsWaters = []
    
        self.tideDatapointsPredictionTimes = []
        self.tideDatapointsPredictionWaters = []
        
        self.windLongitudes = []
        self.windLatitudes = []
        self.windLabels = []
        self.windTimes = []
        
        self.maxWind = 20
        self.mapWindPoints = []
        self.mapWindPointsLatitudes = []
        self.mapWindPointsLongitudes = []
        self.mapWindTimes = []
        self.mapWindTriangles = []
        self.mapWindMaskedTriangles = []
        self.mapSpeeds = []
        self.mapDirections = []
        
        self.datapointsDirections = []
        self.datapointsSpeeds = []
        
        self.waterLongitudes = []
        self.waterLatitudes = []
        self.waterLabels = []
        self.waterTimes = []
        
        self.maxWater = 5
        self.mapWaterPoints = []
        self.mapWaterTimes = []
        self.mapWaterPointsLatitudes = []
        self.mapWaterPointsLongitudes = []
        self.mapWaterTriangles = []
        self.mapWaterMaskedTriangles = []
        self.mapWaters = []
        
        self.datapointsWaters = []
        
        self.stillwaterTimes = []
        self.datapointsStillwaters = []

        self.tidewaterTimes = []
        self.datapointsTidewaters = []

        self.velocityLongitudes = []
        self.velocityLatitudes = []
        self.velocityLabels = []
        self.velocityStationNames = []
        self.velocityTimes = []

        self.maxVelocity = 2
        self.mapVelocityPoints = []
        self.mapVelocityTimes = []
        self.mapVelocityPointsLatitudes = []
        self.mapVelocityPointsLongitudes = []
        self.mapVelocityTriangles = []
        self.mapVelocityMaskedTriangles = []
        self.mapVelocitySpeeds = []
        self.mapVelocityDirections = []

        self.datapointsVelocitySpeeds = []
        self.datapointsVelocityDirections = []

        self.etaLongitudes = []
        self.etaLatitudes = []
        self.etaLabels = []
        self.etaTimes = []
        
        self.maxEta = 5
        self.mapEtaPoints = []
        self.mapEtaTimes = []
        self.mapEtaPointsLatitudes = []
        self.mapEtaPointsLongitudes = []
        self.mapEta = []
        
        self.datapointsEta = []
        
        self.rainLongitudes = []
        self.rainLatitudes = []
        self.rainLabels = []
        self.rainTimes = []
        
        self.maxRain = 15
        self.mapRainPoints = []
        self.mapRainTimes = []
        self.mapRainPointsLatitudes = []
        self.mapRainPointsLongitudes = []
        self.mapRains = []
        
        self.datapointsRains = []
        
        self.waveLongitudes = []
        self.waveLatitudes = []
        self.waveLabels = []
        self.waveTimes = []
        
        self.datapointsSWH = []
        self.datapointsMWD = []
        self.datapointsMWP = []
        self.datapointsPWP = []
        self.datapointsRADMag = []
        self.datapointsRADDir = []
        
        buoyLabelsInitialized = False
        self.buoyLongitudes = []
        self.buoyLatitudes = []
        self.buoyLabels = []
        
        self.buoyDatapointsTimes = []
        self.buoyDatapointsSWH = []
        self.buoyDatapointsMWD = []
        self.buoyDatapointsMWP = []
        self.buoyDatapointsPWP = []
        
        self.maxSWH = 3
        self.mapWavePoints = []
        self.mapWavePointsLatitude = []
        self.mapWavePointsLongitude = []
        self.mapWaveTriangles = []
        self.mapWaveMaskedTriangles = []
        self.mapWaveTimes = []
        self.mapSWH = []
        
        
       
        self.elevationLongitudes = []
        self.elevationLatitudes = []
        self.elevationLabels = []
        
        self.datapointsElevation = []
        
        assetLabelsInitialized = False
        self.assetLongitudes = []
        self.assetLatitudes = []
        self.assetLabels = []
        
        self.assetDatapointsElevation = []
        
        self.maxElevation = 10
        self.mapElevationPoints = []
        self.mapElevationPointsLatitudes = []
        self.mapElevationPointsLongitudes = []
        self.mapElevationTriangles = []
        self.mapElevationMaskedTriangles = []
        self.mapElevation = []
        
        self.maxRunup = 1
        self.runupLongitudes = []
        self.runupLatitudes = []
        self.runupLabels = []
        self.runupSurfDistance = []
        self.runupOffshoreDistance = []
        self.runupAverageSlope = []
        
        self.runupTimes = []
        self.datapointsRunup = []
        self.datapointsRunupHolmanHigh = []
        self.datapointsRunupHolmanMid = []
        self.datapointsRunupHolmanLow = []
        self.datapointsSetupHolmanHigh = []
        self.datapointsSetupHolmanMid = []
        self.datapointsSetupHolmanLow = []
        self.datapointsSwashHolmanHigh = []
        self.datapointsSwashHolmanMid = []
        self.datapointsSwashHolmanLow = []
        self.datapointsSwashHolmanIncident = []
        self.datapointsSwashHolmanInfragravity = []
        self.datapointsSetupStockdon = []
        self.datapointsSetupStockdonLow = []
        self.datapointsSwashStockdonIncident = []
        self.datapointsSwashStockdonInfragravity = []
        self.datapointsSwashStockdonLow = []
        self.datapointsRunupStockdon = []
        self.datapointsRunupStockdonNoSetup = []
        self.datapointsRunupStockdonLow = []
        self.datapointsSetupAdcirc = []
        self.datapointsRunupAdcirc = []
        
        self.datapointsWavelength = []
        self.datapointsIribarren = []
        self.datapointsSteepness = []
        self.datapointsWaterlineLongitudes = []
        self.datapointsWaterlineLatitudes = []
        self.datapointsRunupLongitudes = []
        self.datapointsRunupLatitudes = []
        self.runupAverageSlopes = []
        
        self.datapointsDuneHeights = []
    

#        So loading obs, wind, and waves should be able to cover and set all available data

        windType = ""
        if("OBS" in dataToGraph):
            with open(dataToGraph["OBS"]) as outfile:
                obsDataset = json.load(outfile)
        if("POST" in dataToGraph):  
            self.windType = "POST"
            with open(dataToGraph["POST"]) as outfile:
                windDataset = json.load(outfile)
        if("GFS" in dataToGraph):
            self.windType = "GFS"
            with open(dataToGraph["GFS"]) as outfile:
                windDataset = json.load(outfile)
        if("FORT" in dataToGraph):
            self.windType = "FORT"
            with open(dataToGraph["FORT"]) as outfile:
                windDataset = json.load(outfile)
        if("GAUGE" in dataToGraph):
            with open(dataToGraph["GAUGE"]) as outfile:
                gaugeDataset = json.load(outfile)
        if("TIDE" in dataToGraph):
            with open(dataToGraph["TIDE"]) as outfile:
                tideDataset = json.load(outfile)
        if("BUOY" in dataToGraph):
            with open(dataToGraph["BUOY"]) as outfile:
                buoyDataset = json.load(outfile)
        if("ASSET" in dataToGraph):
            with open(dataToGraph["ASSET"]) as outfile:
                assetDataset = json.load(outfile)
        if("ETA" in dataToGraph):
            with open(dataToGraph["ETA"]) as outfile:
                etaDataset = json.load(outfile)

                  
        if(self.windExists):
            windTimestampsInitialized = False
            for stationKey in windDataset.keys():
                if(stationKey == "map_data"):
                    self.mapWindPoints = windDataset["map_data"]["map_points"]
                    self.mapWindPointsLatitudes = windDataset["map_data"]["map_pointsLatitudes"]
                    self.mapWindPointsLongitudes = windDataset["map_data"]["map_pointsLongitude"]
                    self.mapWindTimes = windDataset["map_data"]["map_times"]
                    if(self.windType == "FORT"):
                        self.mapWindTriangles = windDataset["map_data"]["map_triangles"]
                        self.mapWindMaskedTriangles = windDataset["map_data"]["map_maskedTriangles"]
                        mapWindsX = windDataset["map_data"]["map_windsX"]
                        mapWindsY = windDataset["map_data"]["map_windsY"]
                        for index in range(len(self.mapWindTimes)):
                            lineSpeed = []
                            lineDirection = []
                            for nodeIndex in range(len(mapWindsX[index])):
                                pointSpeed = self.vectorSpeed(mapWindsX[index][nodeIndex], mapWindsY[index][nodeIndex])
                                if(pointSpeed > self.maxWind):
                                    self.maxWind = pointSpeed
                                lineSpeed.append(pointSpeed)
                                lineDirection.append(self.vectorDirection(mapWindsX[index][nodeIndex], mapWindsY[index][nodeIndex]))
                            self.mapSpeeds.append(pointSpeeds)
                            self.mapDirections.append(pointDirections)
                    elif(self.windType == "GFS"):
                        mapWindsX = windDataset["map_data"]["map_windsX"]
                        mapWindsY = windDataset["map_data"]["map_windsY"]
                        for index in range(len(self.mapWindTimes)):
                            mapSpeed = []
                            mapDirection = []
                            for latitudeIndex in range(len(mapWindsX[index])):
                                lineSpeed = []
                                lineDirection = []
                                for longitudeIndex in range(len(mapWindsX[index][latitudeIndex])):
                                    pointSpeed = self.vectorSpeed(mapWindsX[index][latitudeIndex][longitudeIndex], mapWindsY[index][latitudeIndex][longitudeIndex])
                                    pointDirection = self.vectorDirection(mapWindsX[index][latitudeIndex][longitudeIndex], mapWindsY[index][latitudeIndex][longitudeIndex])
                                    if(pointSpeed > self.maxWind):
                                        self.maxWind = pointSpeed
                                    lineSpeed.append(pointSpeed)
                                    lineDirection.append(pointDirection)
                                mapSpeed.append(lineSpeed)
                                mapDirection.append(lineDirection)
                            self.mapSpeeds.append(mapSpeed)
                            self.mapDirections.append(mapDirection)
                    elif(self.windType == "POST"):
                        self.mapSpeeds = windDataset["map_data"]["map_speeds"]
                        self.mapDirections = windDataset["map_data"]["map_directions"]
                        for index in range(len(self.mapWindTimes)):
                            for latitudeIndex in range(len(self.mapSpeeds[index])):
                                for longitudeIndex in range(len(self.mapSpeeds[index][latitudeIndex])):
                                    pointSpeed = self.mapSpeeds[index][latitudeIndex][longitudeIndex]
                                    if(pointSpeed > self.maxWind):
                                        self.maxWind = pointSpeed
                else:
                    nodeIndex = windDataset[stationKey]["nodeIndex"]
                    if(not self.obsExists or (stationKey in obsDataset.keys())):
                        self.windLabels.append(nodeIndex)
                        self.windLatitudes.append(windDataset[stationKey]["latitude"])
                        self.windLongitudes.append(windDataset[stationKey]["longitude"])
                    
                        if(not obsLabelsInitialized):
                            self.obsLabels.append(self.obsMetadata["NOS"][stationKey]["name"])
                            self.obsLatitudes.append(float(self.obsMetadata["NOS"][stationKey]["latitude"]))
                            self.obsLongitudes.append(float(self.obsMetadata["NOS"][stationKey]["longitude"]))
                    
                        datapointDirections = []
                        datapointSpeeds = []
                        for index in range(len(windDataset[stationKey]["times"])):
                            if(self.windStartDate == None):
                                self.windStartDate = datetime.fromtimestamp(int(windDataset[stationKey]["times"][index]), timezone.utc)
                            if(not windTimestampsInitialized):
                                self.windTimes.append(self.unixTimeToDeltaHours(windDataset[stationKey]["times"][index], self.windStartDate))
                            if(self.windType == "GFS" or self.windType == "FORT"):
                                windX = windDataset[stationKey]["windsX"][index]
                                windY = windDataset[stationKey]["windsY"][index]
                                windSpeed = self.vectorSpeed(windX, windY)
                                windDirection = self.vectorDirection(windX, windY)
                            elif(self.windType == "POST"):
                                windSpeed = windDataset[stationKey]["speeds"][index]
                                windDirection = windDataset[stationKey]["directions"][index]
                            datapointDirections.append(windDirection)
                            datapointSpeeds.append(windSpeed)
                        windTimestampsInitialized = True
                        self.datapointsDirections.append(datapointDirections)
                        self.datapointsSpeeds.append(datapointSpeeds)
                        if(self.obsExists):
                            obsTimes = []
                            obsSpeeds = []
                            obsDirections = []
    #                         Height is not station altitude, it is sea surface height
                            obsHeights = []
                            for index in range(len(obsDataset[stationKey]["times"])):
                                obsTimes.append(self.unixTimeToDeltaHours(obsDataset[stationKey]["times"][index], self.windStartDate))
                                obsSpeed = obsDataset[stationKey]["speeds"][index]
                                obsDirection = obsDataset[stationKey]["directions"][index]
                                obsHeight = obsDataset[stationKey]["heights"][index]
                                obsSpeeds.append(obsSpeed)
                                obsDirections.append(obsDirection)
                                obsHeights.append(obsHeight)
                            self.obsDatapointsTimes.append(obsTimes)
                            self.obsDatapointsSpeeds.append(obsSpeeds)
                            self.obsDatapointsDirections.append(obsDataset[stationKey]["directions"])
                            self.obsDatapointsHeights.append(obsHeights)
            obsLabelsInitialized = True

        if(self.velocityExists):
            with open(dataToGraph["VELOCITY"]) as outfile:
                velocityDataset = json.load(outfile)

            velocityTimestampsInitialized = False
            for stationKey in velocityDataset.keys():
                if(stationKey == "map_data"):
                    self.mapVelocityPoints = velocityDataset["map_data"]["map_points"]
                    self.mapVelocityPointsLatitudes = velocityDataset["map_data"]["map_pointsLatitudes"]
                    self.mapVelocityPointsLongitudes = velocityDataset["map_data"]["map_pointsLongitude"]
                    self.mapVelocityTimes = velocityDataset["map_data"]["map_times"]
                    self.mapVelocityTriangles = velocityDataset["map_data"]["map_triangles"]
                    self.mapVelocityMaskedTriangles = velocityDataset["map_data"]["map_maskedTriangles"]
                    mapVelocitiesX = velocityDataset["map_data"]["map_velocitiesX"]
                    mapVelocitiesY = velocityDataset["map_data"]["map_velocitiesY"]
                    for index in range(len(self.mapVelocityTimes)):
                        lineSpeed = []
                        lineDirection = []
                        for nodeIndex in range(len(mapVelocitiesX[index])):
                            velocityX = mapVelocitiesX[index][nodeIndex]
                            velocityY = mapVelocitiesY[index][nodeIndex]
                            # ADCIRC writes -99999.0 for dry/wetting-drying nodes.
                            # Squaring that into a speed would produce a bogus
                            # ~141000 m/s magnitude, so keep it as the sentinel
                            # instead -- the map-rendering code already masks
                            # out triangles whose speed equals -99999.0.
                            if(velocityX == -99999.0 or velocityY == -99999.0):
                                pointSpeed = -99999.0
                                pointDirection = -99999.0
                            else:
                                pointSpeed = self.vectorSpeed(velocityX, velocityY)
                                pointDirection = self.vectorDirection(velocityX, velocityY)
                                if(pointSpeed > self.maxVelocity):
                                    self.maxVelocity = pointSpeed
                            lineSpeed.append(pointSpeed)
                            lineDirection.append(pointDirection)
                        self.mapVelocitySpeeds.append(lineSpeed)
                        self.mapVelocityDirections.append(lineDirection)
                else:
                    nodeIndex = velocityDataset[stationKey]["nodeIndex"]
                    self.velocityLabels.append(nodeIndex)
                    self.velocityLatitudes.append(velocityDataset[stationKey]["latitude"])
                    self.velocityLongitudes.append(velocityDataset[stationKey]["longitude"])
                    self.velocityStationNames.append(self.obsMetadata["NOS"][stationKey]["name"])

                    datapointSpeeds = []
                    datapointDirections = []
                    for index in range(len(velocityDataset[stationKey]["times"])):
                        if(self.velocityStartDate == None):
                            self.velocityStartDate = datetime.fromtimestamp(int(velocityDataset[stationKey]["times"][index]), timezone.utc)
                        if(not velocityTimestampsInitialized):
                            self.velocityTimes.append(self.unixTimeToDeltaHours(velocityDataset[stationKey]["times"][index], self.velocityStartDate))
                        velocityX = velocityDataset[stationKey]["velocitiesX"][index]
                        velocityY = velocityDataset[stationKey]["velocitiesY"][index]
                        datapointSpeeds.append(self.vectorSpeed(velocityX, velocityY))
                        datapointDirections.append(self.vectorDirection(velocityX, velocityY))
                    velocityTimestampsInitialized = True
                    self.datapointsVelocitySpeeds.append(datapointSpeeds)
                    self.datapointsVelocityDirections.append(datapointDirections)

        if(self.rainExists):
            with open(dataToGraph["RAIN"]) as outfile:
                rainDataset = json.load(outfile)
                
            rainTimestampsInitialized = False
            for stationKey in rainDataset.keys():
                if(stationKey == "map_data"):
                    self.mapRainTimes = rainDataset["map_data"]["map_times"]
                    self.mapRainPoints = rainDataset["map_data"]["map_points"]
                    self.mapRainPointsLatitudes = rainDataset["map_data"]["map_pointsLatitudes"]
                    self.mapRainPointsLongitudes = rainDataset["map_data"]["map_pointsLongitude"]
                    self.mapRains = rainDataset["map_data"]["map_rain"]
                    for index in range(len(self.mapRainTimes)):
                        for latitudeIndex in range(len(self.mapRains[index])):
                            for longitudeIndex in range(len(self.mapRains[index][latitudeIndex])):
                                pointRain = self.mapRains[index][latitudeIndex][longitudeIndex]
                                if(pointRain > self.maxRain):
                                    self.maxRain = pointRain
                else:
                    nodeIndex = rainDataset[stationKey]["nodeIndex"]
                    if(not self.gaugeExists or (stationKey in gaugeDataset.keys())):
                        self.rainLabels.append(nodeIndex)
                        self.rainLatitudes.append(rainDataset[stationKey]["latitude"])
                        self.rainLongitudes.append(rainDataset[stationKey]["longitude"])
                    
                        if(not gaugeLabelsInitialized):
                            self.gaugeLabels.append(self.obsMetadata["USGS"][stationKey]["name"])
                            self.gaugeLatitudes.append(float(self.obsMetadata["USGS"][stationKey]["latitude"]))
                            self.gaugeLongitudes.append(float(self.obsMetadata["USGS"][stationKey]["longitude"]))
    
                        datapointRains = []
                        for index in range(len(rainDataset[stationKey]["times"])):
                            if(self.rainStartDate == None):
                                self.rainStartDate = datetime.fromtimestamp(int(rainDataset[stationKey]["times"][index]), timezone.utc)
                            if(not rainTimestampsInitialized):
                                self.rainTimes.append(self.unixTimeToDeltaHours(rainDataset[stationKey]["times"][index], self.rainStartDate))
                            datapointRains.append(rainDataset[stationKey]["rain"][index])
                        rainTimestampsInitialized = True
                        self.datapointsRains.append(datapointRains)
                        
                        if(self.gaugeExists):
                            gaugeTimes = []
                            gaugeRains = []
        #                         Height is not station altitude, it is sea surface height
                            for index in range(len(gaugeDataset[stationKey]["times"])):
                                gaugeTimes.append(self.unixTimeToDeltaHours(gaugeDataset[stationKey]["times"][index], self.rainStartDate))
                                gaugeRain = gaugeDataset[stationKey]["rain"][index]
                                gaugeRains.append(gaugeRain)
                            self.gaugeDatapointsTimes.append(gaugeTimes)
                            self.gaugeDatapointsRains.append(gaugeRains)
            gaugeLabelsInitialized = True
            
            
  
        if(self.meshExists):
            with open(dataToGraph["MESH"]) as outfile:
                meshDataset = json.load(outfile)
                
            for stationKey in meshDataset.keys():
                if(stationKey == "map_data"):
                    self.mapElevationTriangles = meshDataset["map_data"]["map_triangles"]
                    self.mapElevationMaskedTriangles = meshDataset["map_data"]["map_maskedTriangles"]
                    self.mapElevationPoints = meshDataset["map_data"]["map_points"]
                    self.mapElevationPointsLatitudes = meshDataset["map_data"]["map_pointsLatitudes"]
                    self.mapElevationPointsLongitudes = meshDataset["map_data"]["map_pointsLongitude"]
                    self.mapElevation = meshDataset["map_data"]["map_elevation"]
                    for nodeIndex in range(len(self.mapElevation)):
                        pointElevation = self.mapElevation[nodeIndex]
                        if(pointElevation > self.maxElevation):
                            self.maxElevation = pointElevation
                else:
                    nodeIndex = meshDataset[stationKey]["nodeIndex"]
                    if(not self.meshExists or (stationKey in meshDataset.keys())):
                        self.elevationLabels.append(nodeIndex)
                        self.elevationLatitudes.append(meshDataset[stationKey]["latitude"])
                        self.elevationLongitudes.append(meshDataset[stationKey]["longitude"])
                
                        if(not assetLabelsInitialized):
                            self.assetLabels.append(self.obsMetadata["ASSET"][stationKey]["name"])
                            self.assetLatitudes.append(float(self.obsMetadata["ASSET"][stationKey]["latitude"]))
                            self.assetLongitudes.append(float(self.obsMetadata["ASSET"][stationKey]["longitude"]))

                        elevation = meshDataset[stationKey]["elevation"]
                        self.datapointsElevation.append(elevation)
                    
                        if(self.assetExists):
                            assetElevation = assetDataset[stationKey]["elevation"]
                            self.assetDatapointsElevation.append(assetElevation)
            assetLabelsInitialized = True
            
        if(self.waterExists):
            with open(dataToGraph["WATER"]) as outfile:
                waterDataset = loadMapArrays(json.load(outfile))
                
            if(self.stillwaterExists):
                with open(dataToGraph["STILLWATER"]) as outfile:
                    stillwaterDataset = json.load(outfile)
                    
            if(self.tidewaterExists):
                with open(dataToGraph["TIDEWATER"]) as outfile:
                    tidewaterDataset = json.load(outfile)
                
            waterTimestampsInitialized = False
            stillwaterTimestampsInitialized = False
            tidewaterTimestampsInitialized = False
            for stationKey in waterDataset.keys():
                if(stationKey == "map_data"):
                    self.mapWaterTriangles = waterDataset["map_data"]["map_triangles"]
                    self.mapWaterMaskedTriangles = waterDataset["map_data"]["map_maskedTriangles"]
                    self.mapWaterTimes = waterDataset["map_data"]["map_times"]
                    self.mapWaterPoints = waterDataset["map_data"]["map_points"]
                    self.mapWaterPointsLatitudes = waterDataset["map_data"]["map_pointsLatitudes"]
                    self.mapWaterPointsLongitudes = waterDataset["map_data"]["map_pointsLongitude"]
                    self.mapWaters = np.asarray(waterDataset["map_data"]["map_water"])
                    if(self.mapWaters.size):
                        self.maxWater = max(self.maxWater, float(self.mapWaters.max()))
                else:
                    nodeIndex = waterDataset[stationKey]["nodeIndex"]
                    if(not self.tideExists or (stationKey in tideDataset.keys())):
                        self.waterLabels.append(nodeIndex)
                        self.waterLatitudes.append(waterDataset[stationKey]["latitude"])
                        self.waterLongitudes.append(waterDataset[stationKey]["longitude"])
                    
                        if(not tideLabelsInitialized):
                            self.tideLabels.append(self.obsMetadata["NOS"][stationKey]["name"])
                            self.tideIds.append(self.obsMetadata["NOS"][stationKey].get("id", ""))
                            self.tideLatitudes.append(float(self.obsMetadata["NOS"][stationKey]["latitude"]))
                            self.tideLongitudes.append(float(self.obsMetadata["NOS"][stationKey]["longitude"]))

                        datapointWaters = []
                        for index in range(len(waterDataset[stationKey]["times"])):
                            if(self.waterStartDate == None):
                                self.waterStartDate = datetime.fromtimestamp(int(waterDataset[stationKey]["times"][index]), timezone.utc)
                            if(not waterTimestampsInitialized):
                                self.waterTimes.append(self.unixTimeToDeltaHours(waterDataset[stationKey]["times"][index], self.waterStartDate))
                            datapointWaters.append(waterDataset[stationKey]["water"][index])
                        waterTimestampsInitialized = True
                        if(self.CONVERT_TO_WATER_DEPTH and stationKey in meshDataset.keys()):
                            stationElevation = meshDataset[stationKey]["elevation"]
                            datapointWaters = np.array(datapointWaters) + (stationElevation * -1)
                        self.datapointsWaters.append(datapointWaters)
                        if(self.stillwaterExists):
                            datapointStillwaters = []
                            for index in range(len(stillwaterDataset[stationKey]["times"])):
                                if(not stillwaterTimestampsInitialized):
                                    self.stillwaterTimes.append(self.unixTimeToDeltaHours(stillwaterDataset[stationKey]["times"][index], self.waterStartDate))
                                datapointStillwaters.append(stillwaterDataset[stationKey]["water"][index])
                            stillwaterTimestampsInitialized = True
                            self.datapointsStillwaters.append(datapointStillwaters)
                        if(self.tidewaterExists):
                            datapointTidewaters = []
                            for index in range(len(tidewaterDataset[stationKey]["times"])):
                                if(not tidewaterTimestampsInitialized):
                                    self.tidewaterTimes.append(self.unixTimeToDeltaHours(tidewaterDataset[stationKey]["times"][index], self.waterStartDate))
                                datapointTidewaters.append(tidewaterDataset[stationKey]["water"][index])
                            tidewaterTimestampsInitialized = True
                            if(self.CONVERT_TO_WATER_DEPTH and stationKey in meshDataset.keys()):
                                stationElevation = meshDataset[stationKey]["elevation"]
                                datapointTidewaters = np.array(datapointTidewaters) + (stationElevation * -1)
                            self.datapointsTidewaters.append(datapointTidewaters)
                        if(self.tideExists):
                            tideTimes = []
                            tideWaters = []
                #                         Height is not station altitude, it is sea surface height
                            for index in range(len(tideDataset[stationKey]["times"])):
                                tideTimes.append(self.unixTimeToDeltaHours(tideDataset[stationKey]["times"][index], self.waterStartDate))
                                tideWater = tideDataset[stationKey]["water"][index]
                                tideWaters.append(tideWater)
                            self.tideDatapointsTimes.append(tideTimes)
                            self.tideDatapointsWaters.append(tideWaters)
                            tidePredictionTimes = []
                            tidePredictionWaters = []
                #                         Height is not station altitude, it is sea surface height
                            for index in range(len(tideDataset[stationKey]["prediction_times"])):
                                tidePredictionTimes.append(self.unixTimeToDeltaHours(tideDataset[stationKey]["prediction_times"][index], self.waterStartDate))
                                tidePredictionWater = tideDataset[stationKey]["prediction_water"][index]
                                tidePredictionWaters.append(tidePredictionWater)
                            self.tideDatapointsPredictionTimes.append(tidePredictionTimes)
                            self.tideDatapointsPredictionWaters.append(tidePredictionWaters)
            tideLabelsInitialized = True
                      
                      
        
        if(self.etaExists):
            with open(dataToGraph["ETA"]) as outfile:
                etaDataset = json.load(outfile)
                
            etaTimestampsInitialized = False
            for stationKey in etaDataset.keys():
                if(stationKey == "map_data"):
                    self.mapEtaTimes = etaDataset["map_data"]["map_times"]
                    self.mapEtaPoints = etaDataset["map_data"]["map_points"]
                    self.mapEtaPointsLatitudes = etaDataset["map_data"]["map_pointsLatitudes"]
                    self.mapEtaPointsLongitudes = etaDataset["map_data"]["map_pointsLongitude"]
                    self.mapEta = etaDataset["map_data"]["map_eta"]
                    for index in range(len(self.mapEtaTimes)):
                        for latitudeIndex in range(len(self.mapEta[index])):
                            for longitudeIndex in range(len(self.mapEta[index][latitudeIndex])):
                                pointEta = self.mapEta[index][latitudeIndex][longitudeIndex]
                                if(pointEta > self.maxEta):
                                    self.maxEta = pointEta
                else:
                    nodeIndex = etaDataset[stationKey]["nodeIndex"]
                    if(not self.etaExists or (stationKey in etaDataset.keys())):
                        self.etaLabels.append(nodeIndex)
                        self.etaLatitudes.append(etaDataset[stationKey]["latitude"])
                        self.etaLongitudes.append(etaDataset[stationKey]["longitude"])
                    
                        if(not tideLabelsInitialized):
                            self.tideLabels.append(self.obsMetadata["NOS"][stationKey]["name"])
                            self.tideIds.append(self.obsMetadata["NOS"][stationKey].get("id", ""))
                            self.tideLatitudes.append(float(self.obsMetadata["NOS"][stationKey]["latitude"]))
                            self.tideLongitudes.append(float(self.obsMetadata["NOS"][stationKey]["longitude"]))
    
                        datapointEta = []
                        for index in range(len(etaDataset[stationKey]["times"])):
                            if(self.etaStartDate == None):
                                self.etaStartDate = datetime.fromtimestamp(int(etaDataset[stationKey]["times"][index]), timezone.utc)
                            if(not etaTimestampsInitialized):
                                self.etaTimes.append(self.unixTimeToDeltaHours(etaDataset[stationKey]["times"][index], self.etaStartDate))
                            datapointEta.append(etaDataset[stationKey]["eta"][index])
                        etaTimestampsInitialized = True
                        self.datapointsEta.append(datapointEta)
                        if(self.tideExists):
                            tideTimes = []
                            tideWaters = []
                #                         Height is not station altitude, it is sea surface height
                            for index in range(len(tideDataset[stationKey]["times"])):
                                tideTimes.append(self.unixTimeToDeltaHours(tideDataset[stationKey]["times"][index], self.waterStartDate))
                                tideWater = tideDataset[stationKey]["water"][index]
                                tideWaters.append(tideWater)
                            self.tideDatapointsTimes.append(tideTimes)
                            self.tideDatapointsWaters.append(tideWaters)
                            tidePredictionTimes = []
                            tidePredictionWaters = []
                #                         Height is not station altitude, it is sea surface height
                            for index in range(len(tideDataset[stationKey]["prediction_times"])):
                                tidePredictionTimes.append(self.unixTimeToDeltaHours(tideDataset[stationKey]["prediction_times"][index], self.waterStartDate))
                                tidePredictionWater = tideDataset[stationKey]["prediction_water"][index]
                                tidePredictionWaters.append(tidePredictionWater)
                            self.tideDatapointsPredictionTimes.append(tidePredictionTimes)
                            self.tideDatapointsPredictionWaters.append(tidePredictionWaters)
            tideLabelsInitialized = True                      
  
        if(self.wavesExists):
            swhExists = False
            mwdExists = False
            mwpExists = False
            pwpExists = False
            radExists = False
            iteratorDataset = None
            if("SWH" in dataToGraph):
                swhExists = True
                with open(dataToGraph["SWH"]) as outfile:
                    swhDataset = json.load(outfile)
                    if(iteratorDataset == None):
                        iteratorDataset = swhDataset
            if("MWD" in dataToGraph):
                mwdExists = True
                with open(dataToGraph["MWD"]) as outfile:
                    mwdDataset = json.load(outfile)
                    if(iteratorDataset == None):
                        iteratorDataset = mwdDataset
            if("MWP" in dataToGraph):
                mwpExists = True
                with open(dataToGraph["MWP"]) as outfile:
                    mwpDataset = json.load(outfile)
                    if(iteratorDataset == None):
                        iteratorDataset = mwpDataset
            if("PWP" in dataToGraph):
                pwpExists = True
                with open(dataToGraph["PWP"]) as outfile:
                    pwpDataset = json.load(outfile)
                    if(iteratorDataset == None):
                        iteratorDataset = pwpDataset
            if("RAD" in dataToGraph):
                radExists = True
                with open(dataToGraph["RAD"]) as outfile:
                    radDataset = json.load(outfile)
                    if(iteratorDataset == None):
                        iteratorDataset = radDataset
            
            waveTimestampsInitialized = False
            for stationKey in iteratorDataset.keys():
                if(stationKey == "map_data"):
                    self.mapWaveTriangles = swhDataset["map_data"]["map_triangles"]
                    self.mapWaveMaskedTriangles = swhDataset["map_data"]["map_maskedTriangles"]
                    self.mapWaveTimes = swhDataset["map_data"]["map_times"]
                    self.mapWavePoints = swhDataset["map_data"]["map_points"]
                    self.mapWavePointsLatitudes = swhDataset["map_data"]["map_pointsLatitudes"]
                    self.mapWavePointsLongitudes = swhDataset["map_data"]["map_pointsLongitude"]
                    self.mapSWH = swhDataset["map_data"]["map_swh"]
                    for index in range(len(self.mapWaveTimes)):
                        for nodeIndex in range(len(self.mapSWH[index])):
                            pointSWH = self.mapSWH[index][nodeIndex]
                            if(pointSWH > self.maxSWH):
                                self.maxSWH = pointSWH
                else:
                    nodeIndex = iteratorDataset[stationKey]["nodeIndex"]
                    if(not self.buoyExists or (stationKey in buoyDataset.keys())):
                        self.waveLabels.append(nodeIndex)
                        self.waveLatitudes.append(iteratorDataset[stationKey]["latitude"])
                        self.waveLongitudes.append(iteratorDataset[stationKey]["longitude"])
                        if(not buoyLabelsInitialized):
                            self.buoyLabels.append(self.obsMetadata["NDBC"][stationKey]["name"])
                            self.buoyLatitudes.append(float(self.obsMetadata["NDBC"][stationKey]["latitude"]))
                            self.buoyLongitudes.append(float(self.obsMetadata["NDBC"][stationKey]["longitude"]))

                        datapointSWH = []
                        datapointMWD = []
                        datapointMWP = []
                        datapointPWP = []
                        datapointRADMag = []
                        datapointRADDir = []
                        for index in range(len(iteratorDataset[stationKey]["times"])):
                            if(self.waveStartDate == None):
                                self.waveStartDate = datetime.fromtimestamp(int(iteratorDataset[stationKey]["times"][index]), timezone.utc)
                            if(not waveTimestampsInitialized):
                                self.waveTimes.append(self.unixTimeToDeltaHours(iteratorDataset[stationKey]["times"][index], self.waveStartDate))
                            if(swhExists):
                                datapointSWH.append(swhDataset[stationKey]["swh"][index])
                            if(mwdExists):
                                datapointMWD.append(mwdDataset[stationKey]["mwd"][index])
                            if(mwpExists):
                                datapointMWP.append(mwpDataset[stationKey]["mwp"][index])
                            if(pwpExists):
                                datapointPWP.append(pwpDataset[stationKey]["pwp"][index])
                            if(radExists):
                                radX = radDataset[stationKey]["radstressX"][index]
                                radY = radDataset[stationKey]["radstressY"][index]
                                radMag = self.vectorSpeed(radX, radY)
                                radDir = self.vectorDirection(radX, radY)
                                datapointRADMag.append(radMag)
                                datapointRADDir.append(radDir)
                        waveTimestampsInitialized = True
                        self.datapointsSWH.append(datapointSWH)
                        self.datapointsMWD.append(datapointMWD)
                        self.datapointsMWP.append(datapointMWP)
                        self.datapointsPWP.append(datapointPWP)
                        self.datapointsRADMag.append(datapointRADMag)
                        self.datapointsRADDir.append(datapointRADDir) 
                        if(self.buoyExists):
                            buoyTimes = []
                            buoySWH = []
                            buoyMWD = []
                            buoyMWP = []
                            buoyPWP = []
                #                         Height is not station altitude, it is sea surface height
                            for index in range(len(buoyDataset[stationKey]["times"])):
                                buoyTimes.append(self.unixTimeToDeltaHours(buoyDataset[stationKey]["times"][index], self.waveStartDate))
                                buoySWH.append(buoyDataset[stationKey]["swh"][index])
                                buoyMWD.append(buoyDataset[stationKey]["mwd"][index])
                                buoyMWP.append(buoyDataset[stationKey]["mwp"][index])
                                buoyPWP.append(buoyDataset[stationKey]["pwp"][index])
                            self.buoyDatapointsTimes.append(buoyTimes)
                            self.buoyDatapointsSWH.append(buoySWH)
                            self.buoyDatapointsMWD.append(buoyMWD)
                            self.buoyDatapointsMWP.append(buoyMWP)
                            self.buoyDatapointsPWP.append(buoyPWP)
   
            buoyLabelsInitialized = True     
            
        if(self.runupExists):
            with open(dataToGraph["RUNUP"]) as outfile:
                runupDataset = json.load(outfile)
                
            runupTimestampsInitialized = False
            for stationKey in runupDataset.keys():
                nodeIndex = runupDataset[stationKey]["nodeIndex"]
                self.runupLabels.append(nodeIndex)
                self.runupLatitudes.append(runupDataset[stationKey]["latitude"])
                self.runupLongitudes.append(runupDataset[stationKey]["longitude"])
                self.runupSurfDistance.append(str(round(runupDataset[stationKey]["surfDistance"], 2)))
                self.runupOffshoreDistance.append(str(round(runupDataset[stationKey]["offshoreDistance"], 2)))
                self.runupAverageSlope.append(str(round(runupDataset[stationKey]["averageSlope"], 5)))
                datapointRunup = []
                datapointWavelength = []
                datapointIribarren = []
                datapointSteepness = []
                tangentLatitudes = []
                tangentLongitudes = []
                runupLatitudes = []
                runupLongitudes = []
                datapointAverageSlopes = []
                datapointHolmanHigh = []
                datapointHolmanMid = []
                datapointHolmanLow = []
                datapointHolmanHighSetup = []
                datapointHolmanMidSetup = []
                datapointHolmanLowSetup = []
                datapointHolmanHighSwash = []
                datapointHolmanMidSwash = []
                datapointHolmanLowSwash = []
                datapointHolmanSwashIncident = []
                datapointHolmanSwashInfragravity = []
                datapointStockdonSetup = []
                datapointStockdonSetupLow = []
                datapointStockdonSwashIncident = []
                datapointStockdonSwashInfragravity = []
                datapointStockdonSwashLow = []
                datapointStockdonRunup = []
                datapointStockdonRunupNoSetup = []
                datapointStockdonRunupLow = []
                datapointAdcircSetup = []
                datapointAdcircRunup = []
                datapointDuneHeights = []
                
                for index in range(len(runupDataset[stationKey]["times"])):
                    if(self.runupStartDate == None):
                        self.runupStartDate = datetime.fromtimestamp(int(runupDataset[stationKey]["times"][index]), timezone.utc)
                    if(not runupTimestampsInitialized):
                        self.runupTimes.append(self.unixTimeToDeltaHours(runupDataset[stationKey]["times"][index], self.runupStartDate))
                    datapointRunup.append(runupDataset[stationKey]["runup"][index])
                    datapointWavelength.append(runupDataset[stationKey]["wavelength"][index])
                    datapointIribarren.append(runupDataset[stationKey]["iribarren"][index])
                    datapointSteepness.append(runupDataset[stationKey]["steepness"][index])
                    waterlineKey = runupDataset[stationKey]["waterlineKeys"][index]
                    if("d" in stationKey):
                        generalStationKey = stationKey[0:stationKey.index("d")]
                    elif(len(stationKey) == 3):
                        generalStationKey = stationKey[0:-1]
                    else:
                        generalStationKey = stationKey
                    waterlineLatitude = float(self.obsMetadata["NORMAL"][generalStationKey][waterlineKey]["latitude"])
                    waterlineLongitude = float(self.obsMetadata["NORMAL"][generalStationKey][waterlineKey]["longitude"])
                    waterlineTangentLatitude = float(self.obsMetadata["TANGENT"][generalStationKey][waterlineKey]["latitude"])
                    waterlineTangentLongitude = float(self.obsMetadata["TANGENT"][generalStationKey][waterlineKey]["longitude"])
                    tangentLatitude = [waterlineLatitude, waterlineTangentLatitude]
                    tangentLongitude = [waterlineLongitude, waterlineTangentLongitude]
                    tangentLatitudes.append(tangentLatitude)
                    tangentLongitudes.append(tangentLongitude)
                    runupLatitudes.append([runupDataset[stationKey]["runupWaterlineLatitudes"][index], runupDataset[stationKey]["runupTangentLatitudes"][index]])
                    runupLongitudes.append([runupDataset[stationKey]["runupWaterlineLongitudes"][index], runupDataset[stationKey]["runupTangentLongitudes"][index]])
                    datapointAverageSlopes.append(runupDataset[stationKey]["averageSlopes"][index])
                    datapointHolmanHigh.append(runupDataset[stationKey]["runupHolmanHigh"][index])
                    datapointHolmanMid.append(runupDataset[stationKey]["runupHolmanMid"][index])
                    datapointHolmanLow.append(runupDataset[stationKey]["runupHolmanLow"][index])
                    datapointHolmanHighSetup.append(runupDataset[stationKey]["setupHolmanHigh"][index])
                    datapointHolmanMidSetup.append(runupDataset[stationKey]["setupHolmanMid"][index])
                    datapointHolmanLowSetup.append(runupDataset[stationKey]["setupHolmanLow"][index])
                    datapointHolmanHighSwash.append(runupDataset[stationKey]["swashHolmanHigh"][index])
                    datapointHolmanMidSwash.append(runupDataset[stationKey]["swashHolmanMid"][index])
                    datapointHolmanLowSwash.append(runupDataset[stationKey]["swashHolmanLow"][index])
                    datapointHolmanSwashIncident.append(runupDataset[stationKey]["swashHolmanIncident"][index])
                    datapointHolmanSwashInfragravity.append(runupDataset[stationKey]["swashHolmanInfragravity"][index])
                    datapointStockdonSetup.append(runupDataset[stationKey]["setupStockdon"][index])
                    datapointStockdonSetupLow.append(runupDataset[stationKey]["setupStockdonLow"][index])
                    datapointStockdonSwashIncident.append(runupDataset[stationKey]["swashStockdonIncident"][index])
                    datapointStockdonSwashInfragravity.append(runupDataset[stationKey]["swashStockdonInfragravity"][index])
                    datapointStockdonSwashLow.append(runupDataset[stationKey]["swashStockdonLow"][index])
                    datapointStockdonRunup.append(runupDataset[stationKey]["runupStockdon"][index])
                    datapointStockdonRunupNoSetup.append(runupDataset[stationKey]["runupStockdonNoSetup"][index])
                    datapointStockdonRunupLow.append(runupDataset[stationKey]["runupStockdonLow"][index])
                    datapointAdcircSetup.append(runupDataset[stationKey]["setupAdcirc"][index])
                    datapointAdcircRunup.append(runupDataset[stationKey]["runupAdcirc"][index])
                    datapointDuneHeights.append(runupDataset[stationKey]["duneHeights"][index])
                    
                runupTimestampsInitialized = True
                self.datapointsRunup.append(datapointRunup)    
                self.datapointsWavelength.append(datapointWavelength)
                self.datapointsIribarren.append(datapointIribarren)
                self.datapointsSteepness.append(datapointSteepness)   
                self.datapointsWaterlineLatitudes.append(tangentLatitudes)
                self.datapointsWaterlineLongitudes.append(tangentLongitudes)   
                self.datapointsRunupLatitudes.append(runupLatitudes)
                self.datapointsRunupLongitudes.append(runupLongitudes)
                self.runupAverageSlopes.append(datapointAverageSlopes)
                self.datapointsRunupHolmanHigh.append(datapointHolmanHigh)
                self.datapointsRunupHolmanMid.append(datapointHolmanMid)
                self.datapointsRunupHolmanLow.append(datapointHolmanLow)
                self.datapointsSetupHolmanHigh.append(datapointHolmanHighSetup)
                self.datapointsSetupHolmanMid.append(datapointHolmanMidSetup)
                self.datapointsSetupHolmanLow.append(datapointHolmanLowSetup)
                self.datapointsSwashHolmanHigh.append(datapointHolmanHighSwash)
                self.datapointsSwashHolmanMid.append(datapointHolmanMidSwash)
                self.datapointsSwashHolmanLow.append(datapointHolmanLowSwash)
                self.datapointsSwashHolmanIncident.append(datapointHolmanSwashIncident)
                self.datapointsSwashHolmanInfragravity.append(datapointHolmanSwashInfragravity)
                self.datapointsSetupStockdon.append(datapointStockdonSetup)
                self.datapointsSetupStockdonLow.append(datapointStockdonSetupLow)
                self.datapointsSwashStockdonIncident.append(datapointStockdonSwashIncident)
                self.datapointsSwashStockdonInfragravity.append(datapointStockdonSwashInfragravity)
                self.datapointsSwashStockdonLow.append(datapointStockdonSwashLow)
                self.datapointsRunupStockdon.append(datapointStockdonRunup)
                self.datapointsRunupStockdonNoSetup.append(datapointStockdonRunupNoSetup)
                self.datapointsRunupStockdonLow.append(datapointStockdonRunupLow)
                self.datapointsSetupAdcirc.append(datapointAdcircSetup)
                self.datapointsRunupAdcirc.append(datapointAdcircRunup)
                self.datapointsDuneHeights.append(datapointDuneHeights)
                

    def generateGraphs(self):
        graph_directory = "graphs/"
        
        numberOfWindDatapoints = 0
        numberOfRainDatapoints = 0
        numberOfWaterDatapoints = 0
        numberOfEtaDatapoints = 0
        numberOfWaveDatapoints = 0
        numberOfElevationDatapoints = 0
        numberOfRunupDatapoints = 0
        numberOfVelocityDatapoints = 0
#         TODO: Currently, when graphing multiple products with obs on, OBS_STATIONS must contain the same number of station
#           entries for each type of product
        if(self.windExists):
            numberOfWindDatapoints = len(self.windLabels)
        if(self.wavesExists):
            numberOfWaveDatapoints = len(self.waveLabels)
        if(self.rainExists):
            numberOfRainDatapoints = len(self.rainLabels)
        if(self.waterExists):
            numberOfWaterDatapoints = len(self.waterLabels)
        if(self.meshExists):
            numberOfElevationDatapoints = len(self.elevationLabels)
        if(self.etaExists):
            numberOfEtaDatapoints = len(self.etaLabels)
        if(self.buoyExists):
            numberOfWaveDatapoints = len(self.buoyDatapointsTimes)
        if(self.tideExists):
            numberOfWaterDatapoints = len(self.tideDatapointsTimes)
        if(self.gaugeExists):
            numberOfRainDatapoints = len(self.gaugeDatapointsTimes)
        if(self.assetExists):
            numberOfElevationDatapoints = len(self.assetDatapointsElevation)
        if(self.obsExists):
            numberOfWindDatapoints = len(self.obsDatapointsTimes)
        if(self.runupExists):
            numberOfRunupDatapoints = len(self.runupLabels)
        if(self.velocityExists):
            numberOfVelocityDatapoints = len(self.velocityLabels)
        print("numberOfDatapoints Wind, Rain, Water, Wave, Eta, Elevation, Runup, Velocity", numberOfWindDatapoints, numberOfRainDatapoints, numberOfWaterDatapoints, numberOfWaveDatapoints, numberOfEtaDatapoints, numberOfElevationDatapoints, numberOfRunupDatapoints, numberOfVelocityDatapoints, flush=True)
        fig, ax = plt.subplots()
        print("maxWind", self.maxWind, "maxRain", self.maxRain, "maxWave", self.maxSWH, "maxWater", self.maxWater, "maxEta", self.maxEta, "maxElevation", self.maxElevation, "maxRunup", self.maxRunup, "maxVelocity", self.maxVelocity, flush=True)
        
        if(self.windExists):
            ax.scatter(self.obsLongitudes, self.obsLatitudes, label="Obs")
            ax.scatter(self.windLongitudes, self.windLatitudes, label="Wind")
        if(self.wavesExists):
            ax.scatter(self.tideLongitudes, self.tideLatitudes, label="Tide")
            ax.scatter(self.waveLongitudes, self.waveLatitudes, label="Waves")
        if(self.rainExists):
            ax.scatter(self.rainLongitudes, self.rainLatitudes, label="Rain")
            ax.scatter(self.gaugeLongitudes, self.gaugeLatitudes, label="Gauge")
        if(self.waterExists):
            ax.scatter(self.buoyLongitudes, self.buoyLatitudes, label="Buoy")
            ax.scatter(self.waterLongitudes, self.waterLatitudes, label="Water")
        if(self.meshExists):
            ax.scatter(self.assetLongitudes, self.assetLatitudes, label="Asset")
            ax.scatter(self.elevationLongitudes, self.elevationLatitudes, label="Mesh")
        if(self.etaExists):
            ax.scatter(self.etaLongitudes, self.etaLatitudes, label="Eta")
        if(self.runupExists):
            ax.scatter(self.runupLongitudes, self.runupLatitudes)
        if(self.velocityExists):
            ax.scatter(self.velocityLongitudes, self.velocityLatitudes, label="Velocity")
        ax.legend(loc="lower right")

        for index, label in enumerate(self.obsLabels):
            ax.annotate(label, (self.obsLongitudes[index], self.obsLatitudes[index]))
            if(self.windExists):
                ax.annotate(self.windLabels[index], (self.windLongitudes[index], self.windLatitudes[index]))
        for index, label in enumerate(self.buoyLabels):
            ax.annotate(label, (self.buoyLongitudes[index], self.buoyLatitudes[index]))
            if(self.wavesExists):
                ax.annotate(self.waveLabels[index], (self.waveLongitudes[index], self.waveLatitudes[index]))
        for index, label in enumerate(self.gaugeLabels):
            ax.annotate(label, (self.gaugeLongitudes[index], self.gaugeLatitudes[index]))
            if(self.rainExists):
                ax.annotate(self.rainLabels[index], (self.rainLongitudes[index], self.rainLatitudes[index]))
        for index, label in enumerate(self.tideLabels):
            ax.annotate(label, (self.tideLongitudes[index], self.tideLatitudes[index]))
            if(self.waterExists):
                ax.annotate(self.waterLabels[index], (self.waterLongitudes[index], self.waterLatitudes[index]))
            if(self.etaExists):
                ax.annotate(self.etaLabels[index], (self.etaLongitudes[index], self.etaLatitudes[index]))
        for index, label in enumerate(self.assetLabels):
            ax.annotate(label, (self.assetLongitudes[index], self.assetLatitudes[index]))
            if(self.meshExists):
                ax.annotate(self.elevationLabels[index], (self.elevationLongitudes[index], self.elevationLatitudes[index]))
            if(self.etaExists):
                ax.annotate(self.etaLabels[index], (self.etaLongitudes[index], self.etaLatitudes[index]))
        for index, label in enumerate(self.runupLabels):
            ax.annotate(label, (self.runupLongitudes[index], self.runupLatitudes[index]))
        for index, label in enumerate(self.velocityLabels):
            ax.annotate(label, (self.velocityLongitudes[index], self.velocityLatitudes[index]))

        plt.title("location of datapoints by data type")
        plt.xlabel("longitude")
        plt.ylabel("latitude")
        plt.savefig(graph_directory + 'closest_points.png')
        plt.close()
        
        img = mpimg.imread(self.backgroundMap)
        plotAxis = [self.backgroundAxis[0], self.backgroundAxis[1], self.backgroundAxis[3], self.backgroundAxis[2]]
        aspectRatio = (self.backgroundAxis[1] - self.backgroundAxis[0]) / (self.backgroundAxis[2] - self.backgroundAxis[3])
#         img = mpimg.imread('subsetFlipped.png')
#         img = mpimg.imread('NorthAtlanticBasin3.png')
        if(len(self.mapWindTimes) > 0):
            vmin = 0
#             vmax = math.ceil(self.maxWind)
            vmax = 50
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)
            if(self.windType == "FORT"):
                windTriangulation = Triangulation(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, triangles=self.mapWindTriangles, mask=self.mapWindMaskedTriangles)
            for index in range(len(self.mapWindTimes)):
                fig, ax = plt.subplots()
#                 plt.figure(figsize=(6, 6))
    #             print(self.endWindPointsLongitudes)
    #             print(self.endWindPointsLatitudes)
    #             print(self.endSpeeds)
                plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
#                 plt.imshow(img, alpha=0.5, extent=[-76.59179620444773, -63.41595750651321, 46.70943547053439, 36.92061410517965], zorder=2)
                if(self.windType == "FORT"):
#                     plt.scatter(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, c=self.mapSpeeds[index], alpha=0.5, label="Forecast", marker=".")
                    contourset = ax.tricontourf(windTriangulation, self.mapSpeeds[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax, zorder=1)
                elif(self.windType == "POST"):
#                     plt.scatter(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, c=self.mapSpeeds[index], alpha=0.3, label="Forecast", marker=".", s=100)
#                     contourset = ax.tricontourf(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, self.mapSpeeds[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax)
                    contourset = ax.pcolormesh(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, self.mapSpeeds[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
                elif(self.windType == "GFS"):
#                     plt.scatter(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, c=self.mapSpeeds[index], alpha=0.3, label="Forecast", marker=".", s=3600)
#                     contourset = ax.tricontourf(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, self.mapSpeeds[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax)
#                     print(len(self.mapWindPointsLongitudes), len(self.mapWindPointsLatitudes), len(self.mapSpeeds[index]))
                    contourset = ax.pcolormesh(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, self.mapSpeeds[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
                plt.axis(plotAxis)
#                 plt.axis([-76.59179620444773, -63.41595750651321, 36.92061410517965, 46.70943547053439])
                plt.title("Wind Speed")
                plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#                 plt.xlabel(datetime.fromtimestamp(timestamp, timezone.utc))
    #             graphs up to 10 m/s, ~20 knots
                plt.colorbar(
                    ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                    ticks=range(vmin, vmax+5, 5),
                    boundaries=levelBoundaries,
                    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                    label="Meters/Second",
                    ax=plt.gca()
                )
                plt.savefig(graph_directory + 'map_wind_' + str(index) + '.png')
                plt.close()
                gc.collect()
            writeMapAnimation(graph_directory, "map_wind_", len(self.mapWindTimes), "wind")
            mapSpeedsNoNan = np.nan_to_num(self.mapSpeeds)
            swathWind = np.max(mapSpeedsNoNan, axis=0)
            fig, ax = plt.subplots()
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            if(self.windType == "FORT"):
                contourset = ax.tricontourf(windTriangulation, self.mapSpeeds[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax, zorder=1)
            else:
                contourset = ax.pcolormesh(self.mapWindPointsLongitudes, self.mapWindPointsLatitudes, swathWind, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
            plt.axis(plotAxis)
            plt.title("Wind Swath")
#             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#             graphs up to 10 m/s, ~20 knots
            plt.colorbar(
                ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                ticks=range(vmin, vmax+5, 5),
                boundaries=levelBoundaries,
                values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                label="Meters/Second",
                ax=plt.gca()
            )
            plt.savefig(graph_directory + 'map_wind_swath.png')
            plt.close()
            gc.collect()
        if(len(self.mapRainTimes) > 0):
            vmin = 0
            vmax = math.ceil(self.maxRain)
            vmax = 25
            vmax = 5
            vmaxAccumulation = 500
#             vmaxAccumulation = 10
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)
            levelBoundariesAccumulation = np.linspace(vmin, vmaxAccumulation, levels + 1)
            for index in range(len(self.mapRainTimes)):
                fig, ax = plt.subplots()
    #             print(self.endWavePointsLongitudes)
    #             print(self.endWavePointsLatitudes)
    #             print(self.endSWH)
                plt.imshow(img, extent=self.backgroundAxis, alpha=0.6, aspect=aspectRatio, zorder=2)
#                 contourset = ax.tricontourf(self.mapRainPointsLongitudes, self.mapRainPointsLatitudes, self.mapRains[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax)
                contourset = ax.pcolormesh(self.mapRainPointsLongitudes, self.mapRainPointsLatitudes, self.mapRains[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
                plt.axis(plotAxis)
                plt.title("Rain")
                plt.xlabel(datetime.fromtimestamp(int(self.mapRainTimes[index]), timezone.utc))
    #             plt.gca().invert_yaxis()
                plt.colorbar(
                    ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                    ticks=range(vmin, vmax+5, 5),
                    boundaries=levelBoundaries,
                    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                    label="Millimeters/Hour",
                    ax=plt.gca()
                )
                plt.savefig(graph_directory + 'map_rain_' + str(index) + '.png')
                plt.close()
                gc.collect()
            writeMapAnimation(graph_directory, "map_rain_", len(self.mapRainTimes), "rain")
            mapRainsNoNan = np.nan_to_num(self.mapRains)
            accumulatedRain = np.sum(mapRainsNoNan, axis=0)
            fig, ax = plt.subplots()
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            contourset = ax.pcolormesh(self.mapRainPointsLongitudes, self.mapRainPointsLatitudes, accumulatedRain, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmaxAccumulation, zorder=1)
            plt.axis(plotAxis)
            plt.title("Rain Accumulation")
#             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#             graphs up to 10 m/s, ~20 knots
            plt.colorbar(
                ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
#                 Increase vmax by factor of length of time to fit accumulation
                ticks=range(vmin, vmaxAccumulation+5, 50),
                boundaries=levelBoundariesAccumulation,
                values=(levelBoundariesAccumulation[:-1] + levelBoundariesAccumulation[1:]) / 2,
                label="Millimeters",
                ax=plt.gca()
            )
            plt.savefig(graph_directory + 'map_rain_accumulation.png')
            plt.close()
            gc.collect()
        if(len(self.mapElevation) > 0):
            vmin = -30
            vmax = 10
#             vmax = math.ceil(self.maxElevation)
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)
            # waveTriangulation = Triangulation(self.mapWavePointsLongitudes, self.mapWavePointsLatitudes, triangles=self.mapWaveTriangles, mask=self.mapWaveMaskedTriangles)
#             print("triangle len", self.mapElevationTriangles)
            elevationTriangulation = Triangulation(self.mapElevationPointsLongitudes, self.mapElevationPointsLatitudes, triangles=self.mapElevationTriangles, mask=self.mapElevationMaskedTriangles)
            fig, ax = plt.subplots(figsize=(9,9))
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            contourset = ax.tripcolor(elevationTriangulation, self.mapElevation, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
#             ax.scatter(self.mapElevationPointsLongitudes, self.mapElevationPointsLatitudes, label="Nodes", alpha=0.1, marker=".", s=1, zorder=4, color="purple")
#             if(self.assetExists):
#                 ax.scatter(self.assetLongitudes, self.assetLatitudes, label="Assets", zorder=3, alpha=0.7, marker=".", s=40, color="black")
            ax.scatter(self.assetLongitudes, self.assetLatitudes, label="Assets", zorder=3, alpha=0.7, marker=".", s=40, color="black")

#             Below line graphs mesh points
#             ax.scatter(self.mapElevationPointsLongitudes, self.mapElevationPointsLatitudes, label="Nodes", zorder=3, alpha=0.7, marker=".", s=1, color="black")
#           Below line graphs ASSET points without the need for observational asset data to have been generated
#             ax.scatter(self.elevationLongitudes, self.elevationLatitudes, label="Data Locations", zorder=3, alpha=0.7, marker=".", s=40, color="black")
#             for index in range(len(self.datapointsElevation)):
#                 ax.annotate(str(round(self.datapointsElevation[index], 2)), (self.elevationLongitudes[index], self.elevationLatitudes[index]))
            plt.axis(plotAxis)
            plt.title("Elevation Map")
#             plt.title("Map Elevation - " + "surf distance: " + self.runupSurfDistance[index] + " offshore distance: " + self.runupOffshoreDistance[index] + " slope: " + self.runupAverageSlope[index])
#             ax.legend(loc="upper right")
#             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#             graphs up to 10 m/s, ~20 knots
            plt.colorbar(
                ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                ticks=range(vmin, vmax+5, 10),
                boundaries=levelBoundaries,
                values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                label="Meters",
                ax=plt.gca()
            )        
            plt.savefig(graph_directory + 'map_elevation.png')
            plt.close()
            gc.collect()
        if(len(self.mapEtaTimes) > 0):
            vmin = -1
            vmax = math.ceil(self.maxEta)
#             vmax = 20
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)
            for index in range(len(self.mapEtaTimes)):
                fig, ax = plt.subplots()
    #             print(self.endWavePointsLongitudes)
    #             print(self.endWavePointsLatitudes)
    #             print(self.endSWH)
                plt.imshow(img, extent=self.backgroundAxis, alpha=0.6, aspect=aspectRatio, zorder=2)
                contourset = ax.pcolormesh(self.mapEtaPointsLongitudes, self.mapEtaPointsLatitudes, self.mapEta[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
#               Todo: Fix triangulation errors
#                 contourset = ax.tripcolor(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes, self.mapWaters[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
                plt.axis(plotAxis)
                plt.title("Eta Elevation")
                plt.xlabel(datetime.fromtimestamp(int(self.mapEtaTimes[index]),timezone.utc))
    #             plt.gca().invert_yaxis()
                plt.colorbar(
                    ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                    ticks=range(vmin, vmax+5, 2),
                    boundaries=levelBoundaries,
                    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                    label="Meters",
                    ax=plt.gca()
                )
                plt.savefig(graph_directory + 'map_eta_' + str(index) + '.png')
                plt.close()
                gc.collect()
            writeMapAnimation(graph_directory, "map_eta_", len(self.mapEtaTimes), "eta")
            mapEtaNoNan = np.nan_to_num(self.mapEta)
            swathEta = np.max(self.mapEta, axis=0)
            fig, ax = plt.subplots()
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            contourset = ax.pcolormesh(self.mapEtaPointsLongitudes, self.mapEtaPointsLatitudes, swathEta, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
            plt.axis(plotAxis)
            plt.title("Eta Swath")
#             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#             graphs up to 10 m/s, ~20 knots
            plt.colorbar(
                ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                ticks=range(vmin, vmax+5, 2),
                boundaries=levelBoundaries,
                values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                label="Meters",
                ax=plt.gca()
            )
            plt.savefig(graph_directory + 'map_eta_swath.png')
            plt.close()
            gc.collect()
        if(len(self.mapWaterTimes) > 0):
            vmin = -1
            vminSwath = 0
#             vmax = math.ceil(self.maxWater)
            vmax = 3
#             vmax = 20
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)
            levelBoundariesSwath = np.linspace(vminSwath, vmax, levels + 1)

            # Diverging colormap centered on the datum (0 m) instead of the
            # rainbow "jet" map, which has no perceptual ordering for magnitude.
            waterCmap = plt.get_cmap("RdBu_r")
            waterNorm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
            # Swath is always >= 0 (max over the run), so a one-hue sequential
            # ramp reads better than a diverging one with nothing below center.
            swathCmap = plt.get_cmap("Blues")

            assetColor = "#eb6834"       # orange
            obsColor = "#256abf"         # blue
            buoyColor = "#1baf7a"        # aqua
            datapointColor = "#4a3aa7"   # violet

            # The mesh is turned into a pixel grid once and every frame reuses one
            # figure, so a frame costs one numpy gather and a redraw instead of a
            # tripcolor of ~1M triangles plus a fresh figure.
            print("Rasterizing water mesh for the map frames", flush=True)
            rasterizer = MeshRasterizer(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes,
                                        self.mapWaterTriangles, self.mapWaterMaskedTriangles, plotAxis)
            self.writeWaterVideo(graph_directory, rasterizer, plotAxis, img)
            gc.collect()

            swathWaters = np.max(self.mapWaters, axis=0)
            print(len(swathWaters), len(self.mapWaterMaskedTriangles))
            fig, ax = plt.subplots(figsize=(9,9), dpi=150)
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            # A node that never got wet keeps the -99999 fill in the max, so it stays blank
            contourset = rasterizer.imshow(ax, swathWaters, cmap=swathCmap, vmin=vminSwath, vmax=vmax, aspect=aspectRatio, zorder=1)
            ax.scatter(self.waterLongitudes, self.waterLatitudes, label="Datapoints", color=datapointColor, edgecolors="white", linewidths=0.5, s=30, zorder=3)
            if(self.buoyExists):
                    ax.scatter(self.buoyLongitudes, self.buoyLatitudes, label="Buoy", zorder=3, color=buoyColor, edgecolors="white", linewidths=0.5, s=30)
            if(self.meshExists):
                ax.scatter(self.assetLongitudes, self.assetLatitudes, label="Assets", zorder=4, alpha=0.85, marker="o", s=35, color=assetColor, edgecolors="white", linewidths=0.5)

            plt.axis(plotAxis)
            ax.set_title(self.titlePrefix + "Water Swath", fontsize=14, fontweight="bold")
#             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#             graphs up to 10 m/s, ~20 knots
            ax.legend(loc="upper right", framealpha=0.9, fontsize=8)
            plt.colorbar(
                ScalarMappable(norm=mcolors.Normalize(vmin=vminSwath, vmax=vmax), cmap=swathCmap),
                boundaries=levelBoundariesSwath,
                values=(levelBoundariesSwath[:-1] + levelBoundariesSwath[1:]) / 2,
                label="Max Water Elevation (m)",
                ax=plt.gca()
            )
            plt.savefig(graph_directory + 'map_water_swath.png', bbox_inches="tight")
            plt.close()
            gc.collect()
        if(len(self.mapVelocityTimes) > 0):
            vmin = 0
            vmax = math.ceil(self.maxVelocity)
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)

            # Speed is always >= 0, so a one-hue sequential ramp reads better
            # than a diverging colormap.
            velocityCmap = plt.get_cmap("viridis")
            datapointColor = "#4a3aa7"   # violet

            for index in range(len(self.mapVelocityTimes)):
                fig, ax = plt.subplots(figsize=(9,9), dpi=150)
                plt.imshow(img, extent=self.backgroundAxis, alpha=0.6, aspect=aspectRatio, zorder=2)
                currentMaskedTriangles = self.mapVelocityMaskedTriangles.copy()
                for triangleIndex, triangle in enumerate(self.mapVelocityTriangles):
                    for pointIndex in triangle:
                        speed = self.mapVelocitySpeeds[index][pointIndex]
                        if(speed == -99999.0):
                            currentMaskedTriangles[triangleIndex] = True
                            break
                velocityTriangulation = Triangulation(self.mapVelocityPointsLongitudes, self.mapVelocityPointsLatitudes, triangles=self.mapVelocityTriangles, mask=currentMaskedTriangles)

                ax.tripcolor(velocityTriangulation, self.mapVelocitySpeeds[index], shading='gouraud', cmap=velocityCmap, vmin=vmin, vmax=vmax, zorder=1)
                ax.scatter(self.velocityLongitudes, self.velocityLatitudes, label="Datapoints", color=datapointColor, edgecolors="white", linewidths=0.5, s=30, zorder=3)

                plt.axis(plotAxis)
                ax.set_title(self.titlePrefix + "Water Velocity", fontsize=14, fontweight="bold")
                ax.set_xlabel(datetime.fromtimestamp(self.mapVelocityTimes[index], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), fontsize=10)
                ax.legend(loc="upper right", framealpha=0.9, fontsize=8)
                plt.colorbar(
                    ScalarMappable(norm=mcolors.Normalize(vmin=vmin, vmax=vmax), cmap=velocityCmap),
                    boundaries=levelBoundaries,
                    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                    label="Velocity (m/s)",
                    ax=plt.gca()
                )
                plt.savefig(graph_directory + 'map_velocity_' + str(index) + '.png', bbox_inches="tight")
                plt.close()
                gc.collect()
            writeMapAnimation(graph_directory, "map_velocity_", len(self.mapVelocityTimes), "velocity")

            swathVelocity = np.max(self.mapVelocitySpeeds, axis=0)
            swathMaskedTriangles = self.mapVelocityMaskedTriangles.copy()
            for index, triangle in enumerate(self.mapVelocityTriangles):
                for pointIndex in triangle:
                    speed = swathVelocity[pointIndex]
                    if(speed == -99999.0):
                        swathMaskedTriangles[index] = True
                        break
            velocityTriangulation = Triangulation(self.mapVelocityPointsLongitudes, self.mapVelocityPointsLatitudes, triangles=self.mapVelocityTriangles, mask=swathMaskedTriangles)
            fig, ax = plt.subplots(figsize=(9,9), dpi=150)
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            ax.tripcolor(velocityTriangulation, swathVelocity, shading='gouraud', cmap=velocityCmap, vmin=vmin, vmax=vmax, zorder=1)
            ax.scatter(self.velocityLongitudes, self.velocityLatitudes, label="Datapoints", color=datapointColor, edgecolors="white", linewidths=0.5, s=30, zorder=3)
            plt.axis(plotAxis)
            ax.set_title(self.titlePrefix + "Water Velocity Swath", fontsize=14, fontweight="bold")
            ax.legend(loc="upper right", framealpha=0.9, fontsize=8)
            plt.colorbar(
                ScalarMappable(norm=mcolors.Normalize(vmin=vmin, vmax=vmax), cmap=velocityCmap),
                boundaries=levelBoundaries,
                values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                label="Max Velocity (m/s)",
                ax=plt.gca()
            )
            plt.savefig(graph_directory + 'map_velocity_swath.png', bbox_inches="tight")
            plt.close()
            gc.collect()
        if(len(self.mapWaveTimes) > 0):
            vmin = 0
            vmax = math.ceil(self.maxSWH)
            vmax = 5
            levels = 100
            levelBoundaries = np.linspace(vmin, vmax, levels + 1)
            # waveTriangulation = Triangulation(self.mapWavePointsLongitudes, self.mapWavePointsLatitudes, triangles=self.mapWaveTriangles, mask=self.mapWaveMaskedTriangles)
            for index in range(len(self.mapWaveTimes)):
                fig, ax = plt.subplots()
    #             print(self.endWavePointsLongitudes)
    #             print(self.endWavePointsLatitudes)
    #             print(self.endSWH)
    
                currentMaskedTriangles = self.mapWaveMaskedTriangles.copy()
                for triangleIndex, triangle in enumerate(self.mapWaveTriangles):
                    for pointIndex in triangle:
                        swh = self.mapSWH[index][pointIndex]
    #                     Check for nan value
    #                     point = (self.mapWaterPointsLongitudes[pointIndex], self.mapWaterPointsLatitudes[pointIndex])
                        if(swh == -99999.0):
    #                     if(point[0] < -72.1 and point[0] > -72.15 and point[1] > 41.4 and point[1] < 41.42):
    #                         print("point, water", point, water)
                            currentMaskedTriangles[triangleIndex] = True
                            break
                waveTriangulation = Triangulation(self.mapWavePointsLongitudes, self.mapWavePointsLatitudes, triangles=self.mapWaveTriangles, mask=currentMaskedTriangles)

                plt.imshow(img, extent=self.backgroundAxis, aspect=aspectRatio)
                contourset = ax.tricontourf(waveTriangulation, self.mapSWH[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax)
                plt.axis(plotAxis)
                plt.title("Significant Wave Height")
                plt.xlabel(datetime.fromtimestamp(int(self.mapWaveTimes[index]),timezone.utc))
    #             plt.gca().invert_yaxis()
                plt.colorbar(
                    ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                    ticks=range(vmin, vmax+5, 5),
                    boundaries=levelBoundaries,
                    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                    label="Meters",
                    ax=plt.gca()
                )                
                plt.savefig(graph_directory + 'map_swh_' + str(index) + '.png')
                plt.close()
                gc.collect()
            writeMapAnimation(graph_directory, "map_swh_", len(self.mapWaveTimes), "wave")
            swathSWH = np.max(self.mapSWH, axis=0)
            for index, triangle in enumerate(self.mapWaveTriangles):
                for pointIndex in triangle:
                    swh = swathSWH[pointIndex]
#                     Check for nan value
#                     point = (self.mapWaterPointsLongitudes[pointIndex], self.mapWaterPointsLatitudes[pointIndex])
                    if(swh == -99999.0):
#                     if(point[0] < -72.1 and point[0] > -72.15 and point[1] > 41.4 and point[1] < 41.42):
#                         print("point, water", point, water)
                        self.mapWaveMaskedTriangles[index] = True
                        break
            waveTriangulation = Triangulation(self.mapWavePointsLongitudes, self.mapWavePointsLatitudes, triangles=self.mapWaveTriangles, mask=self.mapWaveMaskedTriangles)
            fig, ax = plt.subplots()
            plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
            contourset = ax.tricontourf(waveTriangulation, swathSWH, levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax, zorder=1)
            ax.scatter(self.waveLongitudes, self.waveLatitudes, label="Datapoints")
            if(self.tideExists):
                    ax.scatter(self.tideLongitudes, self.tideLatitudes, label="Tide", zorder=3)
            plt.axis(plotAxis)
            plt.title("Wave Significant Wave Height Swath")
#             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
#             graphs up to 10 m/s, ~20 knots
            plt.colorbar(
                ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
                ticks=range(vmin, vmax+5, 5),
                boundaries=levelBoundaries,
                values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
                label="Meters",
                ax=plt.gca()
            )        
            plt.savefig(graph_directory + 'map_swh_swath.png')
            plt.close()
            gc.collect()
        # Plot wind speed over time
        for index in range(numberOfWindDatapoints):
            if(len(self.datapointsSpeeds) > 0):
                fig, ax = plt.subplots(figsize=(16,9))
                ax.scatter(self.windTimes, self.datapointsSpeeds[index], marker=".", label="Forecast")
                if(self.obsExists):
                    ax.scatter(self.obsDatapointsTimes[index], self.obsDatapointsSpeeds[index], marker=".", label="Obs")
                ax.legend(loc="lower right")
#                 ax.set_ylim([0, 50])
                stationName = self.obsLabels[index]
                plt.title(stationName + " station wind speed", fontsize=24)
#                 plt.xlabel("Hours since " + self.windStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("wind speed (m/s)")
                plt.savefig(graph_directory + stationName + '_wind_speed.png')
                plt.close()
            if(len(self.datapointsDirections) > 0):
                fig, ax = plt.subplots(figsize=(16,9))
                ax.scatter(self.windTimes, self.datapointsDirections[index], marker=".", label="Forecast")
                if(self.obsExists):
                    ax.scatter(self.obsDatapointsTimes[index], self.obsDatapointsDirections[index], marker=".", label="Obs")
                ax.legend(loc="lower right")
#                 ax.set_ylim([0, 50])
                stationName = self.obsLabels[index]
                plt.title(stationName + " station wind directions", fontsize=24)
                plt.ylabel("wind direction (degrees)")
                plt.savefig(graph_directory + stationName + '_wind_direction.png')
                plt.close()
        # Plot water velocity (current speed/direction) over time
        for index in range(numberOfVelocityDatapoints):
            stationName = self.velocityStationNames[index]
            if(len(self.datapointsVelocitySpeeds) > 0):
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.velocityTimes, self.datapointsVelocitySpeeds[index], label="Forecast")
                ax.legend(loc="lower right")
                plt.title(self.titlePrefix + stationName + " station current speed", fontsize=24)
                plt.ylabel("current speed (m/s)")
                plt.xticks(rotation=45, ha='right')
                plt.savefig(graph_directory + stationName + '_velocity_speed.png', bbox_inches='tight')
                plt.close()
            if(len(self.datapointsVelocityDirections) > 0):
                fig, ax = plt.subplots(figsize=(16,9))
                ax.scatter(self.velocityTimes, self.datapointsVelocityDirections[index], marker=".", label="Forecast")
                ax.legend(loc="lower right")
                plt.title(self.titlePrefix + stationName + " station current direction", fontsize=24)
                plt.ylabel("current direction (degrees)")
                plt.xticks(rotation=45, ha='right')
                plt.savefig(graph_directory + stationName + '_velocity_direction.png', bbox_inches='tight')
                plt.close()
        for index in range(numberOfRainDatapoints):
            if(len(self.datapointsRains) > 0):
                fig, ax = plt.subplots()
                ax.scatter(self.rainTimes, self.datapointsRains[index], marker=".", label="Forecast")
                if(self.gaugeExists):
                    ax.plot(self.gaugeDatapointsTimes[index], self.gaugeDatapointsRains[index], label="Gauge")
                    gaugeNoNan = np.nan_to_num(self.gaugeDatapointsRains[index])
                    accumulationGauge = str(round(np.sum(gaugeNoNan), 2))
                    accumulationSeriesGauge = []
                    for rainIndex, gaugeRain in enumerate(gaugeNoNan):
                        if(rainIndex == 0):
                            accumulationSeriesGauge.append(gaugeRain)
                        else:
                            accumulationSeriesGauge.append(gaugeRain + accumulationSeriesGauge[rainIndex - 1])

                else:
                    accumulationGauge = "NA"
                    accumulationSeriesGauge = []
                ax.legend(loc="lower right")
                stationName = self.gaugeLabels[index]
                

                rainNoNan = np.nan_to_num(self.datapointsRains[index])
                accumulationRain = str(round(np.sum(rainNoNan), 2))
                accumulationSeriesRain = []
                for rainIndex, rain in enumerate(rainNoNan):
                    if(rainIndex == 0):
                        accumulationSeriesRain.append(rain)
                    else:
                        accumulationSeriesRain.append(rain + accumulationSeriesRain[rainIndex - 1])
                plt.title(stationName + " rain-accumulation forecast/gauge:" + accumulationRain + "/" + accumulationGauge)
                plt.xlabel("Hours since " + self.rainStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("rain (mm/hr)")
                plt.savefig(graph_directory + stationName + '_rain.png')
                plt.close()
#                Plot accumulation series
                fig, ax = plt.subplots()
                ax.scatter(self.rainTimes, accumulationSeriesRain, marker=".", label="Forecast")
                if(self.gaugeExists):
                    ax.plot(self.gaugeDatapointsTimes[index], accumulationSeriesGauge, label="Gauge")
                ax.legend(loc="lower right")
                plt.title(stationName + " accumulated rain- forecast/gauge:" + accumulationRain + "/" + accumulationGauge)
                plt.xlabel("Hours since " + self.rainStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("rain (mm)")
                plt.savefig(graph_directory + stationName + '_rain_accumulation.png')
                plt.close()
        statisticsCsvRows = []
        for index in range(numberOfWaterDatapoints):
            if(len(self.datapointsWaters) > 0):
                # Publication style figure: compact layout, title and a small
                # forcing/date subtitle in place of the global banner
                # Model series, each with its statistics against the observations
                modelSeries = []
                if(self.stillwaterExists):
                    modelSeries.append(("ADCIRC Stillwater", self.stillwaterTimes, self.datapointsStillwaters[index], ":"))
                if(self.tidewaterExists):
                    modelSeries.append(("ADCIRC Tide Only", self.tidewaterTimes, self.datapointsTidewaters[index], "-."))
                modelSeries.append(("ADCIRC", self.waterTimes, self.datapointsWaters[index], "-"))
                seriesStatistics = []
                if(self.tideExists):
                    for label, times, values, style in modelSeries:
                        seriesStatistics.append(waterStatistics(times, values, self.tideDatapointsTimes[index],
                                                                self.tideDatapointsWaters[index]))
                statisticsRows = sum(stats is not None for stats in seriesStatistics)
                if statisticsRows:
                    tableHeight = 0.3 * statisticsRows + 0.85
                    fig, (ax, statisticsAx) = plt.subplots(2, 1, figsize=(11, 5.5 + tableHeight), layout="constrained",
                                                           height_ratios=[5.5, tableHeight])
                else:
                    fig, ax = plt.subplots(figsize=(11, 5.5), layout="constrained")
                fig.richampBannerDrawn = True
                lineWidth = 2.0
                seriesColors = []
                for label, times, values, style in modelSeries:
                    # ADCIRC keeps C0 and the observations C1; the variants get their own colors
                    color = {"ADCIRC": "C0", "ADCIRC Stillwater": "C2", "ADCIRC Tide Only": "C4"}[label]
                    line, = ax.plot(times, values, label=label, color=color, linestyle=style, linewidth=lineWidth)
                    seriesColors.append(line.get_color())
                if(self.tideExists):
                    ax.plot(self.tideDatapointsTimes[index], self.tideDatapointsWaters[index], label=self.obsWaterLabel, color="C1", linestyle="--", linewidth=lineWidth)
#                     ax.plot(self.tideDatapointsPredictionTimes[index], self.tideDatapointsPredictionWaters[index], label="Tides")
                stationName = self.tideLabels[index]
                stationId = self.tideIds[index] if index < len(self.tideIds) else ""
                maxElevation = str(round(max(self.datapointsWaters[index]), 2))

                title = self.titlePrefix + stationName
                if stationId:
                    title += " — Station " + stationId
                fig.suptitle(title, fontsize=15, fontweight="bold")
                subtitle = bannerSubtitle(self.stormBanner, self.waterTimes[0], self.waterTimes[-1])
                if subtitle:
                    ax.set_title(subtitle, fontsize=11, color="0.25")

                ax.set_ylabel("Water Level (m)", fontsize=12)
                ax.tick_params(labelsize=10)
                ax.margins(x=0)
                # Headroom above the highest crest so the legend sits clear of the data
                low, high = ax.get_ylim()
                ax.set_ylim(low, high + 0.15 * (high - low))
                ax.xaxis.set_major_locator(mdates.DayLocator(tz=timezone.utc))
                ax.xaxis.set_major_formatter(plt.FuncFormatter(
                    lambda value, position: (lambda d: f"{d:%b} {d.day}")(mdates.num2date(value, tz=timezone.utc))))
                plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
                ax.grid(True, which="major", color="0.88", linewidth=0.6)
                ax.set_axisbelow(True)
                ax.set_facecolor("white")
                ax.legend(loc="best", fontsize=10, frameon=True, framealpha=0.9, edgecolor="0.8",
                          ncol=2, handlelength=2.6, borderpad=0.4, columnspacing=1.2)
                if statisticsRows:
                    tableRows = []
                    for (label, times, values, style), color, stats in zip(modelSeries, seriesColors, seriesStatistics):
                        if stats is not None:
                            tableRows.append((label, color, stats))
                            statisticsCsvRows.append([stationName, stationId, label, stats["n"], stats["bias"],
                                                      stats["mae"], stats["rmse"], stats["r"]])
                    drawStatisticsTable(statisticsAx, tableRows, self.obsWaterLabel)
                fig.savefig(graph_directory + stationName + '_water.png', dpi=200, facecolor="white", bbox_inches='tight', pad_inches=0.05)
                plt.close()
                

                if(self.tideExists):
                    fig, ax = plt.subplots(figsize=(16,9))
                    ax.plot(self.tideDatapointsTimes[index], self.tideDatapointsWaters[index], label="Station")
                    ax.legend(loc="upper left")
                    ax.format_xdata = mdates.DateFormatter('%d')
                    stationName = self.tideLabels[index]
                    plt.title(self.titlePrefix + stationName + " station water depth")
#                     plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("depth (meters)")
                    plt.savefig(graph_directory + stationName + '_station_water.png')
                    plt.close()
        if statisticsCsvRows:
            # The same numbers as the tables under the station plots, all stations in one file
            import csv
            with open(graph_directory + "water_statistics.csv", "w", newline="") as statisticsFile:
                writer = csv.writer(statisticsFile)
                writer.writerow(["station", "station_id", "model", "n", "bias_m", "mae_m", "rmse_m", "r"])
                for row in statisticsCsvRows:
                    writer.writerow(row[:4] + [f"{value:.4f}" for value in row[4:]])
#         No loop because no timeseries
        if(len(self.datapointsElevation) > 0):
            fig, ax = plt.subplots(figsize=(16,13))
#             print(len(self.assetLabels), len(self.datapointsElevation))
            ax.scatter(self.assetLabels, self.datapointsElevation, label="Mesh")
            if(self.assetExists):
                ax.scatter(self.assetLabels, self.assetDatapointsElevation, label="Asset")
#                     ax.plot(self.tideDatapointsPredictionTimes[index], self.tideDatapointsPredictionWaters[index], label="Prediction")
            ax.legend(loc="upper left")
            plt.title("Asset Elevation vs. Mesh Elevation", fontsize=18)
            plt.xlabel("asset name")
            plt.xticks(fontsize=8, rotation=45, ha='right')
            plt.yticks(fontsize=14)
            plt.ylabel("elevation (meters)", fontsize=14)
            plt.savefig(graph_directory + "elevation.png")
            plt.close()
        for index in range(numberOfEtaDatapoints):
            if(len(self.datapointsEta) > 0):
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.etaTimes, self.datapointsEta[index], label="Forecast")
                if(self.tideExists):
                    ax.plot(self.tideDatapointsTimes[index], self.tideDatapointsWaters[index], label="Station")
                    ax.plot(self.tideDatapointsPredictionTimes[index], self.tideDatapointsPredictionWaters[index], label="Prediction")
                ax.legend(loc="upper left")
                stationName = self.tideLabels[index]
                plt.title(stationName + " station eta elevation")
                plt.xlabel("Hours since " + self.etaStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("eta (meters)")
                plt.savefig(graph_directory + stationName + '_eta.png')
                plt.close()
        for index in range(numberOfWaveDatapoints):
            if(self.wavesExists):
                if(len(self.datapointsSWH[index]) > 0):
                    fig, ax = plt.subplots(figsize=(16,9))
                    ax.scatter(self.waveTimes, self.datapointsSWH[index], marker=".", label=r"$H_s$")
                    if(self.buoyExists):
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsSWH[index], label="Obs")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " significant wave height", fontsize=24)
#                     plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    ax.format_xdata = mdates.DateFormatter('%d')
                    plt.ylabel("SWH (meters)")
                    plt.savefig(graph_directory + stationName + '_wave_swh.png')
                    plt.close()
                if(len(self.datapointsMWD[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsMWD[index], marker=".", label="Forecast")
                    if(self.buoyExists):
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsMWD[index], label="Buoy")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station mean wave direction", fontsize=24)
#                     plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    ax.format_xdata = mdates.DateFormatter('%d')
                    plt.ylabel("MWD (degrees)")
                    plt.savefig(graph_directory + stationName + '_wave_mwd.png')
                    plt.close()
                if(len(self.datapointsMWP[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsMWP[index], marker=".", label="Forecast")
                    if(self.buoyExists):
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsMWP[index], label="Buoy")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station mean wave period", fontsize=24)
#                     plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    ax.format_xdata = mdates.DateFormatter('%d')
                    plt.ylabel("MWP (seconds)")
                    plt.savefig(graph_directory + stationName + '_wave_mwp.png')
                    plt.close()
                if(len(self.datapointsPWP[index]) > 0):
                    fig, ax = plt.subplots(figsize=(16,9))
                    ax.scatter(self.waveTimes, self.datapointsPWP[index], marker=".", label=r"$T_p$")
                    if(self.buoyExists):
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsPWP[index], label="Obs")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " peak wave period", fontsize=24)
#                     plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    ax.format_xdata = mdates.DateFormatter('%d')
                    plt.ylabel("PWP (seconds)")
                    plt.savefig(graph_directory + stationName + '_wave_pwp.png')
                    plt.close()
                if(len(self.datapointsRADMag[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsRADMag[index], marker=".", label="Forecast")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station radiation stress magnitude", fontsize=24)
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("Rad Stress Magitude (1/m^2s^2)")
                    plt.savefig(graph_directory + stationName + '_wave_radstress_mag.png')
                    plt.close()
                if(len(self.datapointsRADDir[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsRADDir[index], marker=".", label="Forecast")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station radiation stress direction", fontsize=24)
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("Rad stress direction (degrees)")
                    plt.savefig(graph_directory + stationName + '_wave_radstress_dir.png')
                    plt.close()
                    
# Graph wave parameters on the same graph for comparison
#     swh graph
        if self.wavesExists:
            fig_swh, ax_swh = plt.subplots(figsize=(12, 8))
            for index in range(numberOfWaveDatapoints):
                if len(self.datapointsSWH[index]) > 0:
                    if(not np.isnan(np.min(self.datapointsSWH[index]))):
                        ax_swh.scatter(self.waveTimes, self.datapointsSWH[index], 
                                       marker=".", label=f"Forecast {self.buoyLabels[index]}")
                        if self.buoyExists:
                            ax_swh.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsSWH[index], 
                                           label=f"Buoy {self.buoyLabels[index]}")
    
            ax_swh.legend(loc="lower right", ncol=2, bbox_to_anchor=(1, 0))
            ax_swh.set_title("Significant Wave Height Across All Stations")
            ax_swh.format_xdata = mdates.DateFormatter('%d')
            ax_swh.set_ylabel("SWH (meters)")
            ax_swh.set_xlabel("Date")
            plt.tight_layout()
            plt.savefig(graph_directory + 'all_stations_wave_swh.png')
            plt.close(fig_swh)
        # MWP Graph
        if self.wavesExists:
            fig_mwp, ax_mwp = plt.subplots(figsize=(12, 8))
            for index in range(numberOfWaveDatapoints):
                if len(self.datapointsMWP[index]) > 0:
                    if(not np.isnan(np.min(self.datapointsMWP[index]))):
                        ax_mwp.scatter(self.waveTimes, self.datapointsMWP[index], 
                                       marker=".", label=f"Forecast {self.buoyLabels[index]}")
                        if self.buoyExists:
                            ax_mwp.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsMWP[index], 
                                           label=f"Buoy {self.buoyLabels[index]}")
    
            ax_mwp.legend(loc="lower right", ncol=2, bbox_to_anchor=(1, 0))
            ax_mwp.set_title("Mean Wave Period Across All Stations")
            ax_mwp.format_xdata = mdates.DateFormatter('%d')
            ax_mwp.set_ylabel("MWP (seconds)")
            ax_mwp.set_xlabel("Date")
            plt.tight_layout()
            plt.savefig(graph_directory + 'all_stations_wave_mwp.png')
            plt.close(fig_mwp)

        # PWP Graph
        if self.wavesExists:
            fig_pwp, ax_pwp = plt.subplots(figsize=(12, 8))
            for index in range(numberOfWaveDatapoints):
                if len(self.datapointsPWP[index]) > 0:
                    if(not np.isnan(np.min(self.datapointsPWP[index]))):
                        ax_pwp.scatter(self.waveTimes, self.datapointsPWP[index], 
                                       marker=".", label=f"Forecast {self.buoyLabels[index]}")
                        if self.buoyExists:
                            ax_pwp.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsPWP[index], 
                                           label=f"Buoy {self.buoyLabels[index]}")
    
            ax_pwp.legend(loc="lower right", ncol=2, bbox_to_anchor=(1, 0))
            ax_pwp.set_title("Peak Wave Period Across All Stations")
            ax_pwp.format_xdata = mdates.DateFormatter('%d')
            ax_pwp.set_ylabel("PWP (seconds)")
            ax_pwp.set_xlabel("Date")
            plt.tight_layout()
            plt.savefig(graph_directory + 'all_stations_wave_pwp.png')
            plt.close(fig_pwp)
            
#           Plot mwp and pwp together
        if self.wavesExists:
            fig, ax = plt.subplots(figsize=(12, 8))
    
            for index in range(numberOfWaveDatapoints):
                if len(self.datapointsMWP[index]) > 0 and not np.isnan(np.min(self.datapointsMWP[index])):
                    ax.scatter(self.waveTimes, self.datapointsMWP[index], 
                               marker=".", color='b', label=f"MWP Forecast {self.buoyLabels[index]}")
                    if self.buoyExists:
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsMWP[index], 
                                   marker="x", color='b', label=f"MWP Buoy {self.buoyLabels[index]}")

                if len(self.datapointsPWP[index]) > 0 and not np.isnan(np.min(self.datapointsPWP[index])):
                    ax.scatter(self.waveTimes, self.datapointsPWP[index], 
                               marker=".", color='r', label=f"PWP Forecast {self.buoyLabels[index]}")
                    if self.buoyExists:
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsPWP[index], 
                                   marker="x", color='r', label=f"PWP Buoy {self.buoyLabels[index]}")

            ax.legend(loc="upper left", ncol=2, bbox_to_anchor=(1, 1))
            ax.set_title("MWP and PWP Across All Stations")
            ax.format_xdata = mdates.DateFormatter('%d')
            ax.set_ylabel("Wave Period (seconds)")
            ax.set_xlabel("Date")
    
            fig.tight_layout()
            fig.savefig(graph_directory + 'all_stations_wave_mwp_pwp.png')
            plt.close(fig)
            
#         Graph water values on top of each other
        if len(self.datapointsWaters) > 0:
            fig, ax = plt.subplots(figsize=(16, 9))
    
            for index in range(numberOfWaterDatapoints):
                if(not np.isnan(np.min(self.datapointsWaters[index]))):
                    stationName = self.tideLabels[index]
                    # Plot forecast data for each station
                    ax.plot(self.waterTimes, self.datapointsWaters[index], label=f"Forecast {stationName}")
                    
                    if(self.stillwaterExists):
                        ax.plot(self.stillwaterTimes, self.datapointsStillwaters[index], label="Forecast")
                        
                    if(self.tidewaterExists):
                        ax.plot(self.tidewaterTimes, self.datapointsTidewaters[index], label="Forecast")
                    # Plot tide data if available
                    if self.tideExists:
                        ax.plot(self.tideDatapointsTimes[index], self.tideDatapointsWaters[index], label=f"Station {stationName}")
                    
                    # Note: Prediction data plotting is commented out in the original code, so it remains commented here:
                    # ax.plot(self.tideDatapointsPredictionTimes[index], self.tideDatapointsPredictionWaters[index], label=f"Prediction {stationName}")

            # Configure the plot
            ax.legend(loc="upper left", ncol=2, bbox_to_anchor=(1, 1))
            ax.format_xdata = mdates.DateFormatter('%d')
            plt.xticks(fontsize=12)
            plt.yticks(fontsize=12)
    
            # Since we're plotting multiple stations, we'll use a more general title
            plt.title(self.titlePrefix + "Water Elevation for All Stations", fontsize=18)
            plt.xlabel("Date", fontsize=14)
            plt.ylabel("Elevation (meters)", fontsize=14)
    
            plt.tight_layout()
            plt.savefig(graph_directory + 'all_stations_water.png')
            plt.close()

        
#         Graph values generated by GetRunup step
        if(len(self.datapointsRunup) > 0):
            for index in range(numberOfRunupDatapoints):
            
                fig, ax = plt.subplots(figsize=(16,9))
#                 ax.plot(self.runupTimes, self.datapointsRunup[index], label="runup")
                ax.plot(self.runupTimes, self.datapointsRunup[index], label="Stockdon Runup Distance")
#                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon No Setup 1.1(S/2)")
#                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")

                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxRunupDistance = str(round(max(self.datapointsRunup[index]), 2))
                plt.title(self.titlePrefix + stationName + " station runup distance max: " + maxRunupDistance, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("runup distance along shore (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_runup_distance.png')
                plt.close()
#             Iterate through runup times to graph a map of the waterline
            
                fig, ax = plt.subplots(figsize=(16,9))
#                 ax.plot(self.runupTimes, self.datapointsRunup[index], label="runup")
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanHigh[index], label="1.1(setup + S)")
                ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label="1.1([setup + storm surge] + S/2) + [SWL]")
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanHigh[index], label="Holman High Tide ξ")
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label="Holman Mid Tide ξ")
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label="Holman Low Tide ξ")
                ax.plot(self.runupTimes, self.datapointsRunupStockdon[index], label="1.1(<η> + S/2) + [SWL]")
#                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")


                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxRunup = str(round(max(self.datapointsRunupHolmanMid[index]), 2)) + ", " + str(round(max(self.datapointsRunupStockdon[index]), 2))
                plt.title(self.titlePrefix + stationName + " station runup (adcirc, stockdon): " + maxRunup, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("runup (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_runup.png')
                plt.close()
            
#               graph deepwater wave height
                fig, ax = plt.subplots(figsize=(16,9))

                ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label="Deepwater SWH")
#                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")


                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxSwh = str(round(max(self.datapointsRunupHolmanLow[index]), 2))
                plt.title(self.titlePrefix + stationName + " station deepwater SWH max: " + maxSwh, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("Deepwater SWH (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_deepwater_swh.png')
                plt.close()
                
#                 Graph setup
                fig, ax = plt.subplots(figsize=(16,9))
#                 ax.plot(self.runupTimes, self.datapointsRunup[index], label="runup")
#                 ax.plot(self.runupTimes, self.datapointsSetupHolmanHigh[index], label="Holman High Tide ξ")
#                 ax.plot(self.runupTimes, self.datapointsSetupHolmanMid[index], label="Holman Mid Tide ξ")
#                 ax.plot(self.runupTimes, self.datapointsSetupHolmanLow[index], label="Holman Low Tide ξ")
                ax.plot(self.runupTimes, self.datapointsSetupStockdon[index], label=r"Stockdon $\langle\eta\rangle$")
#                 ax.plot(self.runupTimes, self.datapointsSetupAdcirc[index], label="ADCIRC+SWAN setup+storm surge")
                ax.plot(self.runupTimes, self.datapointsSetupStockdonLow[index], label=r"SWAN $\eta_{setup}$")
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanHigh[index], label="ADCIRC+SWAN storm surge")


                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                stationName = self.runupLabels[index]
                maxSetup = str(round(max(self.datapointsSetupStockdonLow[index]), 2)) + ", " + str(round(max(self.datapointsSetupStockdon[index]), 2))
                plt.title(self.titlePrefix + stationName + " station setup max (SWAN, Stockdon): " + maxSetup, fontsize=24)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("setup (meters)")
                plt.savefig(graph_directory + stationName + '_setup.png')
                plt.close()
                
#                 Graph swash
                fig, ax = plt.subplots(figsize=(16,9))
#                 ax.plot(self.runupTimes, self.datapointsRunup[index], label="runup")
#                 ax.plot(self.runupTimes, self.datapointsSwashHolmanHigh[index], label="Holman High Tide ξ")
#                 ax.plot(self.runupTimes, self.datapointsSwashHolmanMid[index], label="Holman Mid Tide ξ")
#                 ax.plot(self.runupTimes, self.datapointsSwashHolmanLow[index], label="Holman Low Tide ξ")
                ax.plot(self.runupTimes, self.datapointsSwashStockdonIncident[index], label="Stockdon Incident βf√(HₒLₒ)")
                ax.plot(self.runupTimes, self.datapointsSwashStockdonInfragravity[index], label="Stockdon Infragravity √(HₒLₒ)")
#                 ax.plot(self.runupTimes, self.datapointsSwashStockdonLow[index], label="Stockdon Low")
                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxSwash = str(round(max(self.datapointsSwashStockdonIncident[index]), 2)) + ", " + str(round(max(self.datapointsSwashStockdonInfragravity[index]), 2))
                plt.title(self.titlePrefix + stationName + " station swash max (inc, ig): " + maxSwash, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("swash (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_swash.png')
                plt.close()
                        
#                 Graph incident swash
                fig, ax = plt.subplots(figsize=(16,9))
#                 ax.plot(self.runupTimes, self.datapointsRunup[index], label="runup")
                ax.plot(self.runupTimes, self.datapointsSwashHolmanIncident[index], label="Holman Incident ξ")
                ax.plot(self.runupTimes, self.datapointsSwashStockdonIncident[index], label="Stockdon Incident βf√(HₒLₒ)")
#                 ax.plot(self.runupTimes, self.datapointsSwashStockdonLow[index], label="Stockdon Low")
                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxIncidentSwash = str(round(max(self.datapointsSwashStockdonIncident[index]), 2))
                plt.title(self.titlePrefix + stationName + " station incident (<3min) swash max: " + maxIncidentSwash, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("swash (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_incident_swash.png')
                plt.close()
                
#                 Graph infragravity swash
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.runupTimes, self.datapointsSwashHolmanInfragravity[index], label="Holman Infragravity ξ")
                ax.plot(self.runupTimes, self.datapointsSwashStockdonInfragravity[index], label="Stockdon Infragravity √(HₒLₒ)")
#                 ax.plot(self.runupTimes, self.datapointsSwashStockdonLow[index], label="Stockdon Low")
                ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxInfragravitySwash = str(round(max(self.datapointsSwashStockdonInfragravity[index]), 2))
                plt.title(self.titlePrefix + stationName + " station infragravity (>3 min) swash max: " + maxInfragravitySwash, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("swash (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_infragravity_swash.png')
                plt.close()
            
                
#                 Graph water_swash
                if(len(self.datapointsWaters) > 0 and False):
                    # Assuming self.findMatchingIndices is defined as per your earlier request
                    datapointsWaterRunupIndices = self.findMatchingIndices(self.tideLabels, self.runupLabels[index][0:9])
#                     print("Finding Water stations corresponding to runup station")
#                     print("Searching for water labels with string: ", self.runupLabels[index][0:9])
#                     print("Found indices count:", len(datapointsWaterRunupIndices))
                    for datapointsWaterRunupIndex in datapointsWaterRunupIndices:
                        
                        fig, ax = plt.subplots(figsize=(16, 9))
                        
                        # Plot the water elevation time series
                        ax.plot(self.waterTimes, self.datapointsWaters[datapointsWaterRunupIndex], label=r"$\eta$", color='blue', linewidth=2)
                        
                        # Calculate total swash: sqrt(S_incident^2 + S_infragravity^2)
                        total_swash = np.sqrt(np.array(self.datapointsSwashStockdonIncident[index])**2 + 
                                              np.array(self.datapointsSwashStockdonInfragravity[index])**2)
                        
                        # Define the upper and lower bounds for the swash area
                        lower_bound = self.datapointsWaters[datapointsWaterRunupIndex] - 0.5 * total_swash
                        upper_bound = self.datapointsWaters[datapointsWaterRunupIndex] + 0.5 * total_swash
                        
                        # Fill the area between upper and lower bounds to highlight swash extent
                        ax.fill_between(self.waterTimes, lower_bound, upper_bound, color='lightblue', alpha=0.4, label="Swash Extent")
                        
                        # Add dotted lines for maximum and minimum extents
                        ax.plot(self.waterTimes, upper_bound, '--', color='red', label="+S/2", linewidth=1.5)
                        ax.plot(self.waterTimes, lower_bound, '--', color='green', label="-S/2", linewidth=1.5)
                        
                        # Calculate the maximum elevation including the swash
                        max_water_elevation = max(self.datapointsWaters[datapointsWaterRunupIndex])
                        max_swash_upper = max(upper_bound)
                        maxElevation = str(round(max_water_elevation, 2)) + ", " + str(round(max_swash_upper, 2))
                        
                        # Customize the plot
                        ax.legend(loc="upper left")
                        ax.format_xdata = mdates.DateFormatter('%d')
                        stationName = self.tideLabels[datapointsWaterRunupIndex]
#                         print("stationName of corresponding water station: ", stationName)
                        plt.title(self.titlePrefix + stationName + " station elevation max (water, swash): " + maxElevation, fontsize=18)
                        plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT))
                        plt.ylabel("elevation (meters)")
                        
                        # Adjust layout to prevent label cutoff
                        plt.tight_layout()
                        
                        # Save and close the plot
                        plt.savefig(graph_directory + stationName + '_water_swash.png', dpi=300)
                        plt.close()
           
       
#               Graph wavelength
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.runupTimes, self.datapointsWavelength[index])
#                 ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxWavelength = str(round(max(self.datapointsWavelength[index]), 2))
                plt.title(self.titlePrefix + stationName + " station wavelength max: " + maxWavelength, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("wavelength (meters)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_wavelength.png')
                plt.close()
                
#                 Graph steepness
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.runupTimes, self.datapointsSteepness[index])
#                 ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxSteepness = str(round(max(self.datapointsSteepness[index]), 2))
                plt.title(self.titlePrefix + stationName + " station steepness max: " + maxSteepness, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("wave steepness (H₀/L₀)", fontsize=14)
                plt.savefig(graph_directory + stationName + '_steepness.png')
                plt.close()
                
#                 Graph iribarren number
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.runupTimes, self.datapointsIribarren[index])
                ax.set_ylim([0, 2])
#                 ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxIribarren = str(round(max(self.datapointsIribarren[index]), 2))
                plt.title(self.titlePrefix + stationName + " station iribarren max: " + maxIribarren, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("iribarren number", fontsize=14)
                plt.savefig(graph_directory + stationName + '_iribarren.png')
                plt.close()
                
#                 Graph average slope
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.runupTimes, self.runupAverageSlopes[index])
                ax.set_ylim([0, 0.1])
#                 ax.legend(loc="upper left")
                ax.format_xdata = mdates.DateFormatter('%d')
                plt.xticks(fontsize=12)
                plt.yticks(fontsize=12)
                stationName = self.runupLabels[index]
                maxAverageSlope = str(round(max(self.runupAverageSlopes[index]), 2))
                plt.title(self.titlePrefix + stationName + " station average slope (waterline to surf) max: " + maxAverageSlope, fontsize=18)
#                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
                plt.ylabel("average slope", fontsize=14)
                plt.savefig(graph_directory + stationName + '_slope.png')
                plt.close()


            # Combined runup plots for all transects
            fig, axes = plt.subplots(5, 1, figsize=(16, 20), sharex=True)
            for transect in range(1, 6):
                ax = axes[transect - 1]
                dune_heights = []
                unique_heights = set()
    
                for index in range(numberOfRunupDatapoints):
                    stationName = self.runupLabels[index]
                    if str(transect) in stationName[0:stationName.index(" ")]:
    #                     print("found label", self.runupLabels[index])
    #                     print("runup values", self.datapointsRunupHolmanMid[index])
                        dune_heights = self.datapointsDuneHeights[index]
                        ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label=stationName)
                        unique_heights.update(dune_heights)
    
                # Plot horizontal lines for each unique dune height
                for height in unique_heights:
                    if(transect >= 5):
                        ax.axhline(y=height, linestyle='--', color='red', label=f'Runup Height {height:.2f}m' if height == list(unique_heights)[0] else None)
                    else:
                        ax.axhline(y=height, linestyle='--', color='grey', label=f'Runup Height {height:.2f}m' if height == list(unique_heights)[0] else None)
    
                ax.legend(loc="upper left", fontsize=10)
                ax.format_xdata = mdates.DateFormatter('%d')
                ax.tick_params(axis='both', labelsize=12)
                ax.set_ylabel("Runup (meters)", fontsize=12)
                ax.set_title(f"{self.titlePrefix}Napatree{transect} Runup", fontsize=14)
    
            plt.tight_layout()
            plt.savefig(graph_directory + 'Napatree_all_runup.png')
            plt.close()
    
            # Combined deepwater significant wave height (H_0) plots for all transects
            fig, axes = plt.subplots(5, 1, figsize=(16, 20), sharex=True)
            for transect in range(1, 6):
                ax = axes[transect - 1]
    
                for index in range(numberOfRunupDatapoints):
                    stationName = self.runupLabels[index]
                    if str(transect) in stationName[0:stationName.index(" ")]:
                        ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label=stationName)
    
                ax.legend(loc="upper left", fontsize=10)
                ax.format_xdata = mdates.DateFormatter('%d')
                ax.tick_params(axis='both', labelsize=12)
                ax.set_ylabel(r"$H_0$ (meters)", fontsize=12)
                ax.set_title(f"{self.titlePrefix}Napatree{transect} Deepwater SWH", fontsize=14)
    
    #         plt.xlabel("Day", fontsize=14)
            plt.tight_layout()
            plt.savefig(graph_directory + 'Napatree_all_deepwater_swh.png')
            plt.close()
    
            # Combined significant wave height (H_s) plots for all transects
            fig, axes = plt.subplots(5, 1, figsize=(16, 20), sharex=True)
            for transect in range(1, 6):
                ax = axes[transect - 1]
    
                for index in range(numberOfRunupDatapoints):
                    stationName = self.runupLabels[index]
                    if str(transect) in stationName[0:stationName.index(" ")]:  # Match deepline indices
                        swhIndex = self.buoyLabels.index(stationName)
                        ax.plot(self.runupTimes, self.datapointsSWH[swhIndex], label=stationName)
    
                ax.legend(loc="upper left", fontsize=10)
                ax.format_xdata = mdates.DateFormatter('%d')
                ax.tick_params(axis='both', labelsize=12)
                ax.set_ylabel(r"$H_s$ (meters)", fontsize=12)
                ax.set_title(f"{self.titlePrefix}Napatree{transect} SWH", fontsize=14)
    
    #         plt.xlabel("Day", fontsize=14)
            plt.tight_layout()
            plt.savefig(graph_directory + 'Napatree_all_swh.png')
            plt.close()
    
            # Combined elevation, max SWH, and max deepwater SWH plots for all transects
            fig, axes = plt.subplots(5, 1, figsize=(16, 20), sharex=True)
            for transect in range(1, 6):
                ax = axes[transect - 1]
                ax2 = ax.twinx()  # Second y-axis for elevation
    
                deeplineDistances = []
                deeplineElevations = []
                deeplineSWH = []
                deeplineDeepwaterSWH = []
    
                for index in range(numberOfRunupDatapoints):
                    stationName = self.runupLabels[index]
                    if str(transect) in stationName[0:stationName.index(" ")]:
                        swhIndex = self.buoyLabels.index(stationName)
                        deeplineSWH.append(np.max(self.datapointsSWH[swhIndex]))
                        elevationIndex = self.assetLabels.index(stationName)
                        deeplineElevations.append(self.datapointsElevation[elevationIndex])
                        deeplineDeepwaterSWH.append(np.max(self.datapointsRunupHolmanLow[index]))
                        # Extract distance from name (e.g., "1000m" -> 1000)
                        distance_str = stationName[stationName.rindex(" ") + 1:-1]
                        deeplineDistances.append(int(distance_str))
    
                # Plot SWH and deepwater SWH on primary y-axis
                ax.plot(deeplineDistances, deeplineSWH, label="Max SWH", color='blue')
                ax.plot(deeplineDistances, deeplineDeepwaterSWH, label="Max Deepwater SWH", color='green')
                # Plot elevation on secondary y-axis
                ax2.plot(deeplineDistances, deeplineElevations, label="Elevation", color='red', linestyle="--")
    
                # Customize axes
                ax.set_ylabel("SWH (meters)", fontsize=12, color='blue')
                ax2.set_ylabel("Elevation (meters)", fontsize=12, color='red')
                ax.tick_params(axis='y', labelcolor='blue', labelsize=12)
                ax2.tick_params(axis='y', labelcolor='red', labelsize=12)
                ax.tick_params(axis='x', labelsize=12)
                ax.set_title(f"{self.titlePrefix}Napatree{transect} Deepline Metrics", fontsize=14)
    
                # Combine legends
                if(transect == 1):
                    lines1, labels1 = ax.get_legend_handles_labels()
                    lines2, labels2 = ax2.get_legend_handles_labels()
                    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=10)
    
            plt.xlabel("Distance (meters)", fontsize=14)
            plt.tight_layout()
            plt.savefig(graph_directory + 'Napatree_all_deepline_metrics.png')
            plt.close()

# 
# # Graph all runup
# 
#         fig, ax = plt.subplots(figsize=(16,9))
#         duneHeights = []
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("1" in stationName[0:stationName.index(" ")]):
#                 duneHeights = self.datapointsDuneHeights[index]
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label=stationName)
# 
#         ax.plot(self.runupTimes, duneHeights, label="Dune Height")
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree1 all runup: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Runup (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree1_all_runup.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         duneHeights = []
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("2" in stationName[0:stationName.index(" ")]):
#                 duneHeights = self.datapointsDuneHeights[index]
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label=stationName)
# 
#         ax.plot(self.runupTimes, duneHeights, label="Dune Height")
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree2 all runup: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Runup (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree2_all_runup.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         duneHeights = []
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("3" in stationName[0:stationName.index(" ")]):
#                 duneHeights = self.datapointsDuneHeights[index]
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label=stationName)
# 
#         ax.plot(self.runupTimes, duneHeights, label="Dune Height")
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree3 all runup: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Runup (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree3_all_runup.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         duneHeights = []
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("4" in stationName[0:stationName.index(" ")]):
#                 duneHeights = self.datapointsDuneHeights[index]
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label=stationName)
# 
#         ax.plot(self.runupTimes, duneHeights, label="Dune Height")
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree4 all runup: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Runup (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree4_all_runup.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         duneHeights = []
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("5" in stationName[0:stationName.index(" ")]):
#                 duneHeights = self.datapointsDuneHeights[index]
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanMid[index], label=stationName)
# 
#         ax.plot(self.runupTimes, duneHeights, label="Dune Height")
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree5 all runup: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Runup (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree5_all_runup.png')
#         plt.close()
# #         
#     #               graph all deepwater swh                
#         fig, ax = plt.subplots(figsize=(16,9))
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("1" in stationName[0:stationName.index(" ")]):
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label=stationName)
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")
# 
# 
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + r"Napatree1 deepwater significant wave height $H_0$: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel(r"$H_0$ (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree1_all_deepwater_swh.png')
#         plt.close()
# # #         
#         fig, ax = plt.subplots(figsize=(16,9))
#         for index in range(numberOfRunupDatapoints):
#             stationName = self.runupLabels[index]
#             if("1" in stationName[0:stationName.index(" ")] and stationName[-1] == "m"):
#                 swhIndex = self.buoyLabels.index(stationName)
#                 ax.plot(self.runupTimes, self.datapointsSWH[swhIndex], label=stationName)
#                 
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + r"Napatree1 significant wave height $H_s$: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel(r"$H_s$ (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree1_all_swh.png')
#         plt.close()
# 
#         deeplineDistances = []
#         deeplineElevations = []
#         deeplineSWH = []
#         deeplineDeepwaterSWH = []
#         for index in range(numberOfRunupDatapoints):
#             stationName = self.runupLabels[index]
#             if("1" in stationName[0:stationName.index(" ")]):
#                 swhIndex = self.buoyLabels.index(stationName)
#                 deeplineSWH.append(np.max(self.datapointsSWH[swhIndex]))
#                 elevationIndex = self.assetLabels.index(stationName)
#                 deeplineElevations.append(self.datapointsElevation[elevationIndex])
#                 deeplineDeepwaterSWH.append(np.max(self.datapointsRunupHolmanLow[index]))
#                 
#                 deeplineDistances.append(int(stationName[stationName.rindex(" ") + 1:len(stationName) - 1]))
# 
# 
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree1 Deepline Elevation: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineElevations)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Elevation (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree1_all_elevations.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree1 Deepline SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree1_max_swh.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree1 Deepline Deepwater SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineDeepwaterSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree1_max_deepwater_swh.png')
#         plt.close()

                
    #               graph all deepwater swh                
#         fig, ax = plt.subplots(figsize=(16,9))
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("2" in stationName[0:stationName.index(" ")]):
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label=stationName)
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")
# 
# 
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree2 all deepwater SWH: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree2_all_deepwater_swh.png')
#         plt.close()
        
#         deeplineDistances = []
#         deeplineElevations = []
#         deeplineSWH = []
#         deeplineDeepwaterSWH = []
#         for index in range(numberOfRunupDatapoints):
#             stationName = self.runupLabels[index]
#             if("2" in stationName[0:stationName.index(" ")] and stationName[-1] == "m"):
#                 swhIndex = self.buoyLabels.index(stationName)
#                 deeplineSWH.append(np.max(self.datapointsSWH[swhIndex]))
#                 elevationIndex = self.assetLabels.index(stationName)
#                 deeplineElevations.append(self.datapointsElevation[elevationIndex])
#                 deeplineDeepwaterSWH.append(np.max(self.datapointsRunupHolmanLow[index]))
#                 
#                 deeplineDistances.append(int(stationName[stationName.rindex(" ") + 1:len(stationName) - 1]))
# 
# 
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree2 Deepline Elevation: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineElevations)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Elevation (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree2_all_elevations.png')
#         plt.close()
        
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree2 Deepline SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree2_max_swh.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree2 Deepline Deepwater SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineDeepwaterSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree2_max_deepwater_swh.png')
#         plt.close()

    #               graph all deepwater swh                
#         fig, ax = plt.subplots(figsize=(16,9))
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("3" in stationName[0:stationName.index(" ")]):
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label=stationName)
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")
# 
# 
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree3 all deepwater SWH: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree3_all_deepwater_swh.png')
#         plt.close()
#         
#         deeplineDistances = []
#         deeplineElevations = []
#         deeplineSWH = []
#         deeplineDeepwaterSWH = []
#         for index in range(numberOfRunupDatapoints):
#             stationName = self.runupLabels[index]
#             if("3" in stationName[0:stationName.index(" ")] and stationName[-1] == "m"):
#                 swhIndex = self.buoyLabels.index(stationName)
#                 deeplineSWH.append(np.max(self.datapointsSWH[swhIndex]))
#                 elevationIndex = self.assetLabels.index(stationName)
#                 deeplineElevations.append(self.datapointsElevation[elevationIndex])
#                 deeplineDeepwaterSWH.append(np.max(self.datapointsRunupHolmanLow[index]))
#                 
#                 deeplineDistances.append(int(stationName[stationName.rindex(" ") + 1:len(stationName) - 1]))
# 
# 
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree3 Deepline Elevation: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineElevations)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Elevation (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree3_all_elevations.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree3 Deepline SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree3_max_swh.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree3 Deepline Deepwater SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineDeepwaterSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree3_max_deepwater_swh.png')
#         plt.close()


    #               graph all deepwater swh                
#         fig, ax = plt.subplots(figsize=(16,9))
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("4" in stationName[0:stationName.index(" ")]):
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label=stationName)
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")
# 
# 
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree4 all deepwater SWH: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree4_all_deepwater_swh.png')
#         plt.close()
        
#         deeplineDistances = []
#         deeplineElevations = []
#         deeplineSWH = []
#         deeplineDeepwaterSWH = []
#         for index in range(numberOfRunupDatapoints):
#             stationName = self.runupLabels[index]
#             if("4" in stationName[0:stationName.index(" ")] and stationName[-1] == "m"):
#                 swhIndex = self.buoyLabels.index(stationName)
#                 deeplineSWH.append(np.max(self.datapointsSWH[swhIndex]))
#                 elevationIndex = self.assetLabels.index(stationName)
#                 deeplineElevations.append(self.datapointsElevation[elevationIndex])
#                 deeplineDeepwaterSWH.append(np.max(self.datapointsRunupHolmanLow[index]))
#                 
#                 deeplineDistances.append(int(stationName[stationName.rindex(" ") + 1:len(stationName) - 1]))
# 
# 
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree4 Deepline Elevation: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineElevations)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Elevation (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree4_all_elevations.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree4 Deepline SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree4_max_swh.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree4 Deepline Deepwater SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineDeepwaterSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree4_max_deepwater_swh.png')
#         plt.close()

    #               graph all deepwater swh                
#         fig, ax = plt.subplots(figsize=(16,9))
#         for index in range(numberOfRunupDatapoints):
# 
#             stationName = self.runupLabels[index]
#             if("5" in stationName[0:stationName.index(" ")]):
#                 ax.plot(self.runupTimes, self.datapointsRunupHolmanLow[index], label=stationName)
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonNoSetup[index], label="Stockdon Swash (S/2)")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupStockdonLow[index], label="Stockdon Low")
#         #                 ax.plot(self.runupTimes, self.datapointsRunupAdcirc[index], label="[SWL + setup] + 1.1(S/2)")
# 
# 
#         ax.legend(loc="upper left")
#         ax.format_xdata = mdates.DateFormatter('%d')
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree5 all deepwater SWH: ", fontsize=18)
# #                 plt.xlabel("Start: " + self.waterStartDate.strftime(self.DATE_FORMAT), fontsize=14)
#         plt.tight_layout()
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.savefig(graph_directory + 'Napatree5_all_deepwater_swh.png')
#         plt.close()
#         
#         deeplineDistances = []
#         deeplineElevations = []
#         deeplineSWH = []
#         deeplineDeepwaterSWH = []
#         for index in range(numberOfRunupDatapoints):
#             stationName = self.runupLabels[index]
#             if("5" in stationName[0:stationName.index(" ")] and stationName[-1] == "m"):
#                 swhIndex = self.buoyLabels.index(stationName)
#                 deeplineSWH.append(np.max(self.datapointsSWH[swhIndex]))
#                 elevationIndex = self.assetLabels.index(stationName)
#                 deeplineElevations.append(self.datapointsElevation[elevationIndex])
#                 deeplineDeepwaterSWH.append(np.max(self.datapointsRunupHolmanLow[index]))
#                 
#                 deeplineDistances.append(int(stationName[stationName.rindex(" ") + 1:len(stationName) - 1]))
# 
# 
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree5 Deepline Elevation: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineElevations)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Elevation (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree5_all_elevations.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree5 Deepline SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree5_max_swh.png')
#         plt.close()
#         
#         fig, ax = plt.subplots(figsize=(16,9))
#         plt.xticks(fontsize=12)
#         plt.yticks(fontsize=12)
#         plt.title(self.titlePrefix + "Napatree5 Deepline Deepwater SWH: ", fontsize=18)
#         ax.plot(deeplineDistances, deeplineDeepwaterSWH)
#         plt.xlabel("Distance", fontsize=14)
#         plt.ylabel("Deepwater SWH (meters)", fontsize=14)
#         plt.tight_layout()
#         plt.savefig(graph_directory + 'Napatree5_max_deepwater_swh.png')
#         plt.close()

                
#         if(len(self.runupTimes) > 0):
#             vmin = -1
#             vminSwath = 0
# #             vmax = math.ceil(self.maxWater)
#             vmax = 5
# #             vmax = 20
#             levels = 100
#             levelBoundaries = np.linspace(vmin, vmax, levels + 1)
#             levelBoundariesSwath = np.linspace(vminSwath, vmax, levels + 1)
# #             waterTriangulation = Triangulation(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes, triangles=self.mapWaterTriangles, mask=self.mapWaterMaskedTriangles)
#             for index in range(len(self.runupTimes)):
#                 fig, ax = plt.subplots(figsize=(9,9))
#     #             print(self.endWavePointsLongitudes)
#     #             print(self.endWavePointsLatitudes)
#     #             print(self.endSWH)
#                 plt.imshow(img, extent=self.backgroundAxis, alpha=0.6, aspect=aspectRatio, zorder=2)
#                 
# #                 Plot points
#                 if(self.meshExists):
#                     ax.scatter(self.assetLongitudes, self.assetLatitudes, label="Assets", zorder=3, alpha=0.7, marker=".", s=40, color="black")
#                     
#                 if(self.runupExists):
#                     for runupIndex, runupLabel in enumerate(self.runupLabels):
#                         self.plotExtendedLines(ax, runupIndex, index, runupLabel)
# #                         print("datapointsWaterlineLongitudes", type(self.datapointsWaterlineLongitudes[runupIndex][index]))
# #                         ax.plot(self.datapointsWaterlineLongitudes[runupIndex][index], self.datapointsWaterlineLatitudes[runupIndex][index], label=runupLabel, zorder=3, alpha=0.7, marker=".", color="green")
# #                         ax.plot(self.datapointsRunupLongitudes[runupIndex][index], self.datapointsRunupLatitudes[runupIndex][index], label=runupLabel, zorder=3, alpha=0.7, marker=".", color="red")
# 
# #               Todo: Fix triangulation errors
# #                 contourset = ax.tripcolor(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes, self.mapWaters[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
#                 plt.axis(plotAxis)
#                 plt.title(self.titlePrefix + "Runup Waterline")
#                 plt.xlabel(self.runupTimes[index])
#     #             plt.gca().invert_yaxis()
# 
#                 plt.savefig(graph_directory + 'map_runup_' + str(index) + '.png')
#                 plt.close()
#                 gc.collect()
#             with imageio.get_writer(graph_directory + 'runup.gif', mode='I') as writer:
#                 for index in range(len(self.runupTimes)):
#                     filename = "map_runup_" + str(index) + ".png"
#                     image = imageio.imread(graph_directory + filename)
#                     writer.append_data(image)
#                 for index in range(len(self.runupTimes)):
#                     filename = "map_runup_" + str(index) + ".png"
#                     os.remove(graph_directory + filename)
#             plt.close()
#             gc.collect()

