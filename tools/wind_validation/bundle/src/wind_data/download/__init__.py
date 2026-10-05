"""Source-specific download and reading modules.

Usage::

    from wind_data.download import gfs, gdas

Each module exposes the same small public API (``download``, ``get_wind``)
and returns data in the common format defined in :mod:`wind_data.schema`.
Shared logic lives in :mod:`wind_data.download.common`; ``gfs`` and ``gdas``
only supply source-specific parameters.

Submodules are imported lazily by Python's ``from package import module``
machinery, so importing this package does not import cfgrib or requests.
"""
