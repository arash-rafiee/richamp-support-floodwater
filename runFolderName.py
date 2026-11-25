"""Print the archive folder name of a run from its run.properties.

    GFS cycle      stormtype gfs,  forecastValidStart 20261125060000   ->  GFS_20261125_06Z
    NHC advisory   advisory 3,     forecastValidStart 20260725...      ->  NHC_20260725_Advisory03

A run with an advisory number is an NHC run; otherwise the forcing (stormtype, in capitals) is used.
With an output directory as the second argument, a name already used there gets _V02, _V03, ... so an
earlier run of the same cycle is kept.

Usage: python3 runFolderName.py properties/run.properties [output_dir]
"""
import os
import re
import sys


def readProperties(path):
    values = {}
    with open(path) as f:
        for line in f:
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
    return values


def runName(values):
    stamp = re.sub(r"\D", "", values.get("forecastValidStart", "") or values.get("rawstart", ""))
    day, hour = stamp[:8] or "unknown", stamp[8:10] or "00"
    advisory = values.get("advisory", "")
    if advisory:
        number = int(advisory) if advisory.isdigit() else advisory
        return f"NHC_{day}_Advisory{number:02d}" if isinstance(number, int) else f"NHC_{day}_Advisory{number}"
    forcing = (values.get("stormtype", "") or "RUN").upper()
    return f"{forcing}_{day}_{hour}Z"


def freeName(name, outputDir):
    if not outputDir or not os.path.exists(os.path.join(outputDir, name)):
        return name
    version = 2
    while os.path.exists(os.path.join(outputDir, f"{name}_V{version:02d}")):
        version += 1
    return f"{name}_V{version:02d}"


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    print(freeName(runName(readProperties(sys.argv[1])), sys.argv[2] if len(sys.argv) == 3 else None))
