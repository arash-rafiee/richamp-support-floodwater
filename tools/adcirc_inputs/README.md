# Making a fort.20 (river inflow) for ADCIRC

`make_fort20.sh` builds an ADCIRC `fort.20` from USGS 15-minute gauge discharge.
It runs `make_fort20.py` in the `floodwater` conda environment on Unity.

## Quick start

1. Go to the **hot-start** run folder. It must hold `fort.14` and `fort.15`.

   ```bash
   cd /path/to/hotstart_run
   ```

2. If a `fort.20` is already there and you want to keep it, rename it.

   ```bash
   mv fort.20 fort.20.old
   ```

3. Run the script on the login node and write the output into this folder.

   ```bash
   OUT_DIR="$PWD" bash $pi_work_hashemi/projects/richamp-support-floodwater/tools/adcirc_inputs/make_fort20.sh ./
   ```

4. Check the result (see [Check the result](#check-the-result)).

The script writes two files:

- `fort.20`: the flux file ADCIRC reads.
- `fort_discharge.csv`: the gauge discharge per river in m³/s, for checking.

Without `OUT_DIR`, both go to `tools/adcirc_inputs/output/<run folder name>/` in the
repository and nothing in the run folder is touched.

## Where the dates come from

The script reads them from `fort.15`:

- start = `base_date` (the date line in the block after `ITITER`)
- end = `base_date + RNDAY`

Use the hot-start `fort.15`. Its `RNDAY` covers the cold and hot start together, so
one `fort.20` serves both runs. A cold-start `fort.15` gives a file that ends too early.

To set the dates yourself (UTC), put `START` and/or `END` in front of the command:

```bash
END="2022-12-30 00:00" OUT_DIR="$PWD" bash $pi_work_hashemi/projects/richamp-support-floodwater/tools/adcirc_inputs/make_fort20.sh ./
```

`END` only changes the length of the `fort.20`. It does not change `RNDAY` in
`fort.15`, and the `fort.20` must be at least as long as the run.

## Options

Set these as environment variables in front of the command.

| Variable | Purpose | Default |
|---|---|---|
| `OUT_DIR` | Where `fort.20` and `fort_discharge.csv` go | `tools/adcirc_inputs/output/<run folder name>` |
| `START`, `END` | UTC dates instead of the `fort.15` ones, e.g. `"2022-12-01 00:00"` | from `fort.15` |
| `FORT14`, `FORT15` | Use these files instead of the ones in the run folder | `RUN_DIR/fort.14`, `RUN_DIR/fort.15` |
| `CACHE_DIR` | Raw USGS downloads, reused across runs | `tools/adcirc_inputs/usgs_cache` |
| `DT` | Record spacing in seconds | `900` |
| `WIDTH` | `length`, or `tributary` to reproduce the old 2018 `fort.20` | `length` |
| `OFFLINE=1` | Use only files already in the cache | off |
| `CONDA_ROOT` | Miniconda install holding the `floodwater` env | group miniconda |
| `REPO` | Repository root, if `sbatch` is not submitted from it | submit directory |

## Running as a Slurm job

```bash
cd $pi_work_hashemi/projects/richamp-support-floodwater
sbatch tools/adcirc_inputs/make_fort20.sh /path/to/hotstart_run
```

Submit from the repository root, or set `REPO`. The log is `make_fort20_<jobid>.out`
in the submit directory.

The script downloads from USGS. If the compute nodes have no internet, run it once
with `bash` on the login node to fill the cache, then add `OFFLINE=1` to later
`sbatch` runs with the same dates.

## Check the result

- **Last line of the log.** It gives the `base_date`, `NFFR` and largest `RNDAY` the
  file supports, e.g. `fort.15: base_date must be 2022-12-01 00:00:00, NFFR 0, RNDAY <= 29.0208`.
- **Warnings in the log.** Look for `WARNING longest gap` (a gauge gap filled
  linearly), `records outside the data (held constant)` and `DAILY MEAN` (no
  15-minute data for that gauge).
- **`fort_discharge.csv`.** Plot or open it and look for straight-line stretches
  (filled gaps) and one-off spikes (gauge artefacts).

## Requirements in fort.15

- `NFFR` must be 0 (or -1), so ADCIRC reads `fort.20` as non-periodic flux.
- `STATIM` must be 0. Record 0 of `fort.20` is the cold-start time.
- If there is no `base_date` line, set `START` and `END` yourself.

## Common errors

| Message | What to do |
|---|---|
| `fort.14 not found` | Pass the folder that holds `fort.14`, or set `FORT14`. |
| `is not the richamp-support-floodwater root` | Submit `sbatch` from the repository root, or set `REPO`. |
| `no start date` / `no end date` | `fort.15` has no `base_date` or `RNDAY`; set `START` / `END`. |
| `STATIM = ...` | Set `START` to the cold-start time. |
| `flux boundaries without a river entry` | The mesh has a river boundary that is not in the built-in table. See below. |
| `--offline but not cached` | Run once without `OFFLINE=1` on a node with internet, with the same dates. |
| `download failed` | USGS is unreachable; retry later or from the login node. |

## A mesh with different river boundaries

The rivers and their USGS gauges are in the `RIVERS` table at the top of
`make_fort20.py`. A boundary is matched to the river within 2 km of its midpoint.
To see the flux boundaries in a mesh and how they match:

```bash
python tools/adcirc_inputs/make_fort20.py --fort14 /path/to/fort.14 --list
```

For a boundary with `NO MATCH`, add an entry to `RIVERS` with its name, midpoint
`lon`/`lat`, the USGS gauge numbers to sum, and a `scale` factor.
