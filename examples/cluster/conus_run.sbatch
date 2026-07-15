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

START_YEAR=${START_YEAR:-2018}
END_YEAR=${END_YEAR:-2025}
OUTPUT_ROOT=${OUTPUT_ROOT:-data/output/conus}

uv run csb run-all "$START_YEAR" "$END_YEAR" --output "$OUTPUT_ROOT"
