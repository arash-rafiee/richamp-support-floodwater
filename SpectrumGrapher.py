import os
import json
import math
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.cm import ScalarMappable
from matplotlib.tri import Triangulation
from matplotlib.colors import LogNorm
from datetime import datetime, timezone
import imageio
import gc

class SpectrumGrapher:
    DATE_FORMAT = "%m/%d/%y-%HZ"
    
        
    def extractLatitudeIndex(self, nodeIndex):
        return int(nodeIndex[1: nodeIndex.find(",")])
    
    def extractLongitudeIndex(self, nodeIndex):
        return int(nodeIndex[nodeIndex.find(",") + 1: nodeIndex.find(")")])
    
        
    def extractLatitude(self, nodeIndex):
        return float(nodeIndex[1: nodeIndex.find(",")])
    
    def extractLongitude(self, nodeIndex):
        return float(nodeIndex[nodeIndex.find(",") + 1: nodeIndex.find(")")])
    
    
    def vectorSpeed(self, x,y):
        return math.sqrt(x**2 + y**2)
    
    def vectorDirection(self, x,y):
        degrees = math.degrees(math.atan2(-y,x))
        if(degrees < 0):
            return degrees + 360
        return degrees
    
    def unixTimeToDeltaHours(self, timestamp, startDate):
        delta = datetime.fromtimestamp(timestamp, timezone.utc) - startDate
        return delta.total_seconds()/3600
    
    def extrapolateWindToTenMeterHeight(self, windVelocity, altitude):
        return windVelocity
    #     WIND_PROFILE_EXPONENT = 0.11
    #     return windVelocity * ((10.0/altitude)**WIND_PROFILE_EXPONENT)

    def calculateSpectrum(self, a, b):
        print("c")
    def __init__(self, dataToGraph={}, STATIONS_FILE="", backgroundMap="", backgroundAxis=[]):
        print("Initializing grapher", flush=True)        
        self.waveStartDate = None
        self.spectrumStartDate = None

        self.backgroundMap = backgroundMap
        self.backgroundAxis = backgroundAxis
        
        if("SPECTRUM" in dataToGraph):
            self.spectrumExists = True
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
        
        
        self.spectrumLongitudes = []
        self.spectrumLatitudes = []
        self.spectrumLabels = []
        self.spectrumTimes = []
        self.spectrumFrequencies = []
        self.spectrumDirections = []
        self.spectrumValues = []

#        So loading obs, wind, and waves should be able to cover and set all available data

        if(self.spectrumExists):
            with open(dataToGraph["SPECTRUM"]) as outfile:
                spectrumDataset = json.load(outfile)
        
        self.spectrumFrequencies = spectrumDataset["frequencies"]
        self.spectrumDirections = spectrumDataset["directions"]
        for timestamp in spectrumDataset["values"].keys():
            if(self.spectrumStartDate == None):
                self.spectrumStartDate = datetime.fromtimestamp(float(timestamp), timezone.utc)
#             self.spectrumTimes.append(self.unixTimeToDeltaHours(float(timestamp), self.spectrumStartDate))
            self.spectrumTimes.append(datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%m/%d/%Y, %H:%M:%S %Z"))
            spectrumValue = []
            for point in spectrumDataset["values"][timestamp].keys():
                pointLatitude = self.extractLatitude(point)
                pointLongitude = self.extractLongitude(point)
                self.spectrumLabels.append(point)
                self.spectrumLatitudes.append(pointLatitude)
                self.spectrumLongitudes.append(pointLongitude)
#                 Appends a 2D specturm array [frequency][direction]
                spectrumValue.append(spectrumDataset["values"][timestamp][point])
            self.spectrumValues.append(spectrumValue)
#         self.spectrumLabels.append("High")




#     def plot_directional_spectrum(self, time, label, frequencies, directions, spectrum):
#         # Ensure spectrum is a numpy array
#         spectrum = np.array(spectrum)
#         if np.max(spectrum) == 0:
#             return
# 
#         # Create edges for frequencies and directions
#         # Note: Adjust the number of points in dir_edges to match the number of columns in spectrum
#         freq_edges = np.linspace(frequencies[0], frequencies[-1], len(frequencies) + 1)
#         dir_edges = np.linspace(directions[0], directions[-1] + 360, len(directions) + 1)
# 
#         # Generate meshgrid
#         freq, dir = np.meshgrid(freq_edges, dir_edges)
# 
#         # Plotting
#         fig, ax = plt.subplots(figsize=(12, 10), subplot_kw={'projection': 'polar'})
# 
#         # Convert directions to radians
#         dir_rad = np.radians(dir)
# 
#         # Extend spectrum to cover from 0 to 360 degrees
#         extended_spectrum = np.hstack([spectrum, spectrum[:, 0].reshape(-1, 1)])  # Ensure correct dimensions
# 
#         # Use pcolormesh with the correct data dimensions
#         im = ax.pcolormesh(dir_rad, freq, extended_spectrum, shading='flat', 
#                            norm=LogNorm(vmin=extended_spectrum.min().clip(1e-10), vmax=extended_spectrum.max()), 
#                            cmap='viridis')
# 
#         # Adjust ticks for polar plot
#         ax.set_rticks(frequencies)
#         ax.set_rmax(frequencies[-1])
# 
#         ax.set_xticks(np.radians(np.arange(0, 360, 45)))
#         ax.set_xticklabels([f"{d}°" for d in np.arange(0, 360, 45)])
# 
#         # Add colorbar
#         cbar = fig.colorbar(im, ax=ax, shrink=0.8)
#         cbar.set_label('Spectral Density', rotation=270, labelpad=15)
# 
#         # Title
#         ax.set_title(f"{label} Directional Wave Spectrum at {time}", va='bottom')
# 
#         plt.show()
# 
#     def plot_directional_spectrum(self, time, label, frequencies, directions, spectrum):
#         # Ensure spectrum is a numpy array
#         spectrum = np.array(spectrum)
#         if(np.max(spectrum) == 0):
#             return
#     
#         # Create edges for frequencies and directions
#         freq_edges = np.linspace(frequencies[0], frequencies[-1], len(frequencies) + 1)
#         dir_edges = np.linspace(directions[0], directions[-1] + 360, len(directions) + 1)
#     
#         # Generate meshgrid
#         freq, dir = np.meshgrid(freq_edges, dir_edges)
# 
#         # Plotting
#         fig, ax = plt.subplots(figsize=(12, 10), subplot_kw={'projection': 'polar'})
# 
#         # Convert directions to radians
#         dir_rad = np.radians(dir)
#         extended_spectrum = np.vstack([spectrum.T, spectrum.T[0]])  
#         # Use pcolormesh
#         im = ax.pcolormesh(dir_rad, frequencies, extended_spectrum, shading='gouraud', 
#                            norm=LogNorm(vmin=spectrum.min().clip(1e-10), vmax=spectrum.max()), 
#                            cmap='viridis')
#     
#         # Adjust ticks for polar plot
#         ax.set_rticks(frequencies)
#         ax.set_rmax(frequencies[-1])
# 
#         ax.set_xticks(np.radians(np.arange(0, 360, 45)))
#         ax.set_xticklabels([f"{d}°" for d in np.arange(0, 360, 45)])
# 
#         # Add colorbar
#         cbar = fig.colorbar(im, ax=ax, shrink=0.8)
#         cbar.set_label('Spectral Density', rotation=270, labelpad=15)
# 
#         # Title
#         ax.set_title(label + " Directional Wave Spectrum at " + time, va='bottom')
# 
#         plt.show()
        
    def plot_directional_spectrum(self, time, label, frequencies, directions, spectrum):
            # Ensure spectrum is a numpy array
            spectrum = np.array(spectrum)
            if np.max(spectrum) == 0:
                # If all spectral values are zero, no need to plot
                return

            # Create edges for frequencies, directions already represent edges
            freq_edges = np.linspace(frequencies[0], frequencies[-1], len(frequencies))
            dir_edges = np.linspace(directions[0], directions[0] + 360, len(directions))  # Since directions are already edges

            # Generate meshgrid
            freq, dir = np.meshgrid(freq_edges, dir_edges)

            # Plotting setup
            fig, ax = plt.subplots(figsize=(12, 10), subplot_kw={'projection': 'polar'})

            # Convert directions to radians
            dir_rad = np.radians(dir)

            # Use pcolormesh for plotting, ensuring continuity at 0/360 degrees
            im = ax.pcolormesh(dir_rad, freq, spectrum.T, shading='gouraud',
                               norm=LogNorm(vmin=spectrum.min().clip(1e-10), vmax=spectrum.max()),
                               cmap='viridis')

            # Adjust ticks for polar plot
            ax.set_rticks(frequencies)
            ax.set_rmax(frequencies[-1])

            ax.set_xticks(np.radians(np.arange(0, 360, 45)))
            ax.set_xticklabels([f"{d}°" for d in np.arange(0, 360, 45)])
            
            # Rotate the plot so that 0° (North) is at the top
            ax.set_theta_offset(np.pi / 2.0)
            ax.set_theta_direction(-1)
            # Add colorbar
            cbar = fig.colorbar(im, ax=ax, shrink=0.8)
            cbar.set_label('Spectral Density', rotation=270, labelpad=15)

            # Set title
            ax.set_title(f"{label} Directional Wave Spectrum at {time}", va='bottom')

            # Show the plot
            plt.show()
# Example usage:
# frequencies = [0.05, 0.1, 0.15, 0.2]
# directions = list(range(0, 360, 45))  # 0 to 360 degrees with 45-degree intervals
# spectrum = [[1, 2, 3, 4, 5, 6, 7, 8] for _ in frequencies]  # Dummy data
# plot_directional_spectrum(frequencies, directions, spectrum)

    def generateGraphs(self):
        timeIndex = 0
        for index in range(len(self.spectrumLabels)):
            self.plot_directional_spectrum(self.spectrumTimes[timeIndex], self.spectrumLabels[index], self.spectrumFrequencies, self.spectrumDirections, self.spectrumValues[timeIndex][index])

    
    
        graph_directory = "graphs/"

        numberOfSpectrumDatapoints = 0
#         TODO: Currently, when graphing multiple products with obs on, OBS_STATIONS must contain the same number of station 
#           entries for each type of product
        if(self.spectrumExists):
            numberOfSpectrumDatapoints = len(self.spectrumLabels)
        print("numberOfDatapoints Wave, Eta, Spectrum", numberOfWaveDatapoints, numberOfEtaDatapoints, numberOfSpectrumDatapoints, flush=True)
        fig, ax = plt.subplots()
        print("maxWind", self.maxWind, "maxRain", self.maxRain, "maxWave", self.maxSWH, "maxWater", self.maxWater, "maxEta", self.maxEta, flush=True)
        
        ax.scatter(self.obsLongitudes, self.obsLatitudes, label="Obs")
        if(self.windExists):
            ax.scatter(self.windLongitudes, self.windLatitudes, label="Wind")
        if(self.buoyExists):
                ax.scatter(self.buoyLongitudes, self.buoyLatitudes, label="Buoy")
        if(self.wavesExists):
            ax.scatter(self.waveLongitudes, self.waveLatitudes, label="Waves")
        if(self.gaugeExists):
                ax.scatter(self.gaugeLongitudes, self.gaugeLatitudes, label="Gauge")
        if(self.rainExists):
            ax.scatter(self.rainLongitudes, self.rainLatitudes, label="Rain")
        if(self.tideExists):
                ax.scatter(self.tideLongitudes, self.tideLatitudes, label="Tide")
        if(self.waterExists):
            ax.scatter(self.waterLongitudes, self.waterLatitudes, label="Water")
        if(self.etaExists):
            ax.scatter(self.etaLongitudes, self.etaLatitudes, label="Eta")
        ax.legend(loc="lower right")

        for index, label in enumerate(self.obsLabels):
            ax.annotate(label, (self.obsLongitudes[index], self.obsLatitudes[index]))
            if(self.windExists):
                ax.annotate(self.windLabels[index], (self.windLongitudes[index], self.windLatitudes[index]))
            if(self.wavesExists):
                ax.annotate(self.waveLabels[index], (self.waveLongitudes[index], self.waveLatitudes[index]))
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
            with imageio.get_writer(graph_directory + 'wind.gif', mode='I') as writer:
                for index in range(len(self.mapWindTimes)):
                    filename = "map_wind_" + str(index) + ".png"
                    image = imageio.imread(graph_directory + filename)
                    writer.append_data(image)
                for index in range(len(self.mapWindTimes)):
                    filename = "map_wind_" + str(index) + ".png"
                    os.remove(graph_directory + filename)
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
#             vmax = math.ceil(self.maxRain)
            vmax = 25
            vmaxAccumulation = 500
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
            with imageio.get_writer(graph_directory + 'rain.gif', mode='I') as writer:
                for index in range(len(self.mapRainTimes)):
                    filename = "map_rain_" + str(index) + ".png"
                    image = imageio.imread(graph_directory + filename)
                    writer.append_data(image)
                for index in range(len(self.mapRainTimes)):
                    filename = "map_rain_" + str(index) + ".png"
                    os.remove(graph_directory + filename)
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
            with imageio.get_writer(graph_directory + 'eta.gif', mode='I') as writer:
                for index in range(len(self.mapEtaTimes)):
                    filename = "map_eta_" + str(index) + ".png"
                    image = imageio.imread(graph_directory + filename)
                    writer.append_data(image)
                for index in range(len(self.mapEtaTimes)):
                    filename = "map_eta_" + str(index) + ".png"
                    os.remove(graph_directory + filename)
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
#         if(len(self.mapWaterTimes) > 0):
#             vmin = -1
#             vmax = math.ceil(self.maxWater)
# #             vmax = 20
#             levels = 100
#             levelBoundaries = np.linspace(vmin, vmax, levels + 1)
#             waterTriangulation = Triangulation(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes, triangles=self.mapWaterTriangles, mask=self.mapWaterMaskedTriangles)
#             for index in range(len(self.mapWaterTimes)):
#                 fig, ax = plt.subplots()
#     #             print(self.endWavePointsLongitudes)
#     #             print(self.endWavePointsLatitudes)
#     #             print(self.endSWH)
#                 plt.imshow(img, extent=self.backgroundAxis, alpha=0.6, aspect=aspectRatio, zorder=2)
#                 contourset = ax.tripcolor(waterTriangulation, self.mapWaters[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
# #               Todo: Fix triangulation errors
# #                 contourset = ax.tripcolor(self.mapWaterPointsLongitudes, self.mapWaterPointsLatitudes, self.mapWaters[index], shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
#                 plt.axis(plotAxis)
#                 plt.title("Water Elevation")
#                 plt.xlabel(datetime.fromtimestamp(int(self.mapWaterTimes[index]),timezone.utc))
#     #             plt.gca().invert_yaxis()
#                 plt.colorbar(
#                     ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
#                     ticks=range(vmin, vmax+5, 2),
#                     boundaries=levelBoundaries,
#                     values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
#                     label="Meters",
#                     ax=plt.gca()
#                 )
#                 plt.savefig(graph_directory + 'map_water_' + str(index) + '.png')
#                 plt.close()
#                 gc.collect()
#             with imageio.get_writer(graph_directory + 'water.gif', mode='I') as writer:
#                 for index in range(len(self.mapWaterTimes)):
#                     filename = "map_water_" + str(index) + ".png"
#                     image = imageio.imread(graph_directory + filename)
#                     writer.append_data(image)
#                 for index in range(len(self.mapWaterTimes)):
#                     filename = "map_water_" + str(index) + ".png"
#                     os.remove(graph_directory + filename)
#             mapWatersNoNan = np.nan_to_num(self.mapWaters)
#             swathWaters = np.max(self.mapWaters, axis=0)
#             fig, ax = plt.subplots()
#             plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
#             contourset = ax.tripcolor(waterTriangulation, swathWaters, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
#             plt.axis(plotAxis)
#             plt.title("Water Swath")
# #             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
# #             graphs up to 10 m/s, ~20 knots
#             plt.colorbar(
#                 ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
#                 ticks=range(vmin, vmax+5, 2),
#                 boundaries=levelBoundaries,
#                 values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
#                 label="Meters",
#                 ax=plt.gca()
#             )
#             plt.savefig(graph_directory + 'map_water_swath.png')
#             plt.close()
#             gc.collect()
#         if(len(self.mapWaveTimes) > 0):
#             vmin = 0
#             vmax = math.ceil(self.maxWave)
#             levels = 100
#             levelBoundaries = np.linspace(vmin, vmax, levels + 1)
#             waveTriangulation = Triangulation(self.mapWavePointsLongitudes, self.mapWavePointsLatitudes, triangles=self.mapWaveTriangles, mask=self.mapWaveMaskedTriangles)
#             for index in range(len(self.mapWaveTimes)):
#                 fig, ax = plt.subplots()
#     #             print(self.endWavePointsLongitudes)
#     #             print(self.endWavePointsLatitudes)
#     #             print(self.endSWH)
#                 plt.imshow(img, extent=self.backgroundAxis, aspect=aspectRatio)
#                 contourset = ax.tricontourf(waveTriangulation, self.mapSWH[index], levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax)
#                 plt.axis(plotAxis)
#                 plt.title("Significant Wave Height")
#                 plt.xlabel(datetime.fromtimestamp(int(self.mapWaveTimes[index]),timezone.utc))
#     #             plt.gca().invert_yaxis()
#                 plt.colorbar(
#                     ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
#                     ticks=range(vmin, vmax+5, 5),
#                     boundaries=levelBoundaries,
#                     values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
#                     label="Meters",
#                     ax=plt.gca()
#                 )                
#                 plt.savefig(graph_directory + 'map_swh_' + str(index) + '.png')
#                 plt.close()
#                 gc.collect()
#             with imageio.get_writer(graph_directory + 'wave.gif', mode='I') as writer:
#                 for index in range(len(self.mapWaveTimes)):
#                     filename = "map_swh_" + str(index) + ".png"
#                     image = imageio.imread(graph_directory + filename)
#                     writer.append_data(image)
#                 for index in range(len(self.mapWaveTimes)):
#                     filename = "map_swh_" + str(index) + ".png"
#                     os.remove(graph_directory + filename)
#             mapSWHNoNan = np.nan_to_num(self.mapSWH)
#             swathSWH = np.max(mapSWHNoNan, axis=0)
#             fig, ax = plt.subplots()
#             plt.imshow(img, alpha=0.5, extent=self.backgroundAxis, aspect=aspectRatio, zorder=2)
#             contourset = ax.tricontourf(waveTriangulation, swathSWH, levelBoundaries, alpha=0.5, vmin=vmin, vmax=vmax)
#             plt.axis(plotAxis)
#             plt.title("Wave Significant Wave Height Swath")
# #             plt.xlabel(datetime.fromtimestamp(int(self.mapWindTimes[index]), timezone.utc))
# #             graphs up to 10 m/s, ~20 knots
#             plt.colorbar(
#                 ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
#                 ticks=range(vmin, vmax+5, 5),
#                 boundaries=levelBoundaries,
#                 values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
#                 label="Meters",
#                 ax=plt.gca()
#             )        
#             plt.savefig(graph_directory + 'map_swh_swath.png')
#             plt.close()
#             gc.collect()
        # Plot wind speed over time
        for index in range(numberOfWindDatapoints):
            if(len(self.datapointsSpeeds) > 0):
                fig, ax = plt.subplots()
                ax.scatter(self.windTimes, self.datapointsSpeeds[index], marker=".", label="Forecast")
                if(self.obsExists):
                    ax.scatter(self.obsDatapointsTimes[index], self.obsDatapointsSpeeds[index], marker=".", label="Obs")
                ax.legend(loc="lower right")
                stationName = self.obsLabels[index]
                plt.title(stationName + " station wind speed")
                plt.xlabel("Hours since " + self.windStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("wind speed (m/s)")
                plt.savefig(graph_directory + stationName + '_wind_speed.png')
                plt.close()
            if(len(self.datapointsDirections) > 0):
                fig, ax = plt.subplots()
                ax.scatter(self.windTimes, self.datapointsDirections[index], marker=".", label="Forecast")
                if(self.obsExists):
                    ax.scatter(self.obsDatapointsTimes[index], self.obsDatapointsDirections[index], marker=".", label="Obs")
                ax.legend(loc="lower right")
                stationName = self.obsLabels[index]
                plt.title(stationName + " station wind directions")
                plt.xlabel("Hours since " + self.windStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("wind direction (degrees)")
                plt.savefig(graph_directory + stationName + '_wind_direction.png')
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
        for index in range(numberOfWaterDatapoints):
            if(len(self.datapointsWaters) > 0):
                fig, ax = plt.subplots(figsize=(16,9))
                ax.plot(self.waterTimes, self.datapointsWaters[index], label="Forecast")
                if(self.tideExists):
                    ax.plot(self.tideDatapointsTimes[index], self.tideDatapointsWaters[index], label="Station")
                    ax.plot(self.tideDatapointsPredictionTimes[index], self.tideDatapointsPredictionWaters[index], label="Prediction")
                ax.legend(loc="upper left")
                stationName = self.tideLabels[index]
                plt.title(stationName + " station water elevation")
                plt.xlabel("Hours since " + self.waterStartDate.strftime(self.DATE_FORMAT))
                plt.ylabel("elevation (meters)")
                plt.savefig(graph_directory + stationName + '_water.png')
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
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsSWH[index], marker=".", label="Forecast")
                    if(self.buoyExists):
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsSWH[index], label="Buoy")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station significant wave height")
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
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
                    plt.title(stationName + " station mean wave direction")
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
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
                    plt.title(stationName + " station mean wave period")
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("MWP (seconds)")
                    plt.savefig(graph_directory + stationName + '_wave_mwp.png')
                    plt.close()
                if(len(self.datapointsPWP[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsPWP[index], marker=".", label="Forecast")
                    if(self.buoyExists):
                        ax.scatter(self.buoyDatapointsTimes[index], self.buoyDatapointsPWP[index], label="Buoy")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station peak wave period")
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("PWP (seconds)")
                    plt.savefig(graph_directory + stationName + '_wave_pwp.png')
                    plt.close()
                if(len(self.datapointsRADMag[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsRADMag[index], marker=".", label="Forecast")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station radiation stress magnitude")
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("Rad Stress Magitude (1/m^2s^2)")
                    plt.savefig(graph_directory + stationName + '_wave_radstress_mag.png')
                    plt.close()
                if(len(self.datapointsRADDir[index]) > 0):
                    fig, ax = plt.subplots()
                    ax.scatter(self.waveTimes, self.datapointsRADDir[index], marker=".", label="Forecast")
                    ax.legend(loc="lower right")
                    stationName = self.buoyLabels[index]
                    plt.title(stationName + " station radiation stress direction")
                    plt.xlabel("Hours since " + self.waveStartDate.strftime(self.DATE_FORMAT))
                    plt.ylabel("Rad stress direction (degrees)")
                    plt.savefig(graph_directory + stationName + '_wave_radstress_dir.png')
                    plt.close()
                
           
