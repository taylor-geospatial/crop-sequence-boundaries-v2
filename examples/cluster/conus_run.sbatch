#!/bin/bash
# Build the full CONUS Crop Sequence Boundaries dataset on a single fat node.
# Adjust --account / --partition for your SLURM site.

#SBATCH --job-name=csb-conus
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --time=04:00:00
#SBATCH --output=logs/conus_%j.out
#SBATCH --error=logs/conus_%j.err

set -euo pipefail
mkdir -p logs
cd "$SLURM_SUBMIT_DIR"

CONFIG=${CONFIG:-configs/conus.yaml}
START_YEAR=${START_YEAR:-2018}
END_YEAR=${END_YEAR:-2025}

uv run csb --config "$CONFIG" polygonize "$START_YEAR" "$END_YEAR"
uv run csb --config "$CONFIG" postprocess "$START_YEAR" "$END_YEAR" \
    --polygonize-dir "data/output/conus/polygonize/${START_YEAR}_${END_YEAR}"
