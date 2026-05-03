# SLURM submission scripts

Reference SBATCH wrappers for running the CSB pipeline on an HPC SLURM
cluster. Each script defaults to a single fat node and is sized for the
CONUS dataset (8-year sequence, ~15 M output polygons).

| Script | Purpose | Typical wall |
|---|---|---|
| `conus_run.sbatch` | Full pipeline (`polygonize` + `postprocess`) | ~25 min |
| `prep_parity_inputs.sbatch` | One-time index of ours + USDA parquets | ~2 min |
| `conus_parity.sbatch` | 16-region IoU validation vs USDA ground truth | ~4 min |
| `build_pmtiles.sbatch` | Build CONUS PMTiles archive | ~17 min |

## Adapting to your site

Each script has placeholders to update:

```bash
#SBATCH --account=YOUR_ACCOUNT       # your SLURM allocation
#SBATCH --partition=cpu              # may be cpu / standard / compute
```

Override paths via env vars rather than editing the scripts in place:

```bash
START_YEAR=2017 END_YEAR=2024 sbatch examples/cluster/conus_run.sbatch
```

`build_pmtiles.sbatch` requires `tippecanoe` on PATH. Install via:

```bash
conda install -c conda-forge tippecanoe
# or build from https://github.com/felt/tippecanoe
```
