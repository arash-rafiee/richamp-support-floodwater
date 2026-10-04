#!/usr/bin/env python3
"""
Download a high-resolution grayscale street basemap for one --backgroundChoice
extent, for the --maps water video.

The image comes from the Esri World Street Map export service in one request
(up to 4096 px a side). It is fetched in Web Mercator so text and shapes are
not stretched, then its rows are resampled to equal steps of latitude so it
lines up with the lon/lat extent the video draws in, and it is converted to
grayscale. The result is saved next to the color background as
<background>StreetGray.png (e.g. RhodeIslandChampStreetGray.png); Grapher uses
that file when it exists. Commit the PNG so the cluster needs no internet.

Attribution shown in the video: "Basemap: Esri World Street Map, grayscale
(Esri, HERE, Garmin, OpenStreetMap contributors, GIS user community)".
Check that your use fits Esri's terms for its basemap services.

With --borders it instead writes StateBorders.json: the land and water
borders shared by two states (Census TIGERweb state polygons), which the video
draws as dashed lines because the basemap's own state lines are too faint at
video size. Coastlines are left out; the video draws its own from the mesh.

Examples (on a machine with internet):
    python tools/fetch_street_basemap.py RHODE_ISLAND_CHAMP
    python tools/fetch_street_basemap.py BLOCK_ISLAND_SOUND --height 4096
    python tools/fetch_street_basemap.py --borders

Requires: numpy Pillow (standard library for the download)
"""
import argparse
import io
import math
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

SERVICE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/export"
STATES_SERVICE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0/query"
EARTH_RADIUS = 6378137.0
MAX_SIDE = 4096


def mercatorX(lon):
    return EARTH_RADIUS * math.radians(lon)


def mercatorY(lat):
    return EARTH_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def download(url):
    """Bytes at url; some networks reset Python's TLS handshake with these servers, so fall back to curl."""
    try:
        with urllib.request.urlopen(url, timeout=300) as response:
            return response.read()
    except OSError as error:
        print("Python download failed (" + str(error) + "); trying curl", flush=True)
        return subprocess.run(["curl", "-sSf", "-m", "300", url], check=True, capture_output=True).stdout


def writeStateBorders(output, box):
    """Save the borders shared by two states inside box [west, south, east, north] as lon/lat polylines."""
    import json
    from collections import defaultdict
    query = urllib.parse.urlencode({
        "geometry": ",".join(str(value) for value in box), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "STUSAB", "outSR": 4326,
        "returnGeometry": "true", "f": "geojson",
    })
    features = json.loads(download(STATES_SERVICE + "?" + query))["features"]
    # The state polygons share vertices along common borders, so an edge that
    # belongs to two states is a border and one that belongs to one is coast.
    point = lambda coordinate: (round(coordinate[0], 6), round(coordinate[1], 6))
    owners = defaultdict(set)
    for feature in features:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            for ring in polygon:
                for start, end in zip(ring, ring[1:]):
                    if point(start) != point(end):
                        owners[frozenset((point(start), point(end)))].add(feature["properties"]["STUSAB"])
    neighbors = defaultdict(list)
    for edge, states in owners.items():
        if len(states) > 1:
            start, end = tuple(edge)
            neighbors[start].append(end)
            neighbors[end].append(start)
    # Chain the border edges into polylines, starting from line ends first
    used = set()
    lines = []
    for start in sorted(neighbors, key=lambda vertex: len(neighbors[vertex]) == 2):
        for nextVertex in neighbors[start]:
            if frozenset((start, nextVertex)) in used:
                continue
            line = [start]
            current = start
            while True:
                used.add(frozenset((current, nextVertex)))
                line.append(nextVertex)
                current = nextVertex
                options = [vertex for vertex in neighbors[current] if frozenset((current, vertex)) not in used]
                if len(neighbors[current]) != 2 or not options:
                    break
                nextVertex = options[0]
            lines.append([list(vertex) for vertex in line])
    with open(output, "w") as file:
        json.dump({"source": "US Census TIGERweb state boundaries", "box": list(box), "lines": lines}, file)
    print("Wrote", output, len(lines), "border lines,", sum(len(line) for line in lines), "points", flush=True)


def readBackgrounds(generateGraphsFile):
    """{"NAME": (map file, [west, east, north, south])} from the *_MAP / *_AXIS constants."""
    source = open(generateGraphsFile, encoding="utf-8").read()
    maps = dict(re.findall(r'^(\w+)_MAP = "([^"]+)"', source, re.M))
    axes = {name: [float(value) for value in values.split(",")]
            for name, values in re.findall(r"^(\w+)_AXIS = \[([^\]]+)\]", source, re.M)}
    return {name: (maps[name], axes[name]) for name in axes if name in maps}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("background", nargs="?", help="--backgroundChoice name, e.g. RHODE_ISLAND_CHAMP")
    parser.add_argument("--borders", action="store_true", help="write StateBorders.json instead of a basemap")
    parser.add_argument("--box", type=float, nargs=4, default=[-76.0, 38.5, -69.0, 43.0],
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="area for --borders")
    parser.add_argument("--height", type=int, default=4096, help="image height in pixels (max 4096)")
    parser.add_argument("--dpi", type=int, default=192,
                        help="map dpi; higher draws larger labels and roads for the same area (96 = normal)")
    args = parser.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.borders:
        writeStateBorders(os.path.join(repo, "StateBorders.json"), args.box)
        return
    if not args.background:
        parser.error("give a background name, or --borders")
    backgrounds = readBackgrounds(os.path.join(repo, "generateGraphs.py"))
    if args.background not in backgrounds:
        sys.exit("Unknown background " + args.background + "; choose from " + ", ".join(sorted(backgrounds)))
    mapFile, (west, east, north, south) = backgrounds[args.background]

    # Web Mercator box and an image of the same shape, so pixels are square
    x0, x1, y0, y1 = mercatorX(west), mercatorX(east), mercatorY(south), mercatorY(north)
    height = min(args.height, MAX_SIDE)
    width = int(round(height * (x1 - x0) / (y1 - y0)))
    if width > MAX_SIDE:
        width, height = MAX_SIDE, int(round(MAX_SIDE * (y1 - y0) / (x1 - x0)))
    query = urllib.parse.urlencode({
        "bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": 3857, "imageSR": 3857,
        "size": f"{width},{height}", "dpi": args.dpi, "format": "png24", "transparent": "false", "f": "image",
    })
    print("Downloading", width, "x", height, "px for", args.background, flush=True)
    data = download(SERVICE + "?" + query)
    try:
        image = np.asarray(Image.open(io.BytesIO(data)).convert("L"), dtype=np.float32)
    except Exception:
        sys.exit("The service did not return an image: " + data[:300].decode("utf-8", "replace"))

    # Row r of the output sits at an even step of latitude; find the Mercator
    # row it falls on and interpolate between the two nearest source rows.
    rows = image.shape[0]
    latitudes = north - (np.arange(rows) + 0.5) * (north - south) / rows
    sourceRows = (y1 - np.array([mercatorY(lat) for lat in latitudes])) / (y1 - y0) * rows - 0.5
    sourceRows = np.clip(sourceRows, 0, rows - 1)
    below = np.floor(sourceRows).astype(int)
    above = np.minimum(below + 1, rows - 1)
    weight = (sourceRows - below)[:, None]
    resampled = image[below] * (1 - weight) + image[above] * weight

    output = os.path.join(repo, os.path.splitext(mapFile)[0] + "StreetGray.png")
    Image.fromarray(np.clip(resampled + 0.5, 0, 255).astype(np.uint8)).save(output, optimize=True)
    print("Wrote", output, os.path.getsize(output) // 1024, "KB", flush=True)


if __name__ == "__main__":
    main()
