# Pulls NOAA STOFS-2D-Global station water levels (combined tide + surge, "cwl")
# for one forecast cycle and saves them per station for the water graphs.
#
# Every cycle (00/06/12/18Z) since late 2020 is kept on NOAA's public AWS bucket,
# so past events and the next one can both be fetched the same way:
#   https://noaa-gestofs-pds.s3.amazonaws.com/stofs_2d_glo.YYYYMMDD/stofs_2d_glo.tHHz.points.cwl.nc
# Values are meters "above geoid" as labelled in the file. The same folder holds the
# tide-only run of the cycle as ...points.htp.nc (product "htp").

from urllib.request import urlretrieve
from urllib.error import HTTPError
from datetime import datetime, timedelta, timezone
import json
import os
import netCDF4 as nc
import numpy as np
from Encoders import NumpyEncoder

STOFS_BUCKET_URL = "https://noaa-gestofs-pds.s3.amazonaws.com/"
# A cycle's points file runs from 6 hours before the cycle time to 180 hours after it
STOFS_CYCLE_HOURS = 180


def stofsCycleFor(dateObject):
    """The latest 6-hourly STOFS cycle at or before dateObject, as YYYYMMDDHH."""
    dateObject = dateObject.astimezone(timezone.utc)
    return dateObject.replace(hour=dateObject.hour - dateObject.hour % 6, minute=0, second=0, microsecond=0).strftime("%Y%m%d%H")


def downloadStofsPoints(cycle, temp_directory, product="cwl"):
    """Downloads the station file of one cycle (YYYYMMDDHH) into temp_directory once
    and returns its path; later runs reuse the downloaded file. product is "cwl"
    (tide + surge, the default) or "htp" (the tide-only run)."""
    day, hour = cycle[0:8], cycle[8:10]
    fileName = f"stofs_2d_glo.t{hour}z.points.{product}.nc"
    filePath = os.path.join(temp_directory, f"stofs_2d_glo.{day}.{fileName}")
    if not os.path.exists(filePath):
        url = f"{STOFS_BUCKET_URL}stofs_2d_glo.{day}/{fileName}"
        print("Downloading STOFS", url, flush=True)
        urlretrieve(url, filePath + ".part")
        os.replace(filePath + ".part", filePath)
    return filePath


def readStofsPoints(stofsFile, stationsDict, startDateObject=None, endDateObject=None, report=True):
    """Reads one points file into {station key: {"index", "times", "water"}} for the NOS
    stations of stationsDict that have a STOFS point, cut to startDateObject..endDateObject."""
    dataset = nc.Dataset(stofsFile)
    timeVariable = dataset.variables["time"]
    times = nc.num2date(timeVariable[:], timeVariable.units, only_use_cftime_datetimes=False, only_use_python_datetimes=True)
    unixTimes = np.array([time.replace(tzinfo=timezone.utc).timestamp() for time in times])
    keep = np.ones(len(unixTimes), dtype=bool)
    if startDateObject is not None:
        keep &= unixTimes >= startDateObject.timestamp()
    if endDateObject is not None:
        keep &= unixTimes <= endDateObject.timestamp()

    # station_name reads like "CPTR1 SOUS41 8452944 RI Conimicut Light"
    stofsNames = [nc.chartostring(name).item().split() for name in dataset.variables["station_name"][:]]
    waters = np.ma.filled(dataset.variables["zeta"][:].astype(np.float64), np.nan)

    series = {}
    for key, stationDict in stationsDict["NOS"].items():
        stationId = str(stationDict["id"])
        # the NOAA id is the third word; anything after it is free text (e.g. "Pier 1")
        matches = [index for index, name in enumerate(stofsNames) if len(name) > 2 and name[2] == stationId]
        if not matches:
            if report:
                print("No STOFS point for station", key, stationDict["name"], flush=True)
            continue
        series[key] = {"index": matches[0], "times": unixTimes[keep], "water": waters[keep, matches[0]]}
    dataset.close()
    return series


def dailyStofsSegments(startDateObject, endDateObject, temp_directory, product="cwl"):
    """[file, start, end] per day of the window, each day taken from the cycle issued at
    its start (the latest cycle at startDateObject, then every following 00Z cycle), so a
    window longer than one cycle is covered by the freshest forecast of each day. A cycle
    not on the bucket yet leaves the previous one to run on to the end of the window."""
    if endDateObject is None:
        endDateObject = startDateObject + timedelta(hours=STOFS_CYCLE_HOURS)
    segments = []
    cycle = stofsCycleFor(startDateObject)
    segmentStart = startDateObject
    while segmentStart < endDateObject:
        try:
            stofsFile = downloadStofsPoints(cycle, temp_directory, product)
        except HTTPError as error:
            if error.code != 404 or not segments:
                raise
            print("STOFS cycle", cycle, "is not on the bucket yet; the previous cycle covers the rest", flush=True)
            break
        nextDay = (segmentStart.astimezone(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        segments.append([stofsFile, segmentStart, nextDay - timedelta(seconds=1)])
        segmentStart = nextDay
        cycle = nextDay.strftime("%Y%m%d00")
    segments[-1][2] = endDateObject
    for stofsFile, segmentStart, segmentEnd in segments:
        print("STOFS file", stofsFile, "for", segmentStart.strftime("%Y-%m-%d %H:%M"), "to", segmentEnd.strftime("%Y-%m-%d %H:%M"), flush=True)
    return segments


class GetStofsWater:
    """stofs is a cycle (YYYYMMDDHH), "auto" for the cycle at the start of the ADCIRC
    water series, or the path of an already downloaded points.cwl.nc file. Stations
    are matched to the NOS stations of STATIONS_FILE by their NOAA id and the series
    is cut to startDateObject..endDateObject.

    product picks the bucket file: "cwl" (tide + surge, the default) or "htp" (tide only).
    stitch=True (the STOFS-only graphs) builds the window from the daily cycles instead of
    one, see dailyStofsSegments; a file path in stofs is always used as it is."""
    def __init__(self, STATIONS_FILE="", STOFS_WATER_DATA_FILE="", stofs="auto", startDateObject=None, endDateObject=None,
                 product="cwl", stitch=False):
        temp_directory = STOFS_WATER_DATA_FILE[0:STOFS_WATER_DATA_FILE.rfind("/") + 1]
        with open(STATIONS_FILE) as stations_file:
            stationsDict = json.load(stations_file)

        if os.path.isfile(stofs):
            stofsFile = stofs
            segments = [(stofsFile, startDateObject, endDateObject)]
            print("STOFS file", stofsFile, flush=True)
        elif stitch:
            segments = dailyStofsSegments(startDateObject, endDateObject, temp_directory, product)
        else:
            cycle = stofsCycleFor(startDateObject) if stofs == "auto" else stofs
            stofsFile = downloadStofsPoints(cycle, temp_directory, product)
            segments = [(stofsFile, startDateObject, endDateObject)]
            print("STOFS file", stofsFile, flush=True)

        waterDict = {}
        for segmentIndex, (stofsFile, segmentStart, segmentEnd) in enumerate(segments):
            series = readStofsPoints(stofsFile, stationsDict, segmentStart, segmentEnd, report=(segmentIndex == 0))
            for key, station in series.items():
                if key not in waterDict:
                    # the three station keys the water graphs read beside the series
                    stationDict = stationsDict["NOS"][key]
                    waterDict[key] = {"nodeIndex": station["index"],
                                      "latitude": float(stationDict["latitude"]), "longitude": float(stationDict["longitude"]),
                                      "times": [], "water": []}
                waterDict[key]["times"].append(station["times"])
                waterDict[key]["water"].append(station["water"])
        for station in waterDict.values():
            station["times"] = np.concatenate(station["times"])
            station["water"] = np.concatenate(station["water"])

        with open(STOFS_WATER_DATA_FILE, "w") as outfile:
            json.dump(waterDict, outfile, cls=NumpyEncoder)
