#!/bin/bash
# Validate parity vs USDA CSB ground truth across 16 CONUS test tiles.
# Requires `csb parity-prep` to have produced the indexed parquets first.

#SBATCH --job-name=csb-parity
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=02:00:00
#SBATCH --output=logs/conus_parity_%j.out
#SBATCH --error=logs/conus_parity_%j.err

set -euo pipefail
mkdir -p logs
cd "$SLURM_SUBMIT_DIR"

OURS=${OURS:-data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet}
USDA=${USDA:-data/CSB1825_indexed.parquet}
REPORT=${REPORT:-data/profile/conus_parity.json}

uv run csb parity --ours "$OURS" --usda "$USDA" --report "$REPORT"
