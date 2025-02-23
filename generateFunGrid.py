import requests
import numpy as np
import utm
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.cm import ScalarMappable
from scipy.interpolate import RegularGridInterpolator
import haversine

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

NAPATREE_MAP = "Napatree.png"
NAPATREE_AXIS = [-71.88687460327148, -71.86112539672851, 41.31967002720852, 41.30032853828529]

# backgroundMap = CHARLESTOWN_MAP
# backgroundAxis = CHARLESTOWN_AXIS
# backgroundMap = RHODE_ISLAND_CHAMP_MAP
# backgroundAxis = RHODE_ISLAND_CHAMP_AXIS
backgroundMap = NAPATREE_MAP
backgroundAxis = NAPATREE_AXIS

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

def InterpolatePoint(grid, Y, X, point, method='linear'):
    """
    Interpolate the elevation at a given point from a grid, considering masked values.

    Parameters:
    - grid: np.ma.array representing elevation data with masked values
    - Y: np.array of y coordinates for the grid
    - X: np.array of x coordinates for the grid
    - point: tuple (x, y) representing the coordinates to interpolate
    - method: str, interpolation method ('linear', 'nearest', 'cubic', etc.)

    Returns:
    - float: Interpolated elevation value at the point or np.nan if interpolation fails
    """
    if grid.size == 0:
        return np.nan  # Return NaN for empty grid

    # Convert grid to masked array if not already
    grid = np.ma.masked_invalid(grid) if not np.ma.is_masked(grid) else grid

    # Create interpolator
    interpolator = RegularGridInterpolator((Y, X), grid, method=method, bounds_error=False, fill_value=None)

    # Interpolate the point
    try:
        value = interpolator(np.flip(point))  # Flip point since RegularGridInterpolator expects (y,x)
        # If the interpolated value is masked, return NaN
        return value if not np.ma.is_masked(value) else np.nan
    except ValueError:
        return np.nan  # Return NaN if interpolation fails

def calculateAverageSlope(bathymetryValues, bathymetryY, bathymetryX, elevationValues, elevationY, elevationX, offshorePoint, shorelinePoint):
    """
    Calculate the average slope between an offshore point and a shoreline point using bathymetry and elevation data,
    considering masked values.

    Parameters:
    - bathymetryValues: np.ma.array representing elevation data for the bathymetry grid
    - bathymetryY, bathymetryX: np.array of y and x coordinates for the bathymetry grid
    - elevationValues: np.ma.array representing elevation data for the elevation grid
    - elevationY, elevationX: np.array of y and x coordinates for the elevation grid
    - offshorePoint: tuple (x1, y1) representing coordinates of the offshore point 
    - shorelinePoint: tuple (x2, y2) representing coordinates of the shoreline point 

    Returns:
    - float: The average slope between the two points in degrees or None if interpolation fails
    """

    try:
        z_offshore = InterpolatePoint(bathymetryValues, bathymetryY, bathymetryX, offshorePoint)
        z_shoreline = InterpolatePoint(elevationValues, elevationY, elevationX, shorelinePoint)

        if np.isnan(z_offshore) or np.isnan(z_shoreline):
            return None  # If either value can't be interpolated, return None

        # Calculate horizontal distance
        horizontalDistance = haversine.haversine(offshorePoint, shorelinePoint, unit='km') * 1000

        # Vertical distance
        dz = abs(z_shoreline - z_offshore)

        # Calculate slope in radians
        if horizontalDistance > 0:
            slopeRadians = np.arctan2(dz, horizontalDistance)
            return np.degrees(slopeRadians)
        else:
            return 0  # Slope is undefined, return 0

    except Exception as e:
        print(f"An error occurred: {e}")
        return None


# northCorner = 41.3584
# southCorner = 41.3474
# eastCorner = -71.62847
# westCorner = -71.63847
northCorner = 41.3584
southCorner = 41.3384
eastCorner = -71.62847
westCorner = -71.64847

northCornerBathymetry = 41.5384
southCornerBathymetry = 41.0
eastCornerBathymetry = -71.42847
westCornerBathymetry = -71.64847

# NAPATREE bounds
northCorner = 41.32
southCorner = 41.29
eastCorner = -71.85
westCorner = -71.90

northCornerBathymetry = 41.32
southCornerBathymetry = 41.29
eastCornerBathymetry = -71.85
westCornerBathymetry = -71.90
downloadElevationData(northCorner, southCorner, eastCorner, westCorner)
downloadBathymetryData(northCornerBathymetry, southCornerBathymetry, eastCornerBathymetry, westCornerBathymetry)
# elevationData[0:1]

elevationValues = []
latitudes = []
longitudes = []
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
    minLatitude, minLongitude = utm.to_latlon(minLongitude, minLatitude, 19, "N")
    maxLatitude, maxLongitude = utm.to_latlon(maxLongitude, maxLatitude, 19, "N")

    longitudes.extend(np.linspace(minLongitude, maxLongitude, longitudeDelta))
    latitudes.extend(np.linspace(maxLatitude, minLatitude, latitudeDelta))
    print(longitudes, latitudes)
    
    for line in lines[6::]:
        data = np.array(line.split(), dtype=float)
        elevationValues.append(data)
    print(longitudeDelta, latitudeDelta, minLongitude, minLatitude, coordinateDelta, noDataValue)
    elevationValues = np.ma.masked_equal(elevationValues, noDataValue)
    elevationValues = np.ma.masked_equal(elevationValues, waterValue)
#     quit()
    
bathymetryValues = []
bathymetryLatitudes = []
bathymetryLongitudes = []
with open(BATHYMETRY_FILE, 'r') as file:
    lines = file.readlines()
    longitudeDelta = int(lines[0][13::].strip())
    latitudeDelta = int(lines[1][13::].strip())
    minLongitude = float(lines[2][13::].strip())
    minLatitude = float(lines[3][13::].strip())
    coordinateDelta = float(lines[4][13::].strip())
    noDataValue = float(lines[5][13::].strip())
#     waterValue = -2.0999999046325683594

    maxLongitude = minLongitude + (longitudeDelta * coordinateDelta)
    maxLatitude = minLatitude + (latitudeDelta * coordinateDelta)
    print(maxLongitude, maxLatitude)
    #     If using 1m USGS DEM, have to convert coordinates from universal transverse mercator to lat lon
#     minLatitude, minLongitude = utm.to_latlon(minLongitude, minLatitude, 19, "N")
#     maxLatitude, maxLongitude = utm.to_latlon(maxLongitude, maxLatitude, 19, "N")

    bathymetryLongitudes.extend(np.linspace(minLongitude, maxLongitude, longitudeDelta))
    bathymetryLatitudes.extend(np.linspace(maxLatitude, minLatitude, latitudeDelta))
    print(bathymetryLongitudes, bathymetryLatitudes)
#     quit()
    for line in lines[6::]:
        data = np.array(line.split(), dtype=float)
        bathymetryValues.append(data)
    print(longitudeDelta, latitudeDelta, minLongitude, minLatitude, coordinateDelta, noDataValue)
    bathymetryValues = np.ma.masked_equal(bathymetryValues, noDataValue)
#     elevationValues = np.ma.masked_equal(elevationValues, waterValue)
    
    
offshorePoint = (-71.64, 41.35)
shorelinePoint = (-71.64081, 41.35584)
# Napatree bathymetry 10m to 20m
offshorePoint = (-71.876028, 41.308975)
shorelinePoint = (-71.876089, 41.309053)
offshoreElevation = InterpolatePoint(bathymetryValues, bathymetryLatitudes, bathymetryLongitudes, offshorePoint)
print(offshoreElevation)
# quit()

shorelineElevation = InterpolatePoint(bathymetryValues, bathymetryLatitudes, bathymetryLongitudes, offshorePoint)
averageSlope = calculateAverageSlope(np.array(bathymetryValues), np.array(bathymetryLatitudes), np.array(bathymetryLongitudes), np.array(elevationValues), np.array(latitudes), np.array(longitudes), offshorePoint, shorelinePoint)
print("AVERAGE SLOPE:", averageSlope)


# quit()
vmin = 0
vmax = 5
levels = 10
levelBoundaries = np.linspace(vmin, vmax, levels + 1)

vminBathymetry = -10
vmaxBathymetry = 10
levelsBathymetry = 10
levelBoundariesBathymetry = np.linspace(vminBathymetry, vmaxBathymetry, levelsBathymetry + 1)

img = mpimg.imread(backgroundMap)
plotAxis = [backgroundAxis[0], backgroundAxis[1], backgroundAxis[3], backgroundAxis[2]]
aspectRatio = (backgroundAxis[1] - backgroundAxis[0]) / (backgroundAxis[2] - backgroundAxis[3])
fig, ax = plt.subplots()
plt.imshow(img, alpha=0.5, extent=backgroundAxis, aspect=aspectRatio, zorder=3)
contourset = ax.pcolormesh(longitudes, latitudes, elevationValues, shading='gouraud', cmap="jet", vmin=vmin, vmax=vmax, zorder=2)
contourset2 = ax.pcolormesh(bathymetryLongitudes, bathymetryLatitudes, bathymetryValues, shading='gouraud', cmap="viridis", vmin=vminBathymetry, vmax=vmaxBathymetry, zorder=1)
ax.scatter(offshorePoint[0], offshorePoint[1], label="offshore", zorder=4)
ax.scatter(shorelinePoint[0], shorelinePoint[1], label="shoreline", zorder=4)
# ax.legend()
plt.axis(plotAxis)
plt.colorbar(
    ScalarMappable(norm=contourset.norm, cmap=contourset.cmap),
    ticks=range(vmin, vmax+5, 1),
    boundaries=levelBoundaries,
    values=(levelBoundaries[:-1] + levelBoundaries[1:]) / 2,
    label="Meters",
    ax=plt.gca()
)
plt.colorbar(
    ScalarMappable(norm=contourset2.norm, cmap=contourset2.cmap),
    ticks=range(vminBathymetry, vmaxBathymetry+5, 5),
    boundaries=levelBoundariesBathymetry,
    values=(levelBoundariesBathymetry[:-1] + levelBoundariesBathymetry[1:]) / 2,
    label="Meters",
    ax=plt.gca()
)
plt.show()
plt.close()
#     for line in lines:
#         print(line)