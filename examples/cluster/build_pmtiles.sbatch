#!/bin/bash
# Build the CONUS PMTiles archive from the national CSB GeoParquet.
# Stages the FGB intermediate to $TMPDIR (NVMe scratch) for fast I/O.
# Requires `tippecanoe` on PATH (e.g. `conda install -c conda-forge tippecanoe`).

#SBATCH --job-name=csb-pmtiles
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=400G
#SBATCH --time=06:00:00
#SBATCH --output=logs/pmtiles_%j.out
#SBATCH --error=logs/pmtiles_%j.err

set -euo pipefail
mkdir -p logs
cd "$SLURM_SUBMIT_DIR"

INPUT_PARQUET=${INPUT_PARQUET:-data/output/conus/postprocess/2018_2025/national/CSB1825.parquet}
FINAL_PMTILES=${FINAL_PMTILES:-data/output/conus/postprocess/2018_2025/CSB1825.pmtiles}

uv run csb pmtiles \
    --input "$INPUT_PARQUET" \
    --output "$FINAL_PMTILES" \
    --workdir "$TMPDIR"
