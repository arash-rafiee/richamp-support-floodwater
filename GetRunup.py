# Calculates runup with a set of json data files, then writes the runup to a json file
# pranav 1/16/2024

from datetime import datetime, timedelta, timezone
import json
import haversine
import math
import numpy as np
from Encoders import NumpyEncoder
from geographiclib.geodesic import Geodesic
        
class GetRunup:

    def calculateHolmanHighRunup(self, iribarrenNumber):
        slope = 0.80
        intercept = 0.11
        return (iribarrenNumber * slope) + intercept

    def calculateHolmanMidRunup(self, iribarrenNumber):
        slope = 0.93
        intercept = 0.04
        return (iribarrenNumber * slope) + intercept

    def calculateHolmanLowRunup(self, iribarrenNumber):
        slope = 0.24
        intercept = 0.65
        return (iribarrenNumber * slope) + intercept
        
    def calculateRunupWaterline(self, waterlineCoordinates, tangentCoordinates, runup):
        # Extract coordinates (latitude, longitude)
        lat1, lon1 = waterlineCoordinates
        lat2, lon2 = tangentCoordinates
    
        # Calculate the geodesic between the points
        geod = Geodesic.WGS84
        g = geod.Inverse(lat1, lon1, lat2, lon2)
    
        # Get the azimuth (bearing) at the first point
        azi = g['azi1']
    
        # Calculate perpendicular bearing (90 degrees clockwise)
        perp_azi = (azi + 90) % 360
    
        # Calculate new positions by moving perpendicular to the line
        # Move waterline point
        r1 = geod.Direct(lat1, lon1, perp_azi, runup)
        runupWaterlineLat = r1['lat2']
        runupWaterlineLon = r1['lon2']
    
        # Move tangent point
        r2 = geod.Direct(lat2, lon2, perp_azi, runup)
        runupTangentLat = r2['lat2']
        runupTangentLon = r2['lon2']
    
        # Return translated coordinates as tuples
        runupWaterlineCoordinates = (runupWaterlineLat, runupWaterlineLon)
        runupTangentCoordinates = (runupTangentLat, runupTangentLon)
    
        return runupWaterlineCoordinates, runupTangentCoordinates

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
            normalDict = stationsDict["NORMAL"][key]
            tangentDict = stationsDict["TANGENT"][key]
            stationId = stationDict["id"]
            stationName = stationDict["name"]
            shorelineCoordinates = (float(stationDict["latitude"]), float(stationDict["longitude"]))
            offshoreKey = stationDict["offshoreKey"]
            offshoreCoordinates = (float(stationDict["offshoreLatitude"]), float(stationDict["offshoreLongitude"]))
            surfKey = stationDict["surfKey"]
            surfCoordinates = (float(stationDict["surfLatitude"]), float(stationDict["surfLongitude"]))
            deeplineKey = stationDict["deeplineKey"]
            
            runupTimes = waterDict[offshoreKey]["times"]
#             runupTimes = swhDict[offshoreKey]["times"]
            offshoreWater = waterDict[offshoreKey]["water"]
            
#             waterTimestampsInitialized = False
#             for stationKey in waterDict.keys():
#                 if(stationKey != "map_data"):
#                     nodeIndex = waterDict[stationKey]["nodeIndex"]
#                     if(not self.tideExists or (stationKey in tideDataset.keys())):
#                         self.waterLabels.append(nodeIndex)
#                         self.waterLatitudes.append(waterDataset[stationKey]["latitude"])
#                         self.waterLongitudes.append(waterDataset[stationKey]["longitude"])
#                 
#                         if(not tideLabelsInitialized):
#                             self.tideLabels.append(self.obsMetadata["NOS"][stationKey]["name"])
#                             self.tideLatitudes.append(float(self.obsMetadata["NOS"][stationKey]["latitude"]))
#                             self.tideLongitudes.append(float(self.obsMetadata["NOS"][stationKey]["longitude"]))
# 
#                         datapointWaters = []
#                         for index in range(len(waterDataset[stationKey]["times"])):
#                             if(self.waterStartDate == None):
#                                 self.waterStartDate = datetime.fromtimestamp(int(waterDataset[stationKey]["times"][index]), timezone.utc)
#                             if(not waterTimestampsInitialized):
#                                 self.waterTimes.append(self.unixTimeToDeltaHours(waterDataset[stationKey]["times"][index], self.waterStartDate))
#                             datapointWaters.append(waterDataset[stationKey]["water"][index])
#                         waterTimestampsInitialized = True
#                         self.datapointsWaters.append(datapointWaters)
#                         if(self.tideExists):
#                             tideTimes = []
#                             tideWaters = []
#                 #                         Height is not station altitude, it is sea surface height
#                             for index in range(len(tideDataset[stationKey]["times"])):
#                                 tideTimes.append(self.unixTimeToDeltaHours(tideDataset[stationKey]["times"][index], self.waterStartDate))
#                                 tideWater = tideDataset[stationKey]["water"][index]
#                                 tideWaters.append(tideWater)
#                             self.tideDatapointsTimes.append(tideTimes)
#                             self.tideDatapointsWaters.append(tideWaters)
#                             tidePredictionTimes = []
#                             tidePredictionWaters = []
#                 #                         Height is not station altitude, it is sea surface height
#                             for index in range(len(tideDataset[stationKey]["prediction_times"])):
#                                 tidePredictionTimes.append(self.unixTimeToDeltaHours(tideDataset[stationKey]["prediction_times"][index], self.waterStartDate))
#                                 tidePredictionWater = tideDataset[stationKey]["prediction_water"][index]
#                                 tidePredictionWaters.append(tidePredictionWater)
#                             self.tideDatapointsPredictionTimes.append(tidePredictionTimes)
#                             self.tideDatapointsPredictionWaters.append(tidePredictionWaters)
#             tideLabelsInitialized = True
            
            
#             offshoreSwh = swhDict[offshoreKey]["swh"]
#             offshoreMwd = mwdDict[offshoreKey]["mwd"]
#             offshorePwp = pwpDict[offshoreKey]["pwp"]
#           Use wave parameters at 2Km away
            offshoreSwh = swhDict[deeplineKey]["swh"]
            offshoreMwd = mwdDict[deeplineKey]["mwd"]
            offshorePwp = pwpDict[deeplineKey]["pwp"]
            shorelineElevation = float(meshDict[key]["elevation"])
            surfElevation = float(meshDict[surfKey]["elevation"])
            offshoreElevation = float(meshDict[offshoreKey]["elevation"])
            
            
#             Calculating wave parameters perserved in arrays
            g = 9.81
            offshoreWavelength = (g * np.array(offshorePwp)**2) / (2 * math.pi)
            offshoreSteepness = np.array(offshoreSwh) / offshoreWavelength

            waterlineKeys = []
            averageSlopes = []
            iribarrenNumbers = []
            runupValues = []
            runupValuesHolmanHigh = []
            runupValuesHolmanMid = []
            runupValuesHolmanLow = []
            runupWaterlineLatitudes = [] 
            runupWaterlineLongitudes = [] 
            runupTangentLatitudes = [] 
            runupTangentLongitudes = []
            
#             From this point, iterate through each timestep in the wave file.
            for index, waterValue in enumerate(offshoreWater):
                waterlineKey = None
                for normalKey in normalDict:
                    normalStationWaterValue = waterDict[normalKey]["water"][index]
#                     print("normalStationWaterValue, index", index, normalStationWaterValue)
                    if(not np.isnan(normalStationWaterValue)):
                        waterlineKey = normalKey
                        break
                if(waterlineKey != None):
#                     print("Found waterline", waterlineKey, " for index", index)
                    waterlineKeys.append(waterlineKey)
                else:
                    print("DID NOT FIND WATERLINE! Appending previous waterline key.")
                    print("If no previous key exists, will error out.")
                    waterlineKeys.append(waterlineKey[-1])
                
#                 Now I have the waterline key

#                   The corresponding tangent key

                tangentStation = tangentDict[waterlineKey]
                tangentCoordinates = (float(tangentStation["latitude"]), float(tangentStation["longitude"]))
                
                waterlineStation = normalDict[waterlineKey]
                waterlineCoordinates = (float(waterlineStation["latitude"]), float(waterlineStation["longitude"]))
                waterlineElevation = float(meshDict[waterlineKey]["elevation"])
                
#                 Now I need to calculate the averageSlope using the waterlineKey point


                waterlineDistance = haversine.haversine(waterlineCoordinates, shorelineCoordinates) * 1000
                averageSlope = math.atan((shorelineElevation - waterlineElevation) / waterlineDistance)
                averageSlopes.append(averageSlope)
                #           Then I need to calculate the wave parameters
#           That can happen outside of the loop

#           Then calculate the irribarren number
                iribarren = (averageSlope / (np.sqrt(offshoreSteepness[index])))
                iribarrenNumbers.append(iribarren)
                runupHolmanHigh = self.calculateHolmanHighRunup(iribarren) * offshoreSwh[index]
                runupHolmanMid = self.calculateHolmanMidRunup(iribarren) * offshoreSwh[index]
                runupHolmanLow = self.calculateHolmanLowRunup(iribarren) * offshoreSwh[index]
                runupValues.append(runupHolmanLow)
                runupValuesHolmanHigh.append(runupHolmanHigh)
                runupValuesHolmanMid.append(runupHolmanMid)
                runupValuesHolmanLow.append(runupHolmanLow)
                
                runupWaterlineCoordinates, runupTangentCoordinates = self.calculateRunupWaterline(waterlineCoordinates, tangentCoordinates, runupHolmanLow)
                runupWaterlineLatitudes.append(runupWaterlineCoordinates[0])
                runupWaterlineLongitudes.append(runupWaterlineCoordinates[1])
                runupTangentLatitudes.append(runupTangentCoordinates[0])
                runupTangentLongitudes.append(runupTangentCoordinates[1])

#           Then calculate the runup value 2% exceedence

#           Then calculate the runupLine and runupTangentLine
#           
#           Save these. Will end up having two arrays called runupWaterlineLatitudes[] runupWaterlineLogitudes runupTangentLatitudes runupTangentLongitudes

#           These will get graphed alongside the waterlineKey generated line with the tangent point


#                     print("waterValue at ", normalKey, waterDict[normalKey]["water"][index])
#                     print("closest Nodes to normalkey", normalKey, waterDict[normalKey]["nodeIndex"])
#                     closestNode = waterDict[normalKey]["nodeIndex"]
#                     closestNodeWaterIndex = waterDict["map_data"]["map_points"].index(closestNode)
#                     print(closestNodeWaterIndex)
#                     print(len(waterDict["map_data"]["map_water"]))
#                     print(waterDict["map_data"]["map_water"][index][closestNodeWaterIndex])
#                     normalWaterValue = waterDict[normalKey]["water"][index]
#                     if not np.isnan(normalWaterValue):
#                         waterlineKey = normalKey
#                         break
#                 normalStation = normalDict[waterlineKey]
#                 tangentStation = tangentDict[waterlineKey]
#                 print("Normal, tangent stations", normalStation, tangentStation)
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

#             Convert mean wave period to deepwater wavelength
#             print("offshoreWavelength", offshoreWavelength)
            
#             Iribarren number
#             swh200 = offshoreSwh[200]
#             wavelength200 = offshoreWavelength[200]
#             iribarren200 = averageSlope / (np.sqrt(swh200 / wavelength200))
#             print("swh200", swh200, "wavelength200", wavelength200)
#             print("IRIBARREN NUMBER AT INDEX 200:", iribarren200)
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

#           2/1/25 program in the runup formulas and compare in graphs

#           2/3/25 Define offshore points with normal vector starting from shoreline.
#           Graph swh as a function of distance from shoreline
#           Graph mwp vp pwp to compare
#           Create script to convert GFS oceanweather wind to 306 type wind for ManRuns

#           2/5 The convert script isint working. Debug by converting oceanweather to 306, then to nc, then graphing until it is right.
#           Ask mr. g to write it starting at max lat and min lon, row by row. And ask him to make a Wind_Inp file so its easy to convert to NetCDF

#           Finished OceanwatherTo306 script. Got runs. Now need to calculate runup.
#            First step is to get a series of points x distance from the 0 water level point on the shpreline.
#             The first question, how to find the 0 water level point for a given time?
#           One idea is to interpolate to where there is 0 water. 
#               Another idea is to pick where the water level is at a minimum.
#             
#             What has to be given is a general location of the shoreline. 
#             What can be given is a set of two points. A general coastline point and a general surfzone point.
#             The goal is, from the genral location of the shoreline point, can a precise location of the 0 water level point be generated?
#              Given the shoreline point, a search can ensue around a set radius. A subset of the water data is taken in the search area
#              The water level of the search area is scanned to try and produce a waterline line segment.



#              Scratch the above.
#                 Define a line along the beach connecting the shoreline point to the surfzone point.
#                  To find the location of runup prediction, traverse the line. Find for which position of the line the water level reaches 0.

#           I created a script to generate normal line obs stations. Detirmining the prediction location for runup for a given timestep
#              i.e finding the location of 0 water level for a given timestep
#           Is another challenge
#           The adcirc mesh might have even better resolution or atlesat on par with the normal line
#           
#           GetBuoyWater stopped working. NAVD datum isint working. Thinking I have to switch to MSL datum, then add in MSL offset to convert to navd after pulling the obs data

#           Thought of a way to get runup prediction location
#              Discretize and generate stations close to shoreline point at a fine resolution
#           For each time, Iterate through the stations, starting at the offshore side. 
#             Check the water level for each station, if the water level is equal to 0, break. The found point
#               Is the location of runup prediction.
#                This method heavily depends on the coarseness of the station locations in order to get an accurate runup prediction point.
#               Save the runup prediction points for each timestep to an array.
#               The next step is to use the runup prediction point as the shoreline point for any given timestep. This means that the beach slope and distance have
#               to be calculated from the runup prediction point, not the shoreline point.
#             It will probbably be easier to create the predictionLocations array first,
#               Then work on creating runup predictions using the predicitonLocation and surfline to generate iribarren number.

#           Water levels look wack. Don't match up with observation at all. Rerunning with fixed sea level offset.

#           I dont know whats wrong with water obs getBuoyWater program. 
#           The problem of telling what location the runup prediction is valid for is a seperate issue.
#           This problem is already solved by looking at the wet and dry nodes for a given timestamp
#             The prediction for how much runup is independent of the location? No its not. But its probbably defined as static.
#              I mean, what is being calculated is the average slope. So technically is the waterline moves inshore, then the average slope would
#               increase.

#           Basically, i thought of it.
#            A map of the beach, showing the waterline changing with each timestamp.
#              Then also a line parallel to the waterline. Showing the 2% exceddence of runup.

#           To accomplish this, the tangent of the waterline has to be calculated at each timestep.
#           Then, the tangent is translated in space by a distance of tue 2% exceedence of runup.
#           This runup tangent line is then visualized in the maps for each timestep.
#           Along with this, a timeseries of the runup value will also be produced.
#             Similar to other products. Map + timeseries

#              Whats the first problem? How can I calculate the tangent line? the current method is creating a set of interpolated stations
#               that is normal to the waterline. 
#               
#              The idea, probbably repeating myself, is to create a hyerresolution set of interpolated stations, all along the beach.
#             Then iterate through these hyperstations, and find the first one (starting from oceanside) that has a waterlevel of nan or 0.
#              This hyperstation is the waterline point.

#              Another stations can be defined alongside the waterline station.
#                 This station can be called the tangent station, and geometrically defines a waterline on the beach.
#               The tangent stations will be hyperresolutionized along with the normal stations, forming a pair of points
#               At each hyerresolution grid step.
    
#               Given the hyperresolution point, the corresponding hyperresolutions tangent point can be used to construct a waterline tangent.
#                  Implement this ASAP. this is what I will present for seminar.


#           Now, how to construct tangent line?
#            It would be easy to construct a tangent line by reading the water map directly. 
#              The hyperstation will have a closestNode attribute, signifying what node is the closest to it.

            predictionLocations = []

            runupDict[key] = {}
            runupDict[key]["surfDistance"] = surfDistance
            runupDict[key]["offshoreDistance"] = offshoreDistance
            runupDict[key]["waterlineKeys"] = waterlineKeys
            runupDict[key]["averageSlope"] = averageSlope
            runupDict[key]["averageSlopes"] = averageSlopes
            runupDict[key]["times"] = runupTimes
            runupDict[key]["runupWaterlineLatitudes"] = runupWaterlineLatitudes
            runupDict[key]["runupWaterlineLongitudes"] = runupWaterlineLongitudes
            runupDict[key]["runupTangentLatitudes"] = runupTangentLatitudes
            runupDict[key]["runupTangentLongitudes"] = runupTangentLongitudes
            runupDict[key]["runup"] = runupValues
            runupDict[key]["runupHolmanHigh"] = runupValuesHolmanHigh
            runupDict[key]["runupHolmanMid"] = runupValuesHolmanMid
            runupDict[key]["runupHolmanLow"] = runupValuesHolmanLow
            runupDict[key]["wavelength"] = offshoreWavelength
            runupDict[key]["steepness"] = offshoreSteepness
            runupDict[key]["iribarren"] = iribarrenNumbers
            runupDict[key]["predictionLocations"] = predictionLocations
            runupDict[key]["nodeIndex"] = stationName
            runupDict[key]["latitude"] = shorelineCoordinates[0]
            runupDict[key]["longitude"] = shorelineCoordinates[1]
        
        # print(windDict)
        print("Writing runup data file!")
        with open(RUNUP_DATA_FILE, "w") as outfile:
            json.dump(runupDict, outfile, cls=NumpyEncoder)
