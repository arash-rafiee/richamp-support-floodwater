import requests
import numpy as np
import utm
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.cm import ScalarMappable

QUERY_URL = "https://portal.opentopography.org/API/otCatalog"
BASE_URL = "https://portal.opentopography.org/API/globaldem"
USGS_URL = "https://portal.opentopography.org/API/usgsdem"
API_KEY = "6dd04fe1048e9dfbfc6652feb1b733b1"
TOPOGRAPHY_FILE = "topography.txt"
BATHYMETRY_FILE = "bathymetry.txt"

RHODE_ISLAND_CHAMP_MAP = "RhodeIslandChamp.png"
RHODE_ISLAND_CHAMP_AXIS = [-71.9050164752, -71.1307245329, 42.000010143316864, 41.1192500979]
CHARLESTOWN_MAP = "Charlestown.png"
CHARLESTOWN_OUTLINE_MAP = "CharlestownOutline.png"
CHARLESTOWN_AXIS = [-71.67074920654298, -71.61925079345704, 41.38432229342453, 41.34567196713463]

backgroundMap = CHARLESTOWN_MAP
backgroundAxis = CHARLESTOWN_AXIS
backgroundMap = RHODE_ISLAND_CHAMP_MAP
backgroundAxis = RHODE_ISLAND_CHAMP_AXIS

def downloadBathymetryData(north, south, east, west, dem_type="GEBCOIceTopo", output_format="AAIGrid", api_key=API_KEY):
    params = {
        "demtype": dem_type,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": output_format
    }
    if api_key:
        params["API_Key"] = api_key

    response = requests.get(BASE_URL, params=params)
    if response.status_code != 200:
        raise Exception(f"Failed to download data: {response.status_code}")

    with open(BATHYMETRY_FILE, "wb") as file:
        file.write(response.content)

# Function to download elevation data
def downloadElevationData(north, south, east, west, dataset_name="USGS1m", output_format="AAIGrid", api_key=API_KEY):
    params = {
        "datasetName": dataset_name,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": output_format
    }
    if api_key:
        params["API_Key"] = api_key

    response = requests.get(USGS_URL, params=params)
    if response.status_code != 200:
        raise Exception(f"Failed to download data: {response.status_code}")
    
    with open(TOPOGRAPHY_FILE, "wb") as file:
        file.write(response.content)
        
# northCorner = 41.3584
# southCorner = 41.3474
# eastCorner = -71.62847
# westCorner = -71.63847
# northCorner = 41.3584
# southCorner = 41.3384
# eastCorner = -71.62847
# westCorner = -71.64847

northCorner = 41.5384
southCorner = 41.0
eastCorner = -71.42847
westCorner = -71.64847
# downloadElevationData(northCorner, southCorner, eastCorner, westCorner)
downloadBathymetryData(northCorner, southCorner, eastCorner, westCorner)
# elevationData[0:1]

elevationValues = []
with open(TOPOGRAPHY_FILE, 'r') as file:
    lines = file.readlines()
    longitudeDelta = int(lines[0][13::].strip())
    latitudeDelta = int(lines[1][13::].strip())
    minLongitude = float(lines[2][13::].strip())
    minLatitude = float(lines[3][13::].strip())
    coordinateDelta = float(lines[4][13::].strip())
    noDataValue = float(lines[5][13::].strip())
    waterValue = -2.0999999046325683594

    maxLongitude = minLongitude + (longitudeDelta * coordinateDelta)
    maxLatitude = minLatitude + (latitudeDelta * coordinateDelta)
    
    #     If using 1m USGS DEM, have to convert coordinates from universal transverse mercator to lat lon
#     minLatitude, minLongitude = utm.to_latlon(minLongitude, minLatitude, 19, "N")
#     maxLatitude, maxLongitude = utm.to_latlon(maxLongitude, maxLatitude, 19, "N")

    longitudes = np.linspace(minLongitude, maxLongitude, longitudeDelta)
    latitudes = np.linspace(maxLatitude, minLatitude, latitudeDelta)
    print(longitudes, latitudes)
    
    for line in lines[6::]:
        data = np.array(line.split(), dtype=float)
        elevationValues.append(data)
    print(longitudeDelta, latitudeDelta, minLongitude, minLatitude, coordinateDelta, noDataValue)
    elevationValues = np.ma.masked_equal(elevationValues, noDataValue)
    elevationValues = np.ma.masked_equal(elevationValues, waterValue)
#     quit()
    
    
    
    vmin = -50
    vmax = 5
    levels = 10
    levelBoundaries = np.linspace(vmin, vmax, levels + 1)
    img = mpimg.imread(backgroundMap)
    plotAxis = [backgroundAxis[0], backgroundAxis[1], backgroundAxis[3], backgroundAxis[2]]
    aspectRatio = (backgroundAxis[1] - backgroundAxis[0]) / (backgroundAxis[2] - backgroundAxis[3])
    fig, ax = plt.subplots()
    plt.imshow(img, alpha=0.5, extent=backgroundAxis, aspect=aspectRatio, zorder=2)
    contourset = ax.pcolormesh(longitudes, latitudes, elevationValues, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=1)
    plt.axis(plotAxis)
    plt.colorbar(
        ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
        ticks=range(vmin, vmax+5, 5),
        boundaries=levelBoundaries,
        values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
        label="Meters/Second",
        ax=plt.gca()
    )
    plt.show()
    plt.close()
#     for line in lines:
#         print(line)