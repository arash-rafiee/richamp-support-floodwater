# Station wind figures

Station wind figures, one per RI NOAA station
(Providence, Conimicut Light, Quonset Point, Newport).

Each figure has the water-graph heading (`Station — Station ID`, `GFS | window UTC`) and:

- **Speed** (m/s), hourly line
- **Wind from**, hourly dots on an N–E–S–W–N axis (dots, so crossing north draws no false jump)
- **Barbs**, one column per day and rows 00Z, 06Z, 12Z, 18Z; half feather 5 m/s, full feather
  10 m/s, flag 50 m/s; the staff points to where the wind comes from; a circle is calm (< 2.5 m/s)
- **Wind rose** for the whole window (% of hours, coloured by speed) and a barb key

The figure is 11.2 × 5.5 in.

## Steps

1. Wind time series per station, as a CSV (`station, time, fhr, u10, v10, speed_ms, dir_from_deg`):
   - from a RICHAMP wind netCDF (netCDF4 + numpy only):
     `python netcdf_wind_series.py RICHAMP_wind.nc wind.csv`
   - or from GFS on NOMADS (needs requests and cfgrib; NOMADS keeps ~10 days):
     `python gfs_wind_series.py 20261124 12 <out_dir>`
2. One figure: `python wind_figure.py wind.csv "Newport, RI" newport_wind.png`

`wind_figure.py` uses pandas; a version inside Grapher.py for the Floodwater graphs would use numpy only.
