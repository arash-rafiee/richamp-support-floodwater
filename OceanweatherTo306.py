import numpy as np
import re
import datetime

def parse_oceanweather_header(line):
    """Parse Oceanweather header line and extract parameters."""
    params = {}
    # Extract parameters using regex, accounting for spaces
    patterns = {
        'iLat': r'iLat\s*=\s*(\d+)',
        'iLong': r'iLong\s*=\s*(\d+)',
        'DX': r'DX\s*=\s*([\d.]+)',
        'DY': r'DY\s*=\s*([\d.]+)',
        'SWLat': r'SWLat\s*=\s*([-\d.]+)',
        'SWLon': r'SWLon\s*=\s*([-\d.]+)',
        'DT': r'DT\s*=\s*(\d+)'
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, line)
        if match:
            params[key] = float(match.group(1))
            
    return params

def read_oceanweather(file_path, is_wind_file):
    """
    Read Oceanweather file and return data blocks.
    
    Args:
        file_path (str): Path to Oceanweather file
        is_wind_file (bool): True if the file is a wind file, False if it's a pressure file
        
    Returns:
        dict: Dictionary containing data blocks for each time step
        dict: Header parameters
    """
    data_blocks = {}
    current_params = None
    current_data_u = []
    current_data_v = []
    current_data = []
    reading_u = True
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
        
    # Skip first line (format and time range)
    for line in lines[1:]:
        line = line.strip()
        if line.startswith('iLat='):
            # New time step
            if current_params:
                # Store previous block
                expected_values = int(current_params['iLat']) * int(current_params['iLong'])
                if len(current_data_u) == expected_values and len(current_data_v) == expected_values:
                    data_blocks[current_params['DT']] = np.dstack((np.array(current_data_u).reshape(int(current_params['iLat']), int(current_params['iLong'])), 
                                                                   np.array(current_data_v).reshape(int(current_params['iLat']), int(current_params['iLong']))))
                elif len(current_data) == expected_values:
                    data_blocks[current_params['DT']] = np.array(current_data).reshape(int(current_params['iLat']), int(current_params['iLong']), 1)
                current_data_u = []
                current_data_v = []
                current_data = []
                reading_u = True
                
            current_params = parse_oceanweather_header(line)
        else:
            # Data line
            if is_wind_file:
                if reading_u:
                    current_data_u.extend([float(x) for x in line.split()])
                    if len(current_data_u) == int(current_params['iLat']) * int(current_params['iLong']):
                        reading_u = False
                else:
                    current_data_v.extend([float(x) for x in line.split()])
            else:
                current_data.extend([float(x) for x in line.split()])
    
    # Store last block
    if current_params:
        expected_values = int(current_params['iLat']) * int(current_params['iLong'])
        if is_wind_file:
            if len(current_data_u) == expected_values and len(current_data_v) == expected_values:
                data_blocks[current_params['DT']] = np.dstack((np.array(current_data_u).reshape(int(current_params['iLat']), int(current_params['iLong'])), 
                                                               np.array(current_data_v).reshape(int(current_params['iLat']), int(current_params['iLong']))))
        else:
            if len(current_data) == expected_values:
                data_blocks[current_params['DT']] = np.array(current_data).reshape(int(current_params['iLat']), int(current_params['iLong']), 1)
            
    return data_blocks, current_params

def convert_to_306(wind, pressure, output):
    """
    Convert Oceanweather files to 306 format.
    
    Args:
        wind (str): Path to wind file
        pressure (str): Path to pressure file
        output (str): Path to output 306 file
    """
    # Read wind file (u and v components)
    wind_data, wind_params = read_oceanweather(wind, True)

    # Read pressure file
    pressure_data, pressure_params = read_oceanweather(pressure, False)

    # Verify that wind and pressure files have matching parameters
    if wind_params != pressure_params:
        raise ValueError("Wind and pressure files have different grid parameters")

    # Calculate descriptive line parameters
    iLat = wind_params['iLat']
    iLong = wind_params['iLong']
    dx = wind_params['DX']
    dy = wind_params['DY']
    sw_lat = wind_params['SWLat']
    sw_lon = wind_params['SWLon']

    # Calculate max lat and min lon
    lat_max = sw_lat + (iLat - 1) * dy
    lon_min = sw_lon
    lon_max = sw_lon + (iLong - 1) * dx
    lat_min = sw_lat

    # Assume 1 hour time step (3600 seconds)
    delta_time = 3600.0

    # Write metadata file 
    with open(output + ".meta", 'w') as f:
        f.write(f"{int(iLat):4d} {int(iLong):4d} {lat_max:5.1f} {lon_min:6.1f} "
                f"{dx:8.6f} {dy:8.6f} {delta_time:6.1f}\n")

    # Write OCI file from top left to bottom right
    with open(output, 'w') as f:
        for time_step in sorted(wind_data.keys()):
            if time_step not in pressure_data:
                continue

            wind_block = wind_data[time_step]
            pressure_block = pressure_data[time_step]

            # Convert pressure from hPa to Pa (multiply by 100)
            pressure_block *= 100

            # Write data from top left to bottom right
            for i in range(int(iLat)):
                for j in range(int(iLong)):
                    # Since Oceanweather data is stored with the first index as latitude
                    # and the second as longitude, we need to reverse the i index to start from the top
                    wind_u = wind_block[int(iLat) - i - 1, j, 0]
                    wind_v = wind_block[int(iLat) - i - 1, j, 1]
                    pressure = pressure_block[int(iLat) - i - 1, j, 0]

                    f.write(f"{wind_u:6.1f} {wind_v:6.1f} {pressure:6.0f}\n")

    # Write additional Wind_Inp.txt file
    with open(output + "Wind_Inp.txt", "w") as f:
        f.write("richamp\n")  # Storm name
        f.write("3\n")  # Not sure what this is
        minTrackTime = datetime.datetime.strptime(str(int(list(wind_data.keys())[0])), "%Y%m%d%H%M")
        f.write(f"{minTrackTime.year:04d} {minTrackTime.month:02d} {minTrackTime.day:02d} {minTrackTime.hour:02d} {minTrackTime.minute:02d} {minTrackTime.second:02d}\n")
        f.write("1.0\n")  # Not sure what this is
        f.write(f"{len(wind_data)}\n")  # Number of timesteps
        f.write(f"{lon_min:.1f} {lon_max:.1f}\n")  # MIN_LONGITUDE, MAX_LONGITUDE
        f.write(f"{lat_min:.1f} {lat_max:.1f}\n")  # MIN_LATITUDE, MAX_LATITUDE
        f.write("10.\n")  # (1 / deltaLatitude) if deltaLat deltaLon 0.1, then 10

def main():
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description='Convert Oceanweather files to 306 format')
    parser.add_argument('--wind', help='Path to wind file')
    parser.add_argument('--pressure', help='Path to pressure file')
    parser.add_argument('--output', help='Path to output 306 file')

    args = parser.parse_args()

    try:
        convert_to_306(args.wind, args.pressure, args.output)
        print(f"Successfully converted files to {args.output}")
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()