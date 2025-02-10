import json
import math

def calculate_bearing(lat1, lon1, lat2, lon2):
    # Convert latitude and longitude to radians
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    
    # Calculate the bearing from point 1 to point 2
    dLon = lon2_rad - lon1_rad
    y = math.sin(dLon) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dLon)
    bearing = math.atan2(y, x)
    
    return bearing

def calculate_new_point(lat, lon, bearing, distance):
    R = 6371e3  # Earth's radius in meters
    lat_rad, lon_rad = math.radians(lat), math.radians(lon)
    
    # Convert bearing to radians
    bearing_rad = bearing
    
    # New latitude
    new_lat = math.asin(math.sin(lat_rad) * math.cos(distance / R) + 
                        math.cos(lat_rad) * math.sin(distance / R) * math.cos(bearing_rad))
    
    # New longitude
    new_lon = lon_rad + math.atan2(math.sin(bearing_rad) * math.sin(distance / R) * math.cos(lat_rad),
                                   math.cos(distance / R) - math.sin(lat_rad) * math.sin(new_lat))
    
    return math.degrees(new_lat), math.degrees(new_lon)

def generate_points_along_line(json_data, resolution=1, points_count=20):
    point_counter = 0  # Counter for unique keys
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        for i in range(-points_count // 2, points_count // 2 + 1):
            distance = i * resolution  # Fixed step size as given by resolution
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
    
            # Create new point entry
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} {distance:.1f} m",  # Use scientific notation for distance
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
    
            # Add to each relevant section with a new key based on sequential numbering
            new_key = f"{runup_id}{point_counter:03d}"  # Unique key using counter
            for section in ['ASSET', 'NDBC', 'NOS']:
                json_data[section][new_key] = new_point
            point_counter += 1  # Increment counter for next iteration
            
def offshore_generate_points_along_line(json_data, resolution=1000, points_count=10):
    point_counter = 0  # Counter for unique keys
    for runup_id, runup_data in json_data['RUNUP'].items():
        shoreline_lat, shoreline_lon = float(runup_data['latitude']), float(runup_data['longitude'])
        surf_lat, surf_lon = float(runup_data['surfLatitude']), float(runup_data['surfLongitude'])
        
        bearing = calculate_bearing(shoreline_lat, shoreline_lon, surf_lat, surf_lon)
        
        for i in range(points_count):
            distance = i * resolution  # Fixed step size as given by resolution
            new_lat, new_lon = calculate_new_point(shoreline_lat, shoreline_lon, bearing, distance)
    
            # Create new point entry
            new_point = {
                "id": "RUNUP",
                "source": "RUNUP",
                "distance": str(distance),
                "name": f"{runup_data['name']} {distance:.1f} m",  # Use scientific notation for distance
                "latitude": f"{new_lat:.6f}",
                "longitude": f"{new_lon:.6f}"
            }
    
            # Add to each relevant section with a new key based on sequential numbering
            new_key = f"{runup_id}{point_counter:03d}"  # Unique key using counter
            for section in ['ASSET', 'NDBC', 'NOS']:
                json_data[section][new_key] = new_point
            point_counter += 1  # Increment counter for next iteration

# Load JSON data
with open('RUNUP_OBS_STATIONS.json', 'r') as file:
    data = json.load(file)


generate_points_along_line(data)

# Write modified JSON back to file
with open('RUNUP_NORMAL_STATIONS.json', 'w') as file:
    json.dump(data, file, indent=2)

with open('RUNUP_OBS_STATIONS.json', 'r') as file:
    data = json.load(file)
        # Generate points and modify JSON
offshore_generate_points_along_line(data)

# Write modified JSON back to file
with open('RUNUP_OFFSHORE_STATIONS.json', 'w') as file:
    json.dump(data, file, indent=2)