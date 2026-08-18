"""
Export the per-station time series that Grapher plots into CSV files.

The heavy ADCIRC/SWAN netcdf files (fort.63.nc and friends) can be tens of GB,
but the station time series extracted from them are tiny. generateGraphs.py
already writes those series to <tempDir>/adcirc_*_data_file.json on its way to
the PNGs, so this script just reshapes that JSON into CSV -- no netcdf reading,
no re-interpolation, stdlib only. Run it on the cluster and copy the CSVs off
instead of the netcdf.

Example:
    python stations_to_csv.py \
      --tempDir /scratch4/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
      --stations OBS_STATIONS.json \
      --out station_csv/
"""

import argparse
import csv
import json
import math
import os
from datetime import datetime, timezone

# This script lives in tools/, so the default station metadata is resolved
# against the repository root rather than the current working directory.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STATIONS_FILE = os.path.join(REPO_ROOT, "OBS_STATIONS.json")

# JSON file written by Reader.py -> column name in the CSV. The dict key inside
# each station entry is always "water" regardless of which run produced it.
SERIES = [
    ("adcirc_water_data_file.json", "water", "eta_m"),
    ("adcirc_stillwater_data_file.json", "water", "eta_still_m"),
    ("adcirc_tidewater_data_file.json", "water", "eta_tide_m"),
    ("obs_water_data_file.json", "water", "obs_m"),
]


def loadSeries(temp_directory):
    """Read whichever of the SERIES files exist, keyed station -> column -> (times, values)."""
    stations = {}
    for fileName, dataKey, columnName in SERIES:
        filePath = os.path.join(temp_directory, fileName)
        if not os.path.exists(filePath):
            print("skipping missing", filePath, flush=True)
            continue
        print("reading", filePath, flush=True)
        with open(filePath) as datafile:
            dataset = json.load(datafile)
        for stationKey in dataset.keys():
            # map_data holds the whole cropped map field, not a station series
            if stationKey == "map_data":
                continue
            stationDict = dataset[stationKey]
            if dataKey not in stationDict:
                continue
            stations.setdefault(stationKey, {})[columnName] = (
                stationDict["times"], stationDict[dataKey]
            )
    return stations


def stationNames(STATIONS_FILE):
    if not STATIONS_FILE or not os.path.exists(STATIONS_FILE):
        return {}
    with open(STATIONS_FILE) as stations_file:
        stationsDict = json.load(stations_file)
    return {key: value["name"] for key, value in stationsDict.get("NOS", {}).items()}


def safeName(name):
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in name)


def isFinite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def writeStationCsv(filePath, columns):
    """Union the timestamps of every column so series on different clocks still line up."""
    allTimes = sorted({int(time) for (times, values) in columns.values() for time in times})
    lookups = {
        columnName: {int(time): value for (time, value) in zip(times, values)}
        for columnName, (times, values) in columns.items()
    }
    columnNames = [name for (_, _, name) in SERIES if name in columns]
    with open(filePath, "w", newline="") as outfile:
        writer = csv.writer(outfile)
        writer.writerow(["time_utc", "unix_time"] + columnNames)
        for time in allTimes:
            row = [
                datetime.fromtimestamp(time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                time,
            ]
            for columnName in columnNames:
                value = lookups[columnName].get(time, "")
                # NaN marks a station the interpolator could not resolve (outside the
                # mesh, or sitting on dry nodes). Leave the cell empty so spreadsheets
                # and pandas read it as missing instead of the string "nan".
                row.append(value if value == "" or isFinite(value) else "")
            writer.writerow(row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tempDir", required=True, help="tempDir passed to generateGraphs.py")
    parser.add_argument("--stations", default=DEFAULT_STATIONS_FILE, help="station metadata, for names")
    parser.add_argument("--out", default="station_csv/", help="output directory for the CSVs")
    args = parser.parse_args()

    stations = loadSeries(args.tempDir)
    if not stations:
        raise SystemExit("no station series found in " + args.tempDir)

    names = stationNames(args.stations)
    if not os.path.exists(args.out):
        os.makedirs(args.out)

    for stationKey in sorted(stations.keys()):
        name = names.get(stationKey, stationKey)
        filePath = os.path.join(args.out, safeName(name) + "_water.csv")
        columns = stations[stationKey]
        writeStationCsv(filePath, columns)
        counts = []
        for columnName, (times, values) in columns.items():
            counts.append(columnName + " " + str(sum(1 for v in values if isFinite(v))) + "/" + str(len(values)))
        print("wrote", filePath, "--", ", ".join(counts), flush=True)
