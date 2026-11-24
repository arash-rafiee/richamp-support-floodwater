# Wind slides

Station wind figures and deck slides for the RICHAMP event decks, one per RI NOAA station
(Providence, Conimicut Light, Quonset Point, Newport).

Each figure has the water-graph heading (`Station — Station ID`, `GFS | window UTC`) and:

- **Speed** (m/s), hourly line
- **Wind from**, hourly dots on an N–E–S–W–N axis (dots, so crossing north draws no false jump)
- **Barbs**, one column per day and rows 00Z, 06Z, 12Z, 18Z; half feather 5 m/s, full feather
  10 m/s, flag 50 m/s; the staff points to where the wind comes from; a circle is calm (< 2.5 m/s)
- **Wind rose** for the whole window (% of hours, coloured by speed) and a barb key

It is drawn at the deck's picture-box size (11.2 × 5.5 in), so the text is the size it shows on the slide.

## Steps

1. Wind time series per station, as a CSV (`station, time, fhr, u10, v10, speed_ms, dir_from_deg`):
   - from a RICHAMP wind netCDF (netCDF4 + numpy only):
     `python netcdf_wind_series.py RICHAMP_wind.nc wind.csv`
   - or from GFS on NOMADS (needs requests and cfgrib; NOMADS keeps ~10 days):
     `python gfs_wind_series.py 20261124 12 <out_dir>`
2. One figure: `python wind_figure.py wind.csv "Newport, RI" newport_wind.png`
3. Four slides: `python make_wind_slides.py wind.csv "GFS Nov 24 12Z" wind_slides.pptx`
   (uses the `richamp-event-presentation` skill in `.claude/skills/`, which is on this PC only)

`wind_figure.py` uses pandas; a version inside Grapher.py for the Floodwater graphs would use numpy only.
