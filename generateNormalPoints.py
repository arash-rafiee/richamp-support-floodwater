import json
import copy
import math
import datetime

HYPERRESOLUTION = 1
HYPERPOINTS = 120
DEEPLINE_DISTANCE = 2200  # Default single distance
MIN_SLOPELINE_DISTANCE = 200  # Minimum slopeline distance in meters
MAX_SLOPELINE_DISTANCE = 1000   # Maximum slopeline distance in meters
SLOPELINE_DELIM_DISTANCE = 100  # Delimitation distance for slope points in meters
DEEPLINE_DISTANCES = [
    1000, 2200, 3500, 5500, 7500, 9500, 10500, 12500, 14500, 16500,
    18500, 20500, 22500, 24500, 26500, 28500, 29500, 31500, 33500, 35500,
    37500, 39500, 41500
]

# Deeplines for 7m depth (Minimum)
MIN_DEEPLINE_DISTANCE_1 = 700   # For Napatree1 (runup_id: 10)
MIN_DEEPLINE_DISTANCE_2 = 550   # For Napatree2 (runup_id: 20)
MIN_DEEPLINE_DISTANCE_3 = 400   # For Napatree3 (runup_id: 30)
MIN_DEEPLINE_DISTANCE_4 = 400   # For Napatree4 (runup_id: 40)
MIN_DEEPLINE_DISTANCE_5 = 550   # For Napatree5 (runup_id: 50)

# Deeplines for 20m depth (Maximum)
MAX_DEEPLINE_DISTANCE_1 = 2315  # For Napatree1 (runup_id: 10)
MAX_DEEPLINE_DISTANCE_2 = 2260  # For Napatree2 (runup_id: 20)
MAX_DEEPLINE_DISTANCE_3 = 2230  # For Napatree3 (runup_id: 30)
MAX_DEEPLINE_DISTANCE_4 = 2200  # For Napatree4 (runup_id: 40)
MAX_DEEPLINE_DISTANCE_5 = 2170  # For Napatree5 (runup_id: 50)

# Dictionary to map runup_id to deepline distances
MIN_DEEPLINE_DISTANCE_MAP = {
    '10': MIN_DEEPLINE_DISTANCE_1,
    '20': MIN_DEEPLINE_DISTANCE_2,
    '30': MIN_DEEPLINE_DISTANCE_3,
    '40': MIN_DEEPLINE_DISTANCE_4,
    '50': MIN_DEEPLINE_DISTANCE_5
}

MAX_DEEPLINE_DISTANCE_MAP = {
    '10': MAX_DEEPLINE_DISTANCE_1,
    '20': MAX_DEEPLINE_DISTANCE_2,
    '30': MAX_DEEPLINE_DISTANCE_3,
    '40': MAX_DEEPLINE_DISTANCE_4,
    '50': MAX_DEEPLINE_DISTANCE_5
}

# Function to convert GMT datetime string to Unix timestamp
def datetime_to_timestamp(dt_str):
    """Convert a GMT datetime string (YYYY-MM-DD HH:MM:SS) to Unix timestamp."""
    dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=datetime.timezone.utc)  # Ensure GMT/UTC
    return int(dt.timestamp())

# Station-specific dune heights with GMT datetime strings
DUNE_HEIGHTS_1 = [
    {"datetime": "2023-12-13 04:00:00", "height": 6.04},
    {"datetime": "2023-12-19 04:00:00", "height": 6.08}
]
DUNE_HEIGHTS_2 = [
    {"datetime": "2023-12-13 04:00:00", "height": 4.81},
    {"datetime": "2023-12-19 04:00:00", "height": 4.82}
]
DUNE_HEIGHTS_3 = [
    {"datetime": "2023-12-13 04:00:00", "height": 4.02},
    {"datetime": "2023-12-19 04:00:00", "height": 4.06}
]
DUNE_HEIGHTS_4 = [
    {"datetime": "2023-12-13 04:00:00", "height": 3.89},
    {"datetime": "2023-12-19 04:00:00", "height": 3.92}
]
DUNE_HEIGHTS_5 = [
    {"datetime": "2023-12-13 04:00:00", "height": 3.32},
    {"datetime": "2023-12-19 04:00:00", "height": 3.22}
]

# Convert datetime strings to timestamps for JSON output
DUNE_HEIGHTS_1 = [{"timestamp": datetime_to_timestamp(h["datetime"]), "height": h["height"]} for h in DUNE_HEIGHTS_1]
DUNE_HEIGHTS_2 = [{"timestamp": datetime_to_timestamp(h["datetime"]), "height": h["height"]} for h in DUNE_HEIGHTS_2]
DUNE_HEIGHTS_3 = [{"timestamp": datetime_to_timestamp(h["datetime"]), "height": h["height"]} for h in DUNE_HEIGHTS_3]
DUNE_HEIGHTS_4 = [{"timestamp": datetime_to_timestamp(h["datetime"]), "height": h["height"]} for h in DUNE_HEIGHTS_4]
DUNE_HEIGHTS_5 = [{"timestamp": datetime_to_timestamp(h["datetime"]), "height": h["height"]} for h in DUNE_HEIGHTS_5]

# Dictionary to map runup_id to dune heights
DUNE_HEIGHTS_MAP = {
    '10': DUNE_HEIGHTS_1,
    '20': DUNE_HEIGHTS_2,
    '30': DUNE_HEIGHTS_3,
    '40': DUNE_HEIGHTS_4,
    '50': DUNE_HEIGHTS_5
}

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    dLon = lon2_rad - lon1_rad
    y = math.sin(dLon) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dLon)
    bearing = math.atan2(y, x)
    return bearing

def calculate_new_point(lat, lon, bearing, distance):
    R = 6371e3
    lat_rad, lon_rad = math.radians(lat), math.radians(lon)
    bearing_rad = bearing
    new_lat = math.asin(math.sin(lat_rad) * math.cos(distance / R) + 
                       math.cos(lat_rad) * math.sin(distance / R) * math.cos(bearing_rad))
    new_lon = lon_rad + math.atan2(math.sin(bearing_rad) * math.sin(distance / R) * math.cos(lat_rad),
                                  math.cos(distance / R) - math.sin(lat_rad) * math.sin(new_lat))
    return math.degrees(new_lat), math.degrees(new_lon)

def generate_points_along_line(json_data, resolution=HYPERRESOLUTION, points_count=HYPERPOINTS):
    if 'NORMAL' not in json_data:
        json_data['NORMAL'] = {}
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        json_data['NORMAL'][runup_id] = {}
        point_counter = 0
        for i in range(-points_count // 4, 3 * points_count // 4 + 1):
            distance = i * resolution
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} {distance:.3f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{runup_id}{point_counter:03d}"
            json_data['NORMAL'][runup_id][new_key] = new_point
            for section in ['ASSET', 'NOS']:
                json_data[section][new_key] = new_point
            point_counter += 1

def generate_slopeline_points(json_data, distance=MAX_SLOPELINE_DISTANCE):
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        # Calculate slopeline coordinates
        new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
        
        # Create unique key for slopeline
        slopeline_key = f"{runup_id}s"
        
        # Add slopelineKey and coordinates to RUNUP section
        runup_data['slopelineKey'] = slopeline_key
        runup_data['slopeLatitude'] = f"{new_lat:.6f}"
        runup_data['slopeLongitude'] = f"{new_lon:.6f}"
        
        # Create slopeline point
        slopeline_point = {
            "id": "RUNUP",
            "source": "RUNUP",
            "name": f"{runup_data['name']} Slopeline",
            "latitude": f"{new_lat:.6f}",
            "longitude": f"{new_lon:.6f}"
        }
        
        # Add to ASSET section
        json_data['ASSET'][slopeline_key] = slopeline_point

def generate_slope_stations(json_data, min_distance=MIN_SLOPELINE_DISTANCE, max_distance=MAX_SLOPELINE_DISTANCE, delim_distance=SLOPELINE_DELIM_DISTANCE):
    # Create a copy of the data to avoid modifying the original
    slope_data = copy.deepcopy(json_data)
    
    # Clear existing ASSET entries once before processing all transects
    slope_data['ASSET'] = {}
    
    for runup_id, runup_data in slope_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        # Generate points at delim_distance intervals from min_distance to max_distance
        point_counter = 0
        distance = min_distance
        while distance <= max_distance:
            # Adjust bearing for negative distances (opposite direction)
            adjusted_bearing = bearing + math.pi if distance < 0 else bearing
            adjusted_distance = abs(distance)  # Use absolute distance for calculation
            
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, adjusted_bearing, adjusted_distance)
            new_key = f"{runup_id}sl{point_counter:03d}"
            
            slope_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "name": f"{runup_data['name']} Slope {distance:.1f}m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            
            slope_data['ASSET'][new_key] = slope_point
            distance += delim_distance
            point_counter += 1
    
    return slope_data


def generate_deepline_points(json_data):
    new_runup = {}
    
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        # Get station-specific min and max deepline distances and dune heights
        station_id = runup_id.rstrip('mM')
        min_distance = MIN_DEEPLINE_DISTANCE_MAP.get(station_id, MIN_DEEPLINE_DISTANCE_1)
        max_distance = MAX_DEEPLINE_DISTANCE_MAP.get(station_id, MAX_DEEPLINE_DISTANCE_1)
        dune_heights = DUNE_HEIGHTS_MAP.get(station_id, DUNE_HEIGHTS_1)
        
        # Create RUNUP entry for minimum deepline
        min_key = f"{runup_id}m"
        min_deepline_key = runup_data['deeplineKey'] + 'm'
        new_runup[min_key] = runup_data.copy()
        new_runup[min_key]['deeplineKey'] = min_deepline_key
        new_runup[min_key]['name'] = f"{runup_data['name']} 7m Depth Waves"
        new_runup[min_key]['duneHeights'] = dune_heights
        
        min_lat, min_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, min_distance)
        new_runup[min_key]['deeplineLatitude'] = f"{min_lat:.6f}"
        new_runup[min_key]['deeplineLongitude'] = f"{min_lon:.6f}"
        
        min_deepline_point = {
            "id": "RUNUP",
            "source": "RUNUP",
            "name": f"{runup_data['name']} 7m Depth Waves",
            "latitude": f"{min_lat:.6f}",
            "longitude": f"{min_lon:.6f}"
        }
        
        # Create RUNUP entry for maximum deepline
        max_key = f"{runup_id}M"
        max_deepline_key = runup_data['deeplineKey'] + 'M'
        new_runup[max_key] = runup_data.copy()
        new_runup[max_key]['deeplineKey'] = max_deepline_key
        new_runup[max_key]['name'] = f"{runup_data['name']} 20m Depth Waves"
        new_runup[max_key]['duneHeights'] = dune_heights
        
        max_lat, max_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, max_distance)
        new_runup[max_key]['deeplineLatitude'] = f"{max_lat:.6f}"
        new_runup[max_key]['deeplineLongitude'] = f"{max_lon:.6f}"
        
        max_deepline_point = {
            "id": "RUNUP",
            "source": "RUNUP",
            "name": f"{runup_data['name']} 20m Depth Waves",
            "latitude": f"{max_lat:.6f}",
            "longitude": f"{max_lon:.6f}"
        }
        
        # Update ASSET, NDBC, NOS sections for both min and max deepline points
        for section in ['ASSET', 'NDBC', 'NOS']:
            if runup_data['deeplineKey'] in json_data[section]:
                json_data[section][min_deepline_key] = min_deepline_point
                json_data[section][max_deepline_key] = max_deepline_point
                if runup_data['deeplineKey'] in json_data[section]:
                    del json_data[section][runup_data['deeplineKey']]
    
    # Replace RUNUP section with new entries
    json_data['RUNUP'] = new_runup

def generate_multiple_deepline_points(json_data, distances=DEEPLINE_DISTANCES):
    if 'NORMAL' not in json_data:
        json_data['NORMAL'] = {}
    if 'TANGENT' not in json_data:
        json_data['TANGENT'] = {}
    new_runup = {}
    
    for orig_runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        tangent_lat, tangent_lon = float(runup_data['tangentLatitude']), float(runup_data['tangentLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        # Get station-specific dune heights
        station_id = orig_runup_id.rstrip('mM')
        dune_heights = DUNE_HEIGHTS_MAP.get(station_id, DUNE_HEIGHTS_1)
        
        # Generate NORMAL points
        json_data['NORMAL'][orig_runup_id] = {}
        point_counter = 0
        for i in range(-HYPERPOINTS // 4, 3 * HYPERPOINTS // 4 + 1):
            distance = i * HYPERRESOLUTION
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} {distance:.3f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{orig_runup_id}{point_counter:03d}"
            json_data['NORMAL'][orig_runup_id][new_key] = new_point
            for section in ['ASSET', 'NOS']:
                json_data[section][new_key] = new_point
            point_counter += 1
        
        # Generate TANGENT points
        json_data['TANGENT'][station_id] = {}
        point_counter = 0
        for i in range(-HYPERPOINTS // 4, 3 * HYPERPOINTS // 4 + 1):
            distance = i * HYPERRESOLUTION
            new_lat, new_lon = calculate_new_point(tangent_lat, tangent_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name'].replace(' 7m Depth Waves', '').replace(' 20m Depth Waves', '')} Tangent {distance:.3f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{station_id}{point_counter:03d}"
            json_data['TANGENT'][station_id][new_key] = new_point
            point_counter += 1
        
        # Generate multiple deepline points
        for idx, distance in enumerate(distances):
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
            new_key = f"{orig_runup_id}d{idx:02d}"
            
            new_runup[new_key] = {
                "id": "RUNUP",
                "source": "RUNUP",
                "surfKey": runup_data['surfKey'],
                "offshoreKey": runup_data['offshoreKey'],
                "deeplineKey": new_key,
                "name": f"{runup_data['name'].replace(' 7m Depth Waves', '').replace(' 20m Depth Waves', '')} Deepline {distance}m",
                "latitude": runup_data['latitude'],
                "longitude": runup_data['longitude'],
                "tangentLatitude": runup_data['tangentLatitude'],
                "tangentLongitude": runup_data['tangentLongitude'],
                "surfLatitude": runup_data['surfLatitude'],
                "surfLongitude": runup_data['surfLongitude'],
                "offshoreLatitude": runup_data['offshoreLatitude'],
                "offshoreLongitude": runup_data['offshoreLongitude'],
                "deeplineLatitude": f"{new_lat:.6f}",
                "deeplineLongitude": f"{new_lon:.6f}",
                "duneHeights": dune_heights
            }
            
            deepline_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "name": f"{runup_data['name'].replace(' 7m Depth Waves', '').replace(' 20m Depth Waves', '')} Deepline {distance}m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            for section in ['ASSET', 'NDBC', 'NOS']:
                json_data[section][new_key] = deepline_point
    
    json_data['RUNUP'] = new_runup

def offshore_generate_points_along_line(json_data, resolution=200, points_count=15):
    point_counter = 0
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        for i in range(points_count):
            distance = i * resolution
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} {distance:.1f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{runup_id}{point_counter:03d}"
            for section in ['ASSET', 'NDBC', 'NOS']:
                json_data[section][new_key] = new_point
            point_counter += 1

def generate_tangent_points(json_data, resolution=HYPERRESOLUTION, points_count=HYPERPOINTS):
    if 'TANGENT' not in json_data:
        json_data['TANGENT'] = {}
    
    # Group by original station IDs (strip 'm' or 'M' from runup_id)
    for runup_id, runup_data in json_data['RUNUP'].items():
        # Extract original station ID (e.g., '40' from '40m' or '40M')
        station_id = runup_id.rstrip('mM')
        
        # Only process one entry per station (skip duplicates)
        if station_id in json_data['TANGENT']:
            continue
        
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        tangent_lat, tangent_lon = float(runup_data['tangentLatitude']), float(runup_data['tangentLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        json_data['TANGENT'][station_id] = {}
        point_counter = 0
        
        for i in range(-points_count // 4, 3 * points_count // 4 + 1):
            distance = i * resolution
            new_lat, new_lon = calculate_new_point(tangent_lat, tangent_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name'].replace(' 7m Depth Waves', '').replace(' 20m Depth Waves', '')} Tangent {distance:.3f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{station_id}{point_counter:03d}"
            json_data['TANGENT'][station_id][new_key] = new_point
            point_counter += 1

# NORMAL stations
with open('RUNUP_NAPATREE_STATIONS.json', 'r') as file:
    data_normal = json.load(file)
generate_points_along_line(data_normal)
generate_deepline_points(data_normal)
generate_slopeline_points(data_normal, distance=MAX_SLOPELINE_DISTANCE)
generate_tangent_points(data_normal)
with open('NAPATREE_NORMAL_STATIONS.json', 'w') as file:
    json.dump(data_normal, file, indent=2)

# LONG stations
with open('RUNUP_NAPATREE_STATIONS.json', 'r') as file:
    data_long = json.load(file)
offshore_generate_points_along_line(data_long)
generate_deepline_points(data_long)
with open('NAPATREE_LONG_STATIONS.json', 'w') as file:
    json.dump(data_long, file, indent=2)

# DEEP stations
with open('RUNUP_NAPATREE_STATIONS.json', 'r') as file:
    data_deep = json.load(file)
generate_multiple_deepline_points(data_deep)
with open('NAPATREE_DEEP_STATIONS.json', 'w') as file:
    json.dump(data_deep, file, indent=2)

# SLOPE stations
with open('RUNUP_NAPATREE_STATIONS.json', 'r') as file:
    data_slope = json.load(file)
slope_data = generate_slope_stations(data_slope)
with open('NAPATREE_SLOPE_STATIONS.json', 'w') as file:
    json.dump(slope_data, file, indent=2)