import json
import copy
import math

HYPERRESOLUTION = 1
HYPERPOINTS = 120
DEEPLINE_DISTANCE = 2200  # Default single distance
SLOPELINE_DISTANCE = -10  # Slopeline distance in meters
SLOPELINE_DELIM_DISTANCE = 1  # Delimitation distance for slope points in meters
DEEPLINE_DISTANCES = [
    1000, 2200, 3500, 5500, 7500, 9500, 10500, 12500, 14500, 16500,
    18500, 20500, 22500, 24500, 26500, 28500, 29500, 31500, 33500, 35500,
    37500, 39500, 41500
]

# Station-specific deepline distances
DEEPLINE_DISTANCE_1 = 2500  # For Napatree1 (runup_id: 10)
DEEPLINE_DISTANCE_2 = 2400  # For Napatree2 (runup_id: 20)
DEEPLINE_DISTANCE_3 = 2300  # For Napatree3 (runup_id: 30)
DEEPLINE_DISTANCE_4 = 2250  # For Napatree4 (runup_id: 40)
DEEPLINE_DISTANCE_5 = 2150  # For Napatree5 (runup_id: 50)

# Dictionary to map runup_id to deepline distance
DEEPLINE_DISTANCE_MAP = {
    '10': DEEPLINE_DISTANCE_1,
    '20': DEEPLINE_DISTANCE_2,
    '30': DEEPLINE_DISTANCE_3,
    '40': DEEPLINE_DISTANCE_4,
    '50': DEEPLINE_DISTANCE_5
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

def generate_slopeline_points(json_data, distance=SLOPELINE_DISTANCE):
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

def generate_slope_stations(json_data, max_distance=SLOPELINE_DISTANCE, delim_distance=SLOPELINE_DELIM_DISTANCE):
    # Create a copy of the data to avoid modifying the original
    slope_data = copy.deepcopy(json_data)
    
    # Clear existing ASSET entries once before processing all transects
    slope_data['ASSET'] = {}
    
    for runup_id, runup_data in slope_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        # Adjust bearing for negative max_distance (opposite direction)
        adjusted_bearing = bearing + math.pi if max_distance < 0 else bearing
        adjusted_max_distance = abs(max_distance)  # Use absolute distance for loop
        
        # Generate points at delim_distance intervals up to adjusted_max_distance
        point_counter = 0
        distance = 0
        while distance <= adjusted_max_distance:
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, adjusted_bearing, distance)
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
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        deepline_key = runup_data['deeplineKey']
        
        # Get station-specific deepline distance
        distance = DEEPLINE_DISTANCE_MAP.get(runup_id, DEEPLINE_DISTANCE_1)  # Fallback to DEEPLINE_DISTANCE_1 if runup_id not found
        
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
        deepline_point = {
            "id": "RUNUP",
            "source": "RUNUP",
            "name": f"{runup_data['name']} Deepline",
            "latitude": f"{new_lat:.6f}",
            "longitude": f"{new_lon:.6f}"
        }
        for section in ['ASSET', 'NDBC', 'NOS']:
            if deepline_key in json_data[section]:
                json_data[section][deepline_key] = deepline_point

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
        json_data['TANGENT'][orig_runup_id] = {}
        point_counter = 0
        for i in range(-HYPERPOINTS // 4, 3 * HYPERPOINTS // 4 + 1):
            distance = i * HYPERRESOLUTION
            new_lat, new_lon = calculate_new_point(tangent_lat, tangent_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} Tangent {distance:.3f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{orig_runup_id}{point_counter:03d}"
            json_data['TANGENT'][orig_runup_id][new_key] = new_point
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
                "name": f"{runup_data['name']} Deepline {distance}m",
                "latitude": runup_data['latitude'],
                "longitude": runup_data['longitude'],
                "tangentLatitude": runup_data['tangentLatitude'],
                "tangentLongitude": runup_data['tangentLongitude'],
                "surfLatitude": runup_data['surfLatitude'],
                "surfLongitude": runup_data['surfLongitude'],
                "offshoreLatitude": runup_data['offshoreLatitude'],
                "offshoreLongitude": runup_data['offshoreLongitude'],
                "deeplineLatitude": f"{new_lat:.6f}",
                "deeplineLongitude": f"{new_lon:.6f}"
            }
            
            deepline_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "name": f"{runup_data['name']} Deepline {distance}m",
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
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        tangent_lat, tangent_lon = float(runup_data['tangentLatitude']), float(runup_data['tangentLongitude'])
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        json_data['TANGENT'][runup_id] = {}
        point_counter = 0
        for i in range(-points_count // 4, 3 * points_count // 4 + 1):
            distance = i * resolution
            new_lat, new_lon = calculate_new_point(tangent_lat, tangent_lon, bearing, distance)
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} Tangent {distance:.3f} m",
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
            new_key = f"{runup_id}{point_counter:03d}"
            json_data['TANGENT'][runup_id][new_key] = new_point
            point_counter += 1

# NORMAL stations
with open('RUNUP_NAPATREE_STATIONS.json', 'r') as file:
    data_normal = json.load(file)
generate_points_along_line(data_normal)
generate_deepline_points(data_normal)
generate_slopeline_points(data_normal, distance=SLOPELINE_DISTANCE)
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