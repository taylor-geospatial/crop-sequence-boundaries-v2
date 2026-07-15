#!/bin/bash
# Hilbert-sort + add bbox columns to enable DuckDB row-group pruning during
# parity validation. Run once after `csb postprocess` finishes.

#SBATCH --job-name=csb-parity-prep
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=200G
#SBATCH --time=01:00:00
#SBATCH --output=logs/parity_prep_%j.out
#SBATCH --error=logs/parity_prep_%j.err

set -euo pipefail
mkdir -p logs
cd "$SLURM_SUBMIT_DIR"

OURS=${OURS:-data/output/conus/postprocess/2018_2025/national/CSB1825.parquet}
OURS_OUT=${OURS_OUT:-data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet}
USDA_GDB=${USDA_GDB:-data/CSB1825.gdb}
USDA_OUT=${USDA_OUT:-data/CSB1825_indexed.parquet}

uv run csb parity-prep \
    --ours "$OURS" \
    --ours-out "$OURS_OUT" \
    --usda-gdb "$USDA_GDB" \
    --usda-out "$USDA_OUT"
