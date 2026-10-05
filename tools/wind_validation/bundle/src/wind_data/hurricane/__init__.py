"""Evaluate GFS and GDAS 10-m winds against observations during a hurricane.

Built for Hurricane Melissa (AL132025) and configured by
``config/melissa.toml``; nothing in the code is specific to that storm.

Modules, in pipeline order:

* :mod:`~wind_data.hurricane.config`        TOML settings
* :mod:`~wind_data.hurricane.fetch`         cached, resumable, manifested HTTP
* :mod:`~wind_data.hurricane.besttrack`     NHC HURDAT2/ATCF parsing, interpolation
* :mod:`~wind_data.hurricane.geodesy`       WGS84 geodesics, quadrants, bands
* :mod:`~wind_data.hurricane.stations`      NDBC / CO-OPS / GHCNh discovery + metadata
* :mod:`~wind_data.hurricane.observations`  observation download and parsing
* :mod:`~wind_data.hurricane.qc`            quality control flags
* :mod:`~wind_data.hurricane.height`        neutral log-law height adjustment
* :mod:`~wind_data.hurricane.models`        GFS/GDAS U10/V10/PRMSL download and crop
* :mod:`~wind_data.hurricane.collocate`     storm-relative geometry, model-obs matching
* :mod:`~wind_data.hurricane.evaluate`      verification statistics
* :mod:`~wind_data.hurricane.plots`         figures
* :mod:`~wind_data.hurricane.report`        markdown report
* :mod:`~wind_data.hurricane.pipeline`      stage orchestration (used by the CLI)

Run it with ``python scripts/hurricane_eval.py --help``.
"""
