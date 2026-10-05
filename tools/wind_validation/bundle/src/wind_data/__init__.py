"""wind_data: download, process, analyze, and compare 10-m winds from GFS and GDAS.

Subpackages
-----------
download
    Source-specific retrieval and reading (``gfs``, ``gdas``) built on a
    shared implementation. Everything returned is in the common format
    defined in :mod:`wind_data.schema`.
processing
    Source-agnostic wind, time, and coordinate utilities.
analysis
    Statistics and GFS-versus-GDAS comparison.
plotting
    Time-series and map figures.

All times handled by this package are UTC. See README.md for the full list
of conventions (wind-vector sign, direction, longitude range, cycles).
"""

__version__ = "0.1.0"

__all__ = ["__version__", "analysis", "download", "plotting", "processing", "schema"]
