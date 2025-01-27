# Calculates runup with a set of json data files, then writes the runup to a json file
# pranav 1/16/2024

from datetime import datetime, timedelta, timezone
import json
import haversine
import math
import numpy as np
from Encoders import NumpyEncoder
        
class GetRunup:
    def __init__(self, 
        STATIONS_FILE="",
        ADCIRC_WATER_DATA_FILE="", 
        WAVE_SWH_DATA_FILE="", 
        WAVE_MWD_DATA_FILE="",
        WAVE_MWP_DATA_FILE="",
        ADCIRC_MESH_DATA_FILE="",
        RUNUP_DATA_FILE=""):
        print("Generating Runup!", flush=True)
        temp_directory = RUNUP_DATA_FILE[0:RUNUP_DATA_FILE.rfind("/") + 1]
        with open(STATIONS_FILE) as stations_file:
            stationsDict = json.load(stations_file)
        with open(ADCIRC_WATER_DATA_FILE) as datafile:
            waterDict = json.load(datafile)
        with open(WAVE_SWH_DATA_FILE) as datafile:
            swhDict = json.load(datafile)
        with open(WAVE_MWD_DATA_FILE) as datafile:
            mwdDict = json.load(datafile)
        with open(WAVE_MWP_DATA_FILE) as datafile:
            mwpDict = json.load(datafile)
        with open(ADCIRC_MESH_DATA_FILE) as datafile:
            meshDict = json.load(datafile)
            


        # Noreaster 12/23 festivus  storm 22, 23
        # startDate = "20221220"
        # endDate = "20221224"
        # dateStartFormat = "2022-12-20"
        # 
        # heightStartDate = "2022-12-20T00:00:00Z"
        # heightEndDate = "2022-12-24T23:59:59Z"
    
        runupDict = {}

        for key in stationsDict["RUNUP"].keys():
#             The RUNUP stations should correspond to a node on the ADCIRC mesh
#               The RUNUP stations can also include an offshore node in the json, bypassing the need to find the offshore node index.
            stationDict = stationsDict["RUNUP"][key]
            stationId = stationDict["id"]
            stationName = stationDict["name"]
            shorelineCoordinates = (float(stationDict["latitude"]), float(stationDict["longitude"]))
            offshoreKey = stationDict["offshoreKey"]
            offshoreLatitude = stationDict["offshoreLatitude"]
            offshoreLongitude = stationDict["offshoreLongitude"]
            offshoreCoordinates = (float(stationDict["offshoreLatitude"]), float(stationDict["offshoreLongitude"]))

            
            runupTimes = waterDict[offshoreKey]["times"]
            offshoreWater = waterDict[offshoreKey]["water"]
            offshoreSwh = swhDict[offshoreKey]["swh"]
            offshoreMwd = mwdDict[offshoreKey]["mwd"]
            offshoreMwp = mwpDict[offshoreKey]["mwp"]
            shorelineElevation = float(meshDict[key]["elevation"])
            offshoreElevation = float(meshDict[offshoreKey]["elevation"])
#             print(offshoreWater, offshoreSwh, offshoreMwd, offshoreMwp, shorelineElevation, offshoreElevation)
#             print("max time, water, swg, mwd, mwp, and elevation shoreline offshore", max(offshoreWater), max(offshoreSwh), max(offshoreMwd), max(offshoreMwp), shorelineElevation, offshoreElevation)
    #                             distance and threshold in kilometers
            distance = haversine.haversine(offshoreCoordinates, shorelineCoordinates) * 1000
            print("distance between offshore and shoreline", distance)
#             Calculate average slope in radians
            averageSlope = math.atan((offshoreElevation - shorelineElevation) / distance)
            print("average slope", averageSlope)
            
            g = 9.81
#             Convert mean wave period to deepwater wavelength
            offshoreWavelength = (g * np.array(offshoreMwp)**2) / (2 * math.pi)
            print("offshoreWavelength", offshoreWavelength)
#             First, calculate offshore node index from given runup station location (Can be hardcoded to a specific v18 node index)
#               A way to find the shoreline and offshore point elevation, water level, and wave parameters,
#               Observational stations can be set for the shoreline and offshore point. Then the values will be interpolated onto the points as
#               a timeseries
#               Next, generate cross shore transect from station to offshore
#               Next, extract depth profile along transect from mesh data file
#               Next, extract wave data for offshore point
#               Finally, loop through each time and calculate the runup using the extracted values and a selected formula
            runupDict[key] = {}
            runupDict[key]["times"] = runupTimes
            runupDict[key]["runup"] = offshoreWater
            runupDict[key]["runup"] = offshoreWavelength
            runupDict[key]["wavelength"] = offshoreWavelength
            runupDict[key]["nodeIndex"] = stationName
            runupDict[key]["latitude"] shorelineCoordinates[0]
            runupDict[key]["longitude"] = shorelineCoordinates[1]
        
        # print(windDict)
        with open(RUNUP_DATA_FILE, "w") as outfile:
            json.dump(runupDict, outfile, cls=NumpyEncoder)
