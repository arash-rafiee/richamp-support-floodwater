#!/usr/bin/env python3
"""
Build a quick, NON-directional z0 interpolant pickle for scale_and_subset.py.

scale_and_subset.py normally needs a directional roughness lookup (z0_interp.pickle)
that averages land roughness over upwind cones for 12 wind directions. Generating it
takes many hours in pure Python. This script builds a pickle with the same structure
and grid, but uses each point's own NLCD roughness for every direction, so it takes
seconds. Use it when no proper pickle is available and you need output now; the
scaled wind will be noisier near roughness transitions than with the directional table.

Usage:
    python build_point_z0_interp.py                       # -> z0_interp_point.pickle
    python build_point_z0_interp.py -hr NLCD_z0_RICHAMP_Reg_Grid.nc -o z0_interp_point
Then:
    python scale_and_subset.py ... -z0name z0_interp_point ...   (no -z0sv)
"""
import argparse
import pickle

import netCDF4
import numpy
import scipy.interpolate


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-hr", default="NLCD_z0_RICHAMP_Reg_Grid.nc", help="high-resolution roughness file")
    ap.add_argument("-o", default="z0_interp_point", help="output name (without .pickle)")
    args = ap.parse_args()

    with netCDF4.Dataset(args.hr) as f:
        lon = numpy.array(f.variables["lon"][:], dtype=numpy.float64)
        lat = numpy.array(f.variables["lat"][:], dtype=numpy.float64)
        z0 = numpy.array(f.variables["land_rough"][:], dtype=numpy.float64)

    cone_ctr_angle = numpy.linspace(0, 360, 13)  # same axis as generate_directional_z0_interpolant
    z0_directional = numpy.repeat(z0[:, :, numpy.newaxis], cone_ctr_angle.size, axis=2)
    interpolant = scipy.interpolate.RegularGridInterpolator((lat, lon, cone_ctr_angle), z0_directional, method="linear")

    with open(args.o + ".pickle", "wb") as out:
        pickle.dump(interpolant, out, pickle.HIGHEST_PROTOCOL)
    print(f"Wrote {args.o}.pickle: grid {lat.size} x {lon.size} x {cone_ctr_angle.size}, non-directional z0")


if __name__ == "__main__":
    main()
