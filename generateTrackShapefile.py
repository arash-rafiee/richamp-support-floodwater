import argparse
import shapefile
import math
import os

def parse_lat_lon(lat_str, lon_str):
    """Convert latitude and longitude strings (e.g., 298N, 0749W) to decimal degrees."""
    lat = float(lat_str[:-1]) / 10  # Remove N/S and convert to decimal
    lon = float(lon_str[:-1]) / 10  # Remove E/W and convert to decimal
    if lat_str[-1] == 'S':
        lat = -lat
    if lon_str[-1] == 'W':
        lon = -lon
    return lat, lon

def calculate_azimuth(lat1, lon1, lat2, lon2):
    """Calculate the azimuth (bearing) between two lat/lon points in degrees using great circle."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    azimuth = math.degrees(math.atan2(y, x))
    return (azimuth + 360) % 360  # Normalize to 0-360 degrees

def write_prj_file(output_shp):
    """Write a .prj file for WGS84 geographic coordinates."""
    wgs84_prj = '''GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'''
    prj_file = output_shp.replace('.shp', '.prj') if output_shp.endswith('.shp') else output_shp + '.prj'
    with open(prj_file, 'w') as f:
        f.write(wgs84_prj)
    print(f".prj file written to {prj_file}")

def process_track_file(input_file, output_shp):
    """Process the track file and generate a shapefile with .prj."""
    # Read the track file
    with open(input_file, 'r') as f:
        lines = f.readlines()

    # Parse lat/lon data
    coordinates = []
    for line in lines:
        parts = line.split()
        if len(parts) < 6:
            continue  # Skip malformed lines
        lat_str, lon_str = parts[4], parts[5]  # e.g., 298N, 0749W
        lat, lon = parse_lat_lon(lat_str, lon_str)
        coordinates.append((lat, lon))

    # Calculate azimuths (headings) for each segment
    azimuths = []
    for i in range(len(coordinates) - 1):
        lat1, lon1 = coordinates[i]
        lat2, lon2 = coordinates[i + 1]
        az = calculate_azimuth(lat1, lon1, lat2, lon2)
        azimuths.append(az)
    # Last point gets the same azimuth as the second-to-last
    if azimuths:
        azimuths.append(azimuths[-1])
    else:
        azimuths.append(0)  # Default if only one point

    # Prepare polyline coordinates (lon, lat order for shapefile)
    polyline = [(lon, lat) for lat, lon in coordinates]

    # Create shapefile
    w = shapefile.Writer(output_shp, shapeType=shapefile.POLYLINE)
    w.field('Name', 'C', 50)  # Add a Name attribute
    w.field('Azimuth', 'F', decimal=2)  # Add Azimuth attribute as float

    # Write the polyline
    w.line([polyline])  # Single polyline with all points
    w.record('TrackLine', azimuths[0] if azimuths else 0)  # Record with Name and first azimuth

    # Close the shapefile
    w.close()

    # Write the .prj file
    write_prj_file(output_shp)

    print(f"Shapefile written to {output_shp}")

def main():
    # Command-line argument parser
    parser = argparse.ArgumentParser(description="Convert a storm track file to a shapefile.")
    parser.add_argument("input_file", help="Path to the input track file")
    parser.add_argument("output_shp", help="Path to the output shapefile (e.g., Track.shp)")
    args = parser.parse_args()

    # Process the file
    process_track_file(args.input_file, args.output_shp)

if __name__ == "__main__":
    main()