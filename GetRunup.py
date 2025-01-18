# Calculates runup with a set of json data files, then writes the runup to a json file
# pranav 1/16/2024

from datetime import datetime, timedelta, timezone
import json
        
class GetRunup:
    def __init__(self, 
        STATIONS_FILE="",
        ADCIRC_WATER_DATA_FILE="", 
        WAVE_SWH_DATA_FILE="", 
        WAVE_MWD_DATA_FILE="",
        WAVE_MWP_DATA_FILE="",
        ADCIRC_MESH_DATA_FILE="",
        RUNUP_DATA_FILE=""):
        temp_directory = RUNUP_DATA_FILE[0:RUNUP_DATA_FILE.rfind("/") + 1]
        with open(STATIONS_FILE) as stations_file:
            stationsDict = json.load(stations_file)
            


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
            latitude = stationDict["latitude"]
            longitude = stationDict["longitude"]
#             First, calculate offshore node index from given runup station location (Can be hardcoded to a specific v18 node index)
#               Next, generate cross shore transect from station to offshore
#               Next, extract depth profile along transect from mesh data file
#               Next, extract wave data for offshore point
#               Finally, loop through each time and calculate the runup using the extracted values and a selected formula
            runupDict[key] = {}
            runupDict[key]["times"] = [100, 200, 300]
            runupDict[key]["runup"] = [2, 3, 1.4]
        
        # print(windDict)
        with open(RUNUP_DATA_FILE, "w") as outfile:
            json.dump(runupDict, outfile)
