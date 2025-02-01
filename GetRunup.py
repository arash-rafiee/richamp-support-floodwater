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
        WAVE_PWP_DATA_FILE="",
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
        with open(WAVE_PWP_DATA_FILE) as datafile:
            pwpDict = json.load(datafile)
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
            offshoreCoordinates = (float(stationDict["offshoreLatitude"]), float(stationDict["offshoreLongitude"]))
            surfKey = stationDict["surfKey"]
            surfCoordinates = (float(stationDict["surfLatitude"]), float(stationDict["surfLongitude"]))
            
            runupTimes = waterDict[offshoreKey]["times"]
            offshoreWater = waterDict[offshoreKey]["water"]
            offshoreSwh = swhDict[offshoreKey]["swh"]
            offshoreMwd = mwdDict[offshoreKey]["mwd"]
            offshorePwp = pwpDict[offshoreKey]["pwp"]
            shorelineElevation = float(meshDict[key]["elevation"])
            surfElevation = float(meshDict[surfKey]["elevation"])
            offshoreElevation = float(meshDict[offshoreKey]["elevation"])
#             print(offshoreWater, offshoreSwh, offshoreMwd, offshoreMwp, shorelineElevation, offshoreElevation)
#             print("max time, water, swg, mwd, mwp, and elevation shoreline offshore", max(offshoreWater), max(offshoreSwh), max(offshoreMwd), max(offshoreMwp), shorelineElevation, offshoreElevation)
    #                             distance and threshold in kilometers
            print("shorelineElevation, surfElevation, offshoreElevation", shorelineElevation, surfElevation, offshoreElevation)
            surfDistance = haversine.haversine(surfCoordinates, shorelineCoordinates) * 1000
            offshoreDistance = haversine.haversine(offshoreCoordinates, shorelineCoordinates) * 1000
            print("surfDistance, offshoreDistance", surfDistance, offshoreDistance)
#             print("distance between offshore and shoreline", distance)
#             Calculate average slope in radians
#              hardcode the average slope
#             distance = 50
#             offshoreElevation = 5
            averageSlope = math.atan((shorelineElevation - surfElevation) / surfDistance)
            print("average slope", averageSlope)
#             averageSlope = 0.025
#             averageSlope = 
            g = 9.81
#             Convert mean wave period to deepwater wavelength
            offshoreWavelength = (g * np.array(offshorePwp)**2) / (2 * math.pi)
#             print("offshoreWavelength", offshoreWavelength)
            
#             Iribarren number
            swh200 = offshoreSwh[200]
            wavelength200 = offshoreWavelength[200]
            iribarren200 = averageSlope / (np.sqrt(swh200 / wavelength200))
#             print("swh200", swh200, "wavelength200", wavelength200)
#             print("IRIBARREN NUMBER AT INDEX 200:", iribarren200)
            offshoreSteepness = np.array(offshoreSwh) / offshoreWavelength
            iribarren = (averageSlope / (np.sqrt(offshoreSteepness)))
#             First, calculate offshore node index from given runup station location (Can be hardcoded to a specific v18 node index)
#               A way to find the shoreline and offshore point elevation, water level, and wave parameters,
#               Observational stations can be set for the shoreline and offshore point. Then the values will be interpolated onto the points as
#               a timeseries
#               Next, generate cross shore transect from station to offshore
#               Next, extract depth profile along transect from mesh data file
#               Next, extract wave data for offshore point
#               Finally, loop through each time and calculate the runup using the extracted values and a selected formula


#           Revised 1/29/25
#             Key steps to calculating the wave runup
#              Find the deep water wavelength. This is a dispeersion problem that should be calculated with account to the water depth.
#                     -I tink done
#             Find the significant wave height, shoaled to the appropriate offshore distance
#                 Vosdoukas uses SWH and wavelength at 93 meter depth
#                     Stockdon "reverse shoaled the data to deep water using linear wave theory". They assimilated data from a bunch of places
#                         Holman used wave data 3km away at 20 m depth
#              Calculate the iribarren number, validate its validity
#             Figure out how to convert the mean wave direction into meaningful values
#                 Created additional points at NJ and Katama Airfield. Running to see what the min max values of MWD are

#              -Also running on local for the purpose of working out the calculations for SWH and Deepwater wavelength

#             1/30/25 Midnight update.. well folks, thats all! Tried a whole numch of things, iribarren calculation is off. I dont know if its a problem with the average slope or the wavelength calculaton, or the iribarren
#               formula itself. Poop. Should I just quit?

#              1/30/25 11PM, tried tweaking. Added graphing for wavelength and iribarren, as well as debugs showing calculation of iribarren.
#                 No luck, iribarren is stll way too low. Do I have to use peak wave period?

#             1/31/25 I fixed it. Was multiplying instead of dividing in the iribarren formula. 
#                 The iribarren number calculations look correct, I can now calculate runup?
#                     Adding graphing for shoreline, surf, and offshore points to visualize where they are in space and their elevation
            runupDict[key] = {}
            runupDict[key]["surfDistance"] = surfDistance
            runupDict[key]["offshoreDistance"] = offshoreDistance
            runupDict[key]["averageSlope"] = averageSlope
            runupDict[key]["times"] = runupTimes
            runupDict[key]["runup"] = iribarren
            runupDict[key]["wavelength"] = offshoreWavelength
            runupDict[key]["steepness"] = offshoreSteepness
            runupDict[key]["iribarren"] = iribarren
            runupDict[key]["nodeIndex"] = stationName
            runupDict[key]["latitude"] = shorelineCoordinates[0]
            runupDict[key]["longitude"] = shorelineCoordinates[1]
        
        # print(windDict)
        print("Writing runup data file!")
        with open(RUNUP_DATA_FILE, "w") as outfile:
            json.dump(runupDict, outfile, cls=NumpyEncoder)
