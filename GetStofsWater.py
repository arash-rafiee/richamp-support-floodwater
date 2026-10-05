# Pulls NOAA STOFS-2D-Global station water levels (combined tide + surge, "cwl")
# for one forecast cycle and saves them per station for the water graphs.
#
# Every cycle (00/06/12/18Z) since late 2020 is kept on NOAA's public AWS bucket,
# so past events and the next one can both be fetched the same way:
#   https://noaa-gestofs-pds.s3.amazonaws.com/stofs_2d_glo.YYYYMMDD/stofs_2d_glo.tHHz.points.cwl.nc
# Values are meters "above geoid" as labelled in the file.

from urllib.request import urlretrieve
from datetime import datetime, timedelta, timezone
import json
import os
import netCDF4 as nc
import numpy as np
from Encoders import NumpyEncoder

STOFS_BUCKET_URL = "https://noaa-gestofs-pds.s3.amazonaws.com/"


def stofsCycleFor(dateObject):
    """The latest 6-hourly STOFS cycle at or before dateObject, as YYYYMMDDHH."""
    dateObject = dateObject.astimezone(timezone.utc)
    return dateObject.replace(hour=dateObject.hour - dateObject.hour % 6, minute=0, second=0, microsecond=0).strftime("%Y%m%d%H")


def downloadStofsPoints(cycle, temp_directory):
    """Downloads the station file of one cycle (YYYYMMDDHH) into temp_directory once
    and returns its path; later runs reuse the downloaded file."""
    day, hour = cycle[0:8], cycle[8:10]
    fileName = f"stofs_2d_glo.t{hour}z.points.cwl.nc"
    filePath = os.path.join(temp_directory, f"stofs_2d_glo.{day}.{fileName}")
    if not os.path.exists(filePath):
        url = f"{STOFS_BUCKET_URL}stofs_2d_glo.{day}/{fileName}"
        print("Downloading STOFS", url, flush=True)
        urlretrieve(url, filePath + ".part")
        os.replace(filePath + ".part", filePath)
    return filePath


class GetStofsWater:
    """stofs is a cycle (YYYYMMDDHH), "auto" for the cycle at the start of the ADCIRC
    water series, or the path of an already downloaded points.cwl.nc file. Stations
    are matched to the NOS stations of STATIONS_FILE by their NOAA id and the series
    is cut to startDateObject..endDateObject."""
    def __init__(self, STATIONS_FILE="", STOFS_WATER_DATA_FILE="", stofs="auto", startDateObject=None, endDateObject=None):
        temp_directory = STOFS_WATER_DATA_FILE[0:STOFS_WATER_DATA_FILE.rfind("/") + 1]
        with open(STATIONS_FILE) as stations_file:
            stationsDict = json.load(stations_file)

        if os.path.isfile(stofs):
            stofsFile = stofs
        else:
            cycle = stofsCycleFor(startDateObject) if stofs == "auto" else stofs
            stofsFile = downloadStofsPoints(cycle, temp_directory)
        print("STOFS file", stofsFile, flush=True)

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

        waterDict = {}
        for key, stationDict in stationsDict["NOS"].items():
            stationId = str(stationDict["id"])
            # the NOAA id is the third word; anything after it is free text (e.g. "Pier 1")
            matches = [index for index, name in enumerate(stofsNames) if len(name) > 2 and name[2] == stationId]
            if not matches:
                print("No STOFS point for station", key, stationDict["name"], flush=True)
                continue
            waterDict[key] = {}
            waterDict[key]["times"] = unixTimes[keep]
            waterDict[key]["water"] = waters[keep, matches[0]]
        dataset.close()

        with open(STOFS_WATER_DATA_FILE, "w") as outfile:
            json.dump(waterDict, outfile, cls=NumpyEncoder)
